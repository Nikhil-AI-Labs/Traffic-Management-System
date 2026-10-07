// Thin client for the FastAPI backend (src/api.py). Every path is relative to
// /api, which the Vite dev server proxies and FastAPI serves in production.

import { useQuery, type UseQueryOptions } from '@tanstack/react-query'
import { useTheme } from './theme'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response
  try {
    r = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, 'The analysis server is not reachable. Start it with `python src/api.py`.')
  }
  if (!r.ok) {
    let msg = r.statusText || `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (typeof j.detail === 'string') msg = j.detail
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg?: string }) => d.msg).join('; ')
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

export const get = <T>(path: string) => request<T>(path)

export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })

export const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

/** Multipart upload with progress, which fetch() cannot report. */
export function uploadFile<T>(file: File, onProgress: (frac: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/uploads')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      let body: { detail?: string } = {}
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T)
      else reject(new ApiError(xhr.status, body.detail || `Upload failed (HTTP ${xhr.status})`))
    }
    xhr.onerror = () => reject(new ApiError(0, 'Upload failed — the server is not reachable.'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

/**
 * GET a payload whose charts are built for the current theme. The theme is
 * part of both the URL and the cache key, so switching theme refetches the
 * figures in the other palette rather than recolouring them client-side.
 */
export function useThemedApi<T>(
  path: string,
  params: Record<string, string | number | boolean | null | undefined> = {},
  options: Partial<UseQueryOptions<T, ApiError>> = {},
) {
  const { resolved } = useTheme()
  const url = path + qs({ ...params, theme: resolved })
  return useQuery<T, ApiError>({ queryKey: [url], queryFn: () => get<T>(url), ...options })
}

export function useApi<T>(path: string | null, options: Partial<UseQueryOptions<T, ApiError>> = {}) {
  return useQuery<T, ApiError>({
    queryKey: [path],
    queryFn: () => get<T>(path as string),
    enabled: path !== null,
    ...options,
  })
}

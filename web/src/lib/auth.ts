// Operator sign-in. The server decides whether it is needed at all
// (STI_OPERATOR_TOKEN, see src/security.py); when it is, write actions carry
// the token and everything that only reads works without it.
//
// The token lives in sessionStorage, so it goes when the tab closes - a wall
// screen left signed in overnight is the failure this avoids. The operator's
// name is only a label for the incident log, so it is remembered.

import { useSyncExternalStore } from 'react'

const TOKEN = 'sti-operator-token'
const NAME = 'sti-operator-name'
const CHANGED = 'sti:auth-changed'
export const AUTH_REQUIRED = 'sti:auth-required'

function read(store: () => Storage, key: string): string {
  try {
    return store().getItem(key) ?? ''
  } catch {
    return ''
  }
}

function write(store: () => Storage, key: string, value: string | null) {
  try {
    if (value) store().setItem(key, value)
    else store().removeItem(key)
  } catch {
    /* private mode: the session simply is not remembered */
  }
}

export const getToken = () => read(() => sessionStorage, TOKEN)
export const getName = () => read(() => localStorage, NAME)

export function signIn(token: string, name: string) {
  write(() => sessionStorage, TOKEN, token)
  write(() => localStorage, NAME, name.trim())
  window.dispatchEvent(new Event(CHANGED))
}

export function signOut() {
  write(() => sessionStorage, TOKEN, null)
  window.dispatchEvent(new Event(CHANGED))
}

/** Headers for a request: the token when signed in, and the operator's name
 *  (percent-encoded - header values must be Latin-1). */
export function authHeaders(token = getToken(), name = getName()): Record<string, string> {
  const h: Record<string, string> = {}
  if (token) h.Authorization = `Bearer ${token}`
  if (name) h['X-Operator-Name'] = encodeURIComponent(name)
  return h
}

export const requestSignIn = () => window.dispatchEvent(new Event(AUTH_REQUIRED))

function subscribe(cb: () => void) {
  window.addEventListener(CHANGED, cb)
  window.addEventListener('storage', cb)
  return () => {
    window.removeEventListener(CHANGED, cb)
    window.removeEventListener('storage', cb)
  }
}

export function useOperator(): { token: string; name: string; signedIn: boolean } {
  const token = useSyncExternalStore(subscribe, getToken)
  const name = useSyncExternalStore(subscribe, getName)
  return { token, name, signedIn: !!token }
}

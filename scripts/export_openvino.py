"""Export the detector to OpenVINO, for faster live detection on Intel CPUs.

    pip install openvino
    python scripts/export_openvino.py            # export, then benchmark
    python scripts/export_openvino.py --bench    # benchmark an existing export

Writes models/weights/YOLOv11-S/UVH-26-MV-YOLOv11-S_openvino_model/, which
the live monitor (services.get_live_detector) picks up on its next start.
Batch analysis, re-measuring and uploads keep the PyTorch weights: those
produced every committed figure, and an exported graph is not guaranteed to
agree with them to the last box.

The benchmark runs both models over the same frames of a dataset clip and
reports frames per second and how closely their detection counts agree, so
the speed-up is measured on this machine rather than quoted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg  # noqa: E402
import services as S  # noqa: E402


def export() -> Path:
    from ultralytics import YOLO
    t0 = time.time()
    # A fixed 16:9 input: the 1080p frames letterbox to exactly this, which is
    # also what the PyTorch model sees. A dynamic-shape export ran at a
    # quarter of PyTorch's speed on this CPU, and a square one would spend
    # three-quarters more compute on padding.
    w = cfg.DETECT_IMGSZ
    h = -(-int(w * 9 / 16) // 32) * 32          # 960 -> 544, stride-aligned
    out = YOLO(str(cfg.DEFAULT_MODEL)).export(format="openvino", imgsz=[h, w],
                                              dynamic=False)
    print(f"exported {h}x{w} -> {out}  ({time.time() - t0:.0f} s)")
    return Path(out)


def bench(frames: int, camera: str) -> None:
    from detector import VehicleDetector

    cap = cfg.open_video(cfg.camera_video_path(camera))
    clip = []
    while len(clip) < frames:
        ok, f = cap.read()
        if not ok:
            break
        for _ in range(3):               # every 4th frame, as live detection would
            cap.grab()
        clip.append(f)
    cap.release()
    if not clip:
        sys.exit(f"could not read {camera}'s footage")

    runs = [("PyTorch", cfg.DEFAULT_MODEL, cfg.DETECT_IMGSZ, None),
            ("OpenVINO", S.OPENVINO_MODEL, S.openvino_imgsz(), "cpu")]
    try:                      # an Intel integrated GPU, when OpenVINO sees one
        import openvino as ov
        if any(d.startswith("GPU") for d in ov.Core().available_devices):
            runs.append(("OpenVINO-GPU", S.OPENVINO_MODEL, S.openvino_imgsz(), "intel:gpu"))
    except Exception:
        pass
    results = {}
    for name, path, imgsz, device in runs:
        try:
            det = VehicleDetector(model_path=path, imgsz=imgsz, device=device)
            det.track_frame(clip[0])     # warm-up: graph compile, first allocation
            det.reset_tracker()
            t0 = time.perf_counter()
            counts = [len(det.track_frame(f)) for f in clip]
            dt = time.perf_counter() - t0
        except Exception as e:           # e.g. no usable GPU driver
            print(f"  {name:<12} unavailable: {type(e).__name__}: {str(e)[:120]}")
            continue
        results[name] = counts
        print(f"  {name:<12} {len(clip) / dt:5.2f} fps   "
              f"{sum(counts) / len(counts):5.1f} detections/frame")
        del det
    a = results["PyTorch"]
    for name in results:
        if name == "PyTorch":
            continue
        b = results[name]
        diff = sum(abs(x - y) for x, y in zip(a, b)) / max(1, sum(a))
        print(f"  {name}: detection counts differ by {diff * 100:.1f}% of the PyTorch total")


def main():
    ap = argparse.ArgumentParser(description="Export the detector to OpenVINO.")
    ap.add_argument("--bench", action="store_true", help="benchmark only, do not export")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--camera", default="rokadiya_hanuman", choices=list(cfg.CAMERAS))
    a = ap.parse_args()
    try:
        import openvino  # noqa: F401
    except ImportError:
        sys.exit("OpenVINO is not installed. Run: pip install openvino")
    if not Path(cfg.DEFAULT_MODEL).exists():
        sys.exit("Detector weights missing. Run: python scripts/download_models.py")
    if not a.bench:
        export()
    if not (S.OPENVINO_MODEL / "metadata.yaml").exists():
        sys.exit(f"No export at {S.OPENVINO_MODEL}")
    print(f"Benchmark on {a.frames} frames of {cfg.camera_name(a.camera)}:")
    bench(a.frames, a.camera)


if __name__ == "__main__":
    main()

"""Fetch the UVH-26 fine-tuned YOLO11 weights.

Trained on 26,646 annotated 1080p frames from Indian Safe-City traffic cameras,
so the domain matches this footage closely and the 14 classes include the
three-wheeler (auto-rickshaw) that COCO-trained weights have no concept of.

    python scripts/download_models.py          # small model (~55 MB)
    python scripts/download_models.py --size x # large model (~342 MB)

Dataset CC-BY-4.0; YOLO weights AGPL-3.0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REPO = "iisc-aim/UVH-26"
REVISION = "v1.0"
FILES = {
    "s": "weights/YOLOv11-S/UVH-26-MV-YOLOv11-S.pt",
    "x": "weights/YOLOv11-X/UVH-26-MV-YOLOv11-X.pt",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=["s", "x", "both"], default="s")
    args = ap.parse_args()

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        sys.exit("huggingface_hub missing. Run: pip install -r requirements.txt")

    sizes = ["s", "x"] if args.size == "both" else [args.size]
    out_dir = ROOT / "models"

    for s in sizes:
        print(f"Downloading YOLOv11-{s.upper()} from {REPO}@{REVISION} ...")
        path = hf_hub_download(
            repo_id=REPO, filename=FILES[s], revision=REVISION,
            local_dir=str(out_dir),
        )
        size_mb = Path(path).stat().st_size / 1e6
        print(f"  -> {path}  ({size_mb:.0f} MB)")

    print("\nVerifying...")
    from ultralytics import YOLO
    m = YOLO(str(out_dir / FILES[sizes[0]]))
    print(f"  {len(m.names)} classes: {', '.join(m.names.values())}")
    print("\nReady. Next: python src/seed_calibration.py")


if __name__ == "__main__":
    main()

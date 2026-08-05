"""Interactive lane / zone calibration.

Click the corners of a lane or zone on the reference frame; press ENTER to
close the polygon and name it. Existing zones for the camera are loaded so you
can add to or redo a calibration incrementally.

Usage:
    python src/calibrate.py rokadiya_hanuman
    python src/calibrate.py chauta_bazaar --reset

Keys:
    left click   add a point
    u            undo last point
    ENTER        finish current polygon (prompts for label in the terminal)
    d            delete the most recently saved zone
    r            reset the current in-progress polygon
    s            save and exit
    q / ESC      quit without saving
"""

from __future__ import annotations

import argparse

import cv2
import numpy as np

import config as cfg
import zones as Z
from reference_frames import REF_DIR

WINDOW = "Calibration - click polygon corners, ENTER to finish, s to save"
DISPLAY_W = 1280  # 1080p downscaled to fit a laptop screen


class Calibrator:
    def __init__(self, camera_id: str, reset: bool = False):
        self.camera_id = camera_id
        cam = cfg.CAMERAS[camera_id]
        self.mode = cam["mode"]

        ref = REF_DIR / f"{camera_id}.jpg"
        if not ref.exists():
            raise FileNotFoundError(
                f"No reference frame at {ref}. Run: python src/reference_frames.py {camera_id}"
            )
        self.frame = cv2.imread(str(ref))
        h, w = self.frame.shape[:2]
        self.frame_size = (w, h)
        self.scale = DISPLAY_W / w
        self.display = cv2.resize(self.frame, None, fx=self.scale, fy=self.scale)

        existing = None if reset else Z.load(camera_id)
        self.calib = existing or Z.CameraCalibration(
            camera_id=camera_id, mode=self.mode, frame_size=(w, h)
        )
        self.current: list[tuple[int, int]] = []

    # -- interaction ------------------------------------------------------

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.current.append((int(x / self.scale), int(y / self.scale)))

    def render(self) -> np.ndarray:
        canvas = self.display.copy()

        # saved zones
        if self.calib.zones:
            full = Z.draw_zones(self.frame, self.calib, alpha=0.25)
            canvas = cv2.resize(full, None, fx=self.scale, fy=self.scale)

        # in-progress polygon
        if self.current:
            pts = [(int(x * self.scale), int(y * self.scale)) for x, y in self.current]
            for i, p in enumerate(pts):
                cv2.circle(canvas, p, 4, (0, 255, 255), -1)
                cv2.putText(canvas, str(i + 1), (p[0] + 6, p[1] - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            if len(pts) > 1:
                cv2.polylines(canvas, [np.array(pts, np.int32)], False, (0, 255, 255), 2)

        banner = [
            f"{self.camera_id}  [{self.mode}]  zones={len(self.calib.zones)}  "
            f"points={len(self.current)}",
            "ENTER finish poly | u undo pt | r reset poly | d del last zone | s save | q quit",
        ]
        for i, line in enumerate(banner):
            cv2.putText(canvas, line, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(canvas, line, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return canvas

    def finish_polygon(self) -> None:
        if len(self.current) < 3:
            print("Need at least 3 points.")
            return

        print("\n--- new zone ---")
        label = input("Label (e.g. 'Lane 1', 'Carriageway'): ").strip() or f"Zone {len(self.calib.zones) + 1}"
        default_kind = "lane" if self.mode == "junction" else "carriageway"
        kind = input(f"Kind [lane/carriageway/pedestrian/parking/platform] "
                     f"({default_kind}): ").strip() or default_kind
        approach = None
        if kind == "lane":
            approach = input("Approach name (blank if single approach): ").strip() or None
        area_s = input("Real area in m^2 if known (blank to skip): ").strip()
        real_area = float(area_s) if area_s else None

        zid = label.lower().replace(" ", "_")
        self.calib.zones.append(Z.Zone(
            id=zid, label=label, polygon=list(self.current),
            kind=kind, approach=approach, real_area_m2=real_area,
        ))
        print(f"Added '{label}' ({kind}) with {len(self.current)} points.")
        self.current = []

    def run(self) -> None:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, self.on_mouse)
        print(__doc__)

        while True:
            cv2.imshow(WINDOW, self.render())
            key = cv2.waitKey(20) & 0xFF

            if key in (13, 10):          # ENTER
                self.finish_polygon()
            elif key == ord("u"):
                if self.current:
                    self.current.pop()
            elif key == ord("r"):
                self.current = []
            elif key == ord("d"):
                if self.calib.zones:
                    gone = self.calib.zones.pop()
                    print(f"Deleted zone '{gone.label}'.")
            elif key == ord("s"):
                Z.save(self.calib)
                print(f"\nSaved {len(self.calib.zones)} zones for "
                      f"'{self.camera_id}' to {cfg.CAMERA_CONFIG}")
                break
            elif key in (ord("q"), 27):
                print("Quit without saving.")
                break

        cv2.destroyAllWindows()


def preview(camera_id: str) -> None:
    """Write a PNG of the saved calibration for visual checking."""
    calib = Z.load(camera_id)
    if calib is None:
        print(f"No calibration saved for '{camera_id}'.")
        return
    frame = cv2.imread(str(REF_DIR / f"{camera_id}.jpg"))
    out = Z.draw_zones(frame, calib)

    lines = [f"{cfg.CAMERAS[camera_id]['name']}  [{calib.mode}]"]
    for z in calib.zones:
        area = Z.zone_real_area(z, calib.scale_model)
        area_s = f"{area:.0f} m2" if area else f"{z.pixel_area / 1000:.0f} kpx"
        lines.append(f"  {z.label} ({z.kind}) - {area_s}")

    y = 40
    for line in lines:
        cv2.putText(out, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
        y += 30

    path = cfg.FRAME_DIR / "calibration" / f"{camera_id}_zones.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"Preview -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--reset", action="store_true", help="discard existing zones")
    ap.add_argument("--preview", action="store_true",
                    help="just render the saved calibration, no editing")
    args = ap.parse_args()

    if args.preview:
        preview(args.camera)
        return

    Calibrator(args.camera, reset=args.reset).run()
    preview(args.camera)


if __name__ == "__main__":
    main()

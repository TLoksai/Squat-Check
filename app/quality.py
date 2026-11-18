"""Video quality gate: is the picture good enough to measure at all?

Checked on 12 frames, inside the box around the lifter:
  - size: how many pixels long the lifter's leg is
  - brightness: average grey level (0-255)
  - sharpness: variance of the Laplacian (a standard blur measure)

Thresholds live in criteria.yaml under `video_quality` (app choices, set from
test clips: the common sample scores leg 336 px / brightness 77 / sharpness
~300; a dark, blurred, 1/3-size copy scores 165 / 26 / 14). Below a "min"
value the video is reported as unsuitable instead of producing numbers that
look precise but aren't.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .pose import SIDES

DEFAULTS = {"min_leg_px": 150, "min_brightness": 35, "min_sharpness": 40, "warn_sharpness": 80}


def measure(video_path: Path, lm: np.ndarray) -> dict:
    n = lm.shape[0]
    vis = np.nan_to_num(lm[:, :, 2])
    side = "left" if vis[:, [23, 25, 27]].sum() >= vis[:, [24, 26, 28]].sum() else "right"
    s = SIDES[side]
    leg = (np.linalg.norm(lm[:, s["hip"], :2] - lm[:, s["knee"], :2], axis=1)
           + np.linalg.norm(lm[:, s["knee"], :2] - lm[:, s["ankle"], :2], axis=1))
    leg_px = float(np.nanmedian(leg)) if np.isfinite(leg).any() else 0.0

    cap = cv2.VideoCapture(str(video_path))
    bright, sharp = [], []
    for t in np.linspace(0, max(n - 1, 0), 12).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t))
        ok, frame = cap.read()
        if not ok:
            continue
        pts = lm[t, :, :2][np.isfinite(lm[t, :, 0])]
        if len(pts) < 5:
            continue
        h, w = frame.shape[:2]
        x0, y0 = np.clip(pts.min(0), 0, [w - 1, h - 1]).astype(int)
        x1, y1 = np.clip(pts.max(0), 0, [w, h]).astype(int)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        g = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        bright.append(float(g.mean()))
        sharp.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
    cap.release()
    return {
        "leg_px": round(leg_px, 1),
        "brightness": round(float(np.median(bright)), 1) if bright else None,
        "sharpness": round(float(np.median(sharp)), 1) if sharp else None,
    }


def problems(q: dict, limits: dict | None) -> tuple[list[str], list[str]]:
    """Return (blocking problems, warnings) in plain words."""
    L = {**DEFAULTS, **(limits or {})}
    block, warn = [], []
    if q.get("leg_px", 0) < L["min_leg_px"]:
        block.append("you are too small in the picture (move the camera closer or record at a higher resolution)")
    if q.get("brightness") is not None and q["brightness"] < L["min_brightness"]:
        block.append("the video is too dark")
    if q.get("sharpness") is not None:
        if q["sharpness"] < L["min_sharpness"]:
            block.append("the video is too blurry")
        elif q["sharpness"] < L["warn_sharpness"]:
            warn.append("The video is a little blurry, so measurements are less precise.")
    return block, warn

"""Barbell tracking from a side view.

From the side, the bar points at the camera, so its end shows up as the round
weight plate. Per frame we:
  1. look for the plate circle (OpenCV Hough circle transform) near where the
     bar was last seen, at the size it had last time;
  2. if motion blur hides the circle edge, match the plate's appearance from
     the last good frame (template matching) instead.
Then we clean the track and fill only short gaps.

Every frame gets a status so the UI and the rules never confuse a measured
bar position with a guess:
    detected      - plate circle found in this frame
    tracked       - plate found by matching its appearance (blurred frames)
    interpolated  - short gap (<= MAX_GAP frames) filled between measurements
    missing       - no reliable position; bar rules treat it as unknown
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .pose import SIDES

MAX_GAP = 6                  # frames we are willing to interpolate (0.2 s at 30 fps)
# Plate radius as a fraction of the lifter's leg length (hip-knee-ankle). A
# 45 cm plate is ~0.26 of an adult leg and looks bigger because it's nearer the
# camera; real clips measured 0.22-0.41. The lower limit stops the head or a
# hand being taken for a plate (a stick-only clip gave a false "plate" at 0.165).
RADIUS_RANGE = (0.19, 0.60)
MAX_STEP = 0.6               # max centre movement between frames, in plate radii
MIN_MATCH = 0.6              # template-match score needed to accept a "tracked" frame
MAX_TRACK_RUN = 15           # appearance-matching may bridge at most 0.5 s of blur
MIN_HOUGH_SHARE = 0.25       # below this share of circle detections: "no plate found"
MEASURED = ("detected", "tracked")


def _leg_length(lm: np.ndarray, side: str) -> float:
    s = SIDES[side]
    hip, knee, ankle = lm[:, s["hip"], :2], lm[:, s["knee"], :2], lm[:, s["ankle"], :2]
    leg = np.linalg.norm(hip - knee, axis=1) + np.linalg.norm(knee - ankle, axis=1)
    leg = leg[np.isfinite(leg)]
    return float(np.median(leg)) if leg.size else 0.0


def _hough(gray: np.ndarray, r_min: int, r_max: int, strict: float) -> np.ndarray:
    blur = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        blur, cv2.HOUGH_GRADIENT_ALT, dp=1.5, minDist=max(r_min, 10),
        param1=150, param2=strict, minRadius=max(r_min, 5), maxRadius=max(r_max, r_min + 2),
    )
    return circles[0] if circles is not None else np.zeros((0, 3), dtype=np.float32)


def _window(shape, cx, cy, half):
    h, w = shape[:2]
    x0, y0 = max(0, int(cx - half)), max(0, int(cy - half))
    x1, y1 = min(w, int(cx + half)), min(h, int(cy + half))
    return x0, y0, x1, y1


def track_bar(video_path: Path, landmarks: np.ndarray, side: str,
              progress: Callable[[float], None] | None = None) -> dict:
    """Two passes: the first finds the plate and its size; the second re-tracks
    with the size locked (the plate's size in pixels barely changes from the
    side), which stops the circle snapping to the plate's inner ring."""
    n = landmarks.shape[0]
    leg = _leg_length(landmarks, side)
    if leg <= 0:
        return _result(np.full((n, 2), np.nan, dtype=np.float32), np.full(n, np.nan, dtype=np.float32),
                       np.array(["missing"] * n, dtype=object))
    half = (lambda f: progress(f / 2)) if progress else None
    first = _track(video_path, landmarks, side, leg, None, half)
    det = first["status"] == "detected"
    if det.sum() < max(10, MIN_HOUGH_SHARE * n):
        # The plate circle itself was rarely seen: whatever was followed is not
        # trustworthy (a stick, a head, a hand). Report no bar instead.
        if progress:
            progress(1.0)
        return _result(np.full((n, 2), np.nan, dtype=np.float32), np.full(n, np.nan, dtype=np.float32),
                       np.array(["missing"] * n, dtype=object))
    r_lock = float(np.nanmedian(first["radius"][det]))
    rest = (lambda f: progress(0.5 + f / 2)) if progress else None
    second = _track(video_path, landmarks, side, leg, r_lock, rest)
    return second if second["detected_ratio"] >= first["detected_ratio"] - 0.05 else first


def _track(video_path: Path, landmarks: np.ndarray, side: str, leg: float, r_lock: float | None,
           progress: Callable[[float], None] | None) -> dict:
    n = landmarks.shape[0]
    centers = np.full((n, 2), np.nan, dtype=np.float32)
    radii = np.full(n, np.nan, dtype=np.float32)
    status = np.array(["missing"] * n, dtype=object)
    if r_lock:
        r_min, r_max = int(r_lock * 0.94), int(r_lock * 1.06) + 1
    else:
        r_min, r_max = int(RADIUS_RANGE[0] * leg), int(RADIUS_RANGE[1] * leg)
    s = SIDES[side]
    cap = cv2.VideoCapture(str(video_path))
    prev = None        # (x, y, r) of the last measured plate
    template = None    # grayscale patch of the last Hough-detected plate
    track_run = 0      # frames in a row found only by appearance matching
    for t in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Where we expect the bar: last position, else the shoulder (even a
        # low-confidence shoulder guess is close: the bar sits on the upper back).
        guess = prev[:2] if prev is not None else landmarks[t, s["shoulder"], :2]
        if not np.all(np.isfinite(guess)):
            continue

        found = None
        if prev is None:
            x0, y0, x1, y1 = _window(frame.shape, guess[0], guess[1], 2.2 * r_max)
            cands = _hough(gray_full[y0:y1, x0:x1], r_min, r_max, 0.80)
            if cands.size:
                cands[:, :2] += (x0, y0)
                d = np.linalg.norm(cands[:, :2] - guess, axis=1)
                i = int(np.argmin(d))
                if d[i] <= 0.6 * leg:
                    found = cands[i]
        else:
            px, py, pr = prev
            x0, y0, x1, y1 = _window(frame.shape, px, py, pr * (1 + MAX_STEP) + 4)
            lo_r, hi_r = (r_min, r_max) if r_lock else (int(pr * 0.85), int(pr * 1.15) + 1)
            for strict in (0.80, 0.65):
                cands = _hough(gray_full[y0:y1, x0:x1], lo_r, hi_r, strict)
                if cands.size:
                    cands[:, :2] += (x0, y0)
                    d = np.linalg.norm(cands[:, :2] - prev[:2], axis=1) + np.abs(cands[:, 2] - pr)
                    i = int(np.argmin(d))
                    if np.linalg.norm(cands[i, :2] - prev[:2]) <= MAX_STEP * pr:
                        found = cands[i]
                        break

        if found is not None:
            centers[t], radii[t], status[t] = found[:2], found[2], "detected"
            prev = found.astype(np.float32).copy()
            x0, y0, x1, y1 = _window(frame.shape, found[0], found[1], found[2] * 1.05)
            template = gray_full[y0:y1, x0:x1].copy()
            track_run = 0
        elif prev is not None and template is not None and template.size and track_run < MAX_TRACK_RUN:
            track_run += 1
            px, py, pr = prev
            x0, y0, x1, y1 = _window(frame.shape, px, py, pr * (1.05 + MAX_STEP))
            area = gray_full[y0:y1, x0:x1]
            if area.shape[0] > template.shape[0] and area.shape[1] > template.shape[1]:
                res = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                if score >= MIN_MATCH:
                    cx = x0 + loc[0] + template.shape[1] / 2.0
                    cy = y0 + loc[1] + template.shape[0] / 2.0
                    centers[t], radii[t], status[t] = (cx, cy), pr, "tracked"
                    prev = np.array([cx, cy, pr], dtype=np.float32)
        if progress and n:
            progress((t + 1) / n)
    cap.release()

    centers, radii, status = _clean(centers, radii, status)
    return _result(centers, radii, status)


def _clean(centers: np.ndarray, radii: np.ndarray, status: np.ndarray):
    """Drop size/jump outliers, then fill short gaps by linear interpolation."""
    meas = np.isin(status, MEASURED)
    if meas.sum() < 3:
        return centers, radii, status
    r_med = float(np.nanmedian(radii[status == "detected"])) if (status == "detected").any() else float(np.nanmedian(radii[meas]))
    bad = meas & (np.abs(radii - r_med) > 0.2 * r_med)
    idx = np.where(meas & ~bad)[0]
    for k, t in enumerate(idx):
        lo, hi = max(0, k - 3), min(len(idx), k + 4)
        med = np.median(centers[idx[lo:hi]], axis=0)
        if np.linalg.norm(centers[t] - med) > MAX_STEP * r_med * 1.5:
            bad[t] = True
    centers[bad] = np.nan
    radii[bad] = np.nan
    status[bad] = "missing"

    good = np.where(np.isin(status, MEASURED))[0]
    for a, b in zip(good[:-1], good[1:]):
        gap = b - a - 1
        if 0 < gap <= MAX_GAP:
            for t in range(a + 1, b):
                w = (t - a) / (b - a)
                centers[t] = (1 - w) * centers[a] + w * centers[b]
                radii[t] = (1 - w) * radii[a] + w * radii[b]
                status[t] = "interpolated"
    return centers, radii, status


def _result(centers, radii, status) -> dict:
    n = len(status)
    return {
        "center": centers,
        "radius": radii,
        "status": status,
        # share of frames where the bar position was measured (not filled in)
        "detected_ratio": float(np.mean(np.isin(status, MEASURED))) if n else 0.0,
    }

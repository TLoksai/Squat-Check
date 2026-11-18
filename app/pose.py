"""Body landmarks with MediaPipe Pose Landmarker (Tasks API, VIDEO mode).

Each frame is analysed twice: as recorded and mirrored left-to-right. A perfect
model would give the same body points both times. The average of the two is
used as the measurement, and the disagreement between them is kept as an
honest, per-video estimate of the model's error (it widens the uncertainty
band of every check). Testing showed why this matters: on the common sample, a
mirrored copy flipped the depth result from "hip 5 cm above the knee" to
"3.5 cm below".

Output:
  landmarks[t, i] = (x_px, y_px, visibility) for 33 BlazePose points, NaN if no person
  passes[k, t, i] = the same for pass k (0 = as recorded, 1 = mirrored, mapped back)
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Callable

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "pose_landmarker_full.task"

# BlazePose landmark indices used by the app.
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_HEEL, R_HEEL = 29, 30
L_TOE, R_TOE = 31, 32  # "foot index" = tip of the big toe area

SIDES = {
    "left": {"shoulder": L_SHOULDER, "wrist": L_WRIST, "hip": L_HIP, "knee": L_KNEE,
             "ankle": L_ANKLE, "heel": L_HEEL, "toe": L_TOE},
    "right": {"shoulder": R_SHOULDER, "wrist": R_WRIST, "hip": R_HIP, "knee": R_KNEE,
              "ankle": R_ANKLE, "heel": R_HEEL, "toe": R_TOE},
}

# Mirroring the picture turns the lifter's left side into what looks like a
# right side, so the model's left/right labels swap. This undoes that.
_SWAP = list(range(33))
for a, b in [(1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18),
             (19, 20), (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32)]:
    _SWAP[a], _SWAP[b] = b, a


def _options() -> vision.PoseLandmarkerOptions:
    return vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )


def _to_array(result, w: int, h: int) -> np.ndarray:
    arr = np.full((33, 3), np.nan, dtype=np.float32)
    if result.pose_landmarks:
        for i, lm in enumerate(result.pose_landmarks[0]):
            vis = lm.visibility if lm.visibility is not None else 0.0
            # Points outside the picture are guesses, so treat them as unseen.
            if not (0.0 <= lm.x <= 1.0 and 0.0 <= lm.y <= 1.0):
                vis = min(vis, 0.1)
            arr[i] = (lm.x * w, lm.y * h, vis)
    return arr


def run_pose(video_path: Path, progress: Callable[[float], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Pose model missing: {MODEL_PATH}. See README, 'Setup'.")

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    a_frames: list[np.ndarray] = []
    b_frames: list[np.ndarray] = []

    # Two landmarkers: VIDEO mode tracks across frames, so each pass needs its own.
    with vision.PoseLandmarker.create_from_options(_options()) as normal, \
            vision.PoseLandmarker.create_from_options(_options()) as mirrored:
        t = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            ts = int(round(t * 1000.0 / fps))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            a = _to_array(normal.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts), w, h)
            flip = np.ascontiguousarray(rgb[:, ::-1])
            b = _to_array(mirrored.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=flip), ts), w, h)
            b = b[_SWAP]
            b[:, 0] = w - b[:, 0]
            a_frames.append(a)
            b_frames.append(b)
            t += 1
            if progress and total:
                progress(min(t / total, 1.0))
    cap.release()
    if not a_frames:
        empty = np.zeros((0, 33, 3), dtype=np.float32)
        return empty, np.zeros((2, 0, 33, 3), dtype=np.float32)

    passes = np.stack([np.stack(a_frames), np.stack(b_frames)])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # frames where neither pass found a person
        avg = np.nanmean(passes, axis=0)
    # A point counts as seen only if both passes saw it.
    avg[:, :, 2] = np.fmin(passes[0, :, :, 2], passes[1, :, :, 2])
    # If only one pass found the person in a frame, use that pass alone.
    only_a = np.isfinite(passes[0, :, 0, 0]) & ~np.isfinite(passes[1, :, 0, 0])
    only_b = ~np.isfinite(passes[0, :, 0, 0]) & np.isfinite(passes[1, :, 0, 0])
    avg[only_a] = passes[0][only_a]
    avg[only_b] = passes[1][only_b]
    return avg.astype(np.float32), passes.astype(np.float32)

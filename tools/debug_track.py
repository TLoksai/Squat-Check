"""Developer check: run pose + bar tracking and save a contact sheet of overlays.

    python -m tools.debug_track samples/common_sample.mp4 out.jpg
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

from app.bar import track_bar
from app.pose import SIDES, run_pose
from app.video import normalise


def main(src: str, out: str) -> None:
    work = Path(out).with_suffix(".norm.mp4")
    t0 = time.time()
    info = normalise(Path(src), work)
    print("normalised", info.to_dict(), f"{time.time() - t0:.1f}s")
    t0 = time.time()
    lm, _ = run_pose(work)
    print("pose", lm.shape, f"{time.time() - t0:.1f}s")
    vis = np.nanmean(lm[:, :, 2], axis=0)
    side = "left" if np.nansum(vis[[11, 23, 25, 27, 29, 31]]) >= np.nansum(vis[[12, 24, 26, 28, 30, 32]]) else "right"
    print("side", side, "vis", {k: round(float(vis[v]), 2) for k, v in SIDES[side].items()})
    t0 = time.time()
    bar = track_bar(work, lm, side)
    print("bar detected ratio", round(bar["detected_ratio"], 2), f"{time.time() - t0:.1f}s",
          {s: int((bar["status"] == s).sum()) for s in ("detected", "interpolated", "missing")})

    cap = cv2.VideoCapture(str(work))
    n = lm.shape[0]
    picks = np.linspace(0, n - 1, 8).astype(int)
    tiles = []
    s = SIDES[side]
    for t in range(n):
        ok, f = cap.read()
        if not ok:
            break
        if t not in picks:
            continue
        for name, i in s.items():
            x, y, v = lm[t, i]
            if np.isfinite(x):
                cv2.circle(f, (int(x), int(y)), 7, (0, 255, 0) if v > 0.5 else (0, 0, 255), -1)
        c = bar["center"][: t + 1]
        pts = c[np.isfinite(c[:, 0])].astype(int)
        if len(pts) > 1:
            cv2.polylines(f, [pts.reshape(-1, 1, 2)], False, (255, 0, 255), 3)
        if np.isfinite(bar["radius"][t]):
            cx, cy = bar["center"][t]
            cv2.circle(f, (int(cx), int(cy)), int(bar["radius"][t]), (255, 255, 0), 3)
        cv2.putText(f, f"f{t} {bar['status'][t]}", (10, 40), 0, 1.2, (0, 255, 255), 3)
        tiles.append(cv2.resize(f, (270, int(270 * f.shape[0] / f.shape[1]))))
    cap.release()
    cv2.imwrite(out, np.vstack([np.hstack(tiles[:4]), np.hstack(tiles[4:8])]))
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

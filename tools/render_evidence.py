"""Save evidence images for a finished analysis: the bottom frame of every rep
with the tracked points, bar path, midfoot line and knee-height line drawn on,
plus the measured numbers. Used for docs/evidence and for checking by eye.

    python -m tools.render_evidence data/cli/common_sample docs/evidence/common_sample.jpg
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np


def _pt(p):
    return None if p is None else (int(round(p[0])), int(round(p[1])))


def render(job_dir: Path, out: Path) -> None:
    res = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    a = res["analysis"]
    if a["status"] != "ok":
        print("unsuitable:", a["message"])
        return
    frames = a["frames"]
    findings = {(f["rep"], f["criterion_id"]): f for f in res["evaluation"]["findings"]}
    cap = cv2.VideoCapture(str(job_dir / "video.mp4"))
    tiles = []
    for rep in a["reps"]:
        b = rep["bottom_frame"]
        cap.set(cv2.CAP_PROP_POS_FRAMES, b)
        ok, img = cap.read()
        if not ok:
            continue
        F = frames[b]
        # bar path for the rep
        path = [frames[k]["bar"] for k in range(rep["start_frame"], rep["end_frame"] + 1) if frames[k]["bar"]]
        if len(path) > 1:
            cv2.polylines(img, [np.array(path, dtype=np.int32).reshape(-1, 1, 2)], False, (247, 85, 168), 4)
        if F["bar"] and F["bar_r"]:
            cv2.circle(img, _pt(F["bar"]), int(F["bar_r"]), (247, 85, 168), 2)
        if a["feet"].get("seen", True):
            mx, my = _pt(a["feet"]["midfoot"])
            cv2.line(img, (mx, my), (mx, 0), (238, 211, 34), 2)
            for k in ("heel", "toe"):
                cv2.circle(img, _pt(a["feet"][k]), 6, (11, 158, 245), 2)
        # knee-height line and joints
        kx, ky = _pt(F["knee_s"])
        hx, hy = _pt(F["hip_s"])
        below = hy > ky
        cv2.line(img, (kx, ky), (hx + (hx - kx) // 3, ky), (74, 222, 128) if below else (60, 146, 251), 2)
        cv2.line(img, (hx, hy), _pt(F["torso_top"]), (21, 204, 250), 3)
        for name in ("shoulder", "hip", "knee", "ankle"):
            p = _pt(F[name])
            if p:
                cv2.circle(img, p, 7, (11, 158, 245), 2 if F.get(f"{name}_est") else -1)
        lines = [f"rep {rep['index']} bottom  t={rep['bottom_s']:.2f}s  frame {b}"]
        for cid, short in (("depth", "depth"), ("bar_over_midfoot", "bar/midfoot"), ("back_angle_bottom", "back angle")):
            f = findings.get((rep["index"], cid))
            if f and f["measurement"] and f["measurement"]["value"] is not None:
                lines.append(f"{short}: {f['measurement']['value']:.2f} -> {f['result']}")
        for i, text in enumerate(lines):
            y = 40 + i * 34
            cv2.putText(img, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
            cv2.putText(img, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        h = 900
        tiles.append(cv2.resize(img, (int(img.shape[1] * h / img.shape[0]), h)))
    cap.release()
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print("wrote", out)


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]))

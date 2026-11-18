"""The critical path: upload -> normalise -> pose -> bar -> reps/measures -> rules -> coach."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import agent, quality
from .analysis import analyse
from .bar import track_bar
from .pose import run_pose
from .rules import evaluate, load_skill
from .video import normalise

Progress = Callable[[str, float, str], None]

STAGES = [
    ("prepare", 0.00, 0.08, "Preparing your video"),
    ("pose", 0.08, 0.50, "Finding hips, knees and ankles"),
    ("bar", 0.50, 0.82, "Tracking the bar"),
    ("measure", 0.82, 0.88, "Counting reps and checking the rules"),
    ("coach", 0.88, 1.00, "Writing your feedback"),
]


def _stage(progress: Progress, key: str, frac: float = 0.0) -> None:
    for k, lo, hi, label in STAGES:
        if k == key:
            progress(k, lo + (hi - lo) * frac, label)
            return


def run(job_dir: Path, src: Path, progress: Progress) -> dict:
    timings = {}
    t0 = time.time()
    _stage(progress, "prepare")
    video_path = job_dir / "video.mp4"
    info = normalise(src, video_path)
    timings["prepare_s"] = round(time.time() - t0, 1)

    t = time.time()
    _stage(progress, "pose")
    lm, passes = run_pose(video_path, lambda f: _stage(progress, "pose", f))
    timings["pose_s"] = round(time.time() - t, 1)
    q = quality.measure(video_path, lm)
    (job_dir / "quality.json").write_text(json.dumps(q), encoding="utf-8")

    skill = load_skill()

    t = time.time()
    _stage(progress, "bar")
    vis = np.nan_to_num(lm[:, :, 2])
    side = "left" if vis[:, [11, 23, 25, 27, 29, 31]].sum() >= vis[:, [12, 24, 26, 28, 30, 32]].sum() else "right"
    bar = track_bar(video_path, lm, side, lambda f: _stage(progress, "bar", f))
    timings["bar_s"] = round(time.time() - t, 1)

    # Keep the raw tracking so the rules can be re-run later without re-processing.
    np.save(job_dir / "landmarks.npy", lm)
    np.save(job_dir / "pose_passes.npy", passes)
    np.savez(job_dir / "bar.npz", center=bar["center"], radius=bar["radius"],
             status=bar["status"].astype(str), detected_ratio=bar["detected_ratio"])

    return _assess(job_dir, info.to_dict(), lm, passes, bar, skill, progress, timings, t0)


def _pass_uncertainty(result: dict, alts: list[dict]) -> None:
    """Widen each metric's uncertainty by half the disagreement between the
    normal and mirrored pose passes (measured on this very video)."""
    for rep in result["reps"]:
        matched = []
        for alt in alts:
            cand = [r for r in alt.get("reps", []) if abs(r["bottom_frame"] - rep["bottom_frame"]) <= 10]
            matched.append(cand[0] if cand else None)
        for key, m in rep["metrics"].items():
            vals = [r["metrics"][key]["value"] if r and r["metrics"].get(key) else None for r in matched]
            if m["value"] is None:
                continue
            if any(v is None for v in vals):
                m["observed"] = False
                m["detail"] += " One of the two pose passes could not measure this rep, so it isn't judged."
                continue
            spread = abs(vals[0] - vals[1]) / 2.0
            m["extra_uncertainty"] = round(m["extra_uncertainty"] + spread, 3)
            m.setdefault("extra", {})["pose_passes"] = [round(v, 3) for v in vals]
            if spread > 0:
                m["detail"] += f" (Normal and mirrored pose passes gave {vals[0]:.2f} and {vals[1]:.2f}.)"


def _assess(job_dir: Path, video: dict, lm, passes, bar: dict, skill: dict, progress: Progress,
            timings: dict, t0: float) -> dict:
    """Measurements -> rules -> coach. Cheap; re-run whenever the skill changes."""
    t = time.time()
    _stage(progress, "measure")
    estimates = skill["criteria"].get("estimates", {})
    size = (video["width"], video["height"])
    qf = job_dir / "quality.json"
    q = json.loads(qf.read_text(encoding="utf-8")) if qf.exists() else None
    q_limits = skill["criteria"].get("video_quality")
    result = analyse(lm, bar, video["fps"], size, estimates, q, q_limits)
    if result["status"] == "ok" and passes is not None:
        # Same rep frames for both passes, so the spread is landmark disagreement only.
        frames = [(r["start_frame"], r["bottom_frame"], r["end_frame"], r["bottom_window"]) for r in result["reps"]]
        alts = [analyse(p, bar, video["fps"], size, estimates, reps_override=frames,
                        torso_override=result["torso_uses_bar"]) for p in passes]
        _pass_uncertainty(result, alts)
    evaluation = evaluate(skill, result) if result["status"] == "ok" else \
        {"findings": [], "not_assessable": [], "counts": {"meets": 0, "does_not_meet": 0, "cannot_assess": 0}}
    timings["measure_s"] = round(time.time() - t, 2)

    t = time.time()
    _stage(progress, "coach")
    if result["status"] == "ok":
        feedback = agent.coach(skill, evaluation, result)
    else:
        feedback = {"overall_summary": result["message"], "top_priorities": [], "findings_feedback": [],
                    "source": "app"}
    timings["coach_s"] = round(time.time() - t, 1)
    # total processing time for this video (a re-check reuses the tracking times)
    timings["total_s"] = round(sum(float(timings.get(k) or 0) for k in
                                   ("prepare_s", "pose_s", "bar_s", "measure_s", "coach_s")), 1)

    out = {
        "video": video,
        "analysis": result,
        "evaluation": evaluation,
        "feedback": feedback,
        "skill": {"name": "squat-standard", "version": skill["criteria"].get("version"),
                  "source": skill["criteria"].get("source"),
                  "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        "timings": timings,
    }
    out = json.loads(json.dumps(out, default=_to_builtin))  # numpy -> plain Python, once
    (job_dir / "result.json").write_text(json.dumps(out), encoding="utf-8")
    return out


def reassess(job_dir: Path) -> dict:
    """Re-run measurements, rules and coach with the CURRENT skill files."""
    prev = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    lm = np.load(job_dir / "landmarks.npy")
    pp = job_dir / "pose_passes.npy"
    passes = np.load(pp) if pp.exists() else None
    z = np.load(job_dir / "bar.npz")
    bar = {"center": z["center"], "radius": z["radius"], "status": z["status"].astype(object),
           "detected_ratio": float(z["detected_ratio"])}
    timings = {k: prev["timings"].get(k) for k in ("prepare_s", "pose_s", "bar_s")}
    return _assess(job_dir, prev["video"], lm, passes, bar, load_skill(), lambda *a: None, timings, time.time())


def _to_builtin(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not serialisable: {type(o).__name__}")

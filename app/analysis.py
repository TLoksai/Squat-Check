"""Turn landmarks + bar track into reps and per-rep measurements.

Everything here is deterministic geometry. No AI decides a number.

Conventions
  - Image y grows downward, so "lower in the picture" = larger y.
  - `fwd` is +1 if the lifter faces right in the picture, -1 if facing left.
    Forward offsets are multiplied by `fwd` so "positive = in front".
  - Lengths are normalised by the lifter's own segments (thigh or foot length)
    so results do not depend on how far away the camera is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from .pose import SIDES
from .quality import problems as video_quality_problems

PLATE_DIAMETER_CM = 45.0  # standard competition / bumper plate (used only as a ruler)

# Sanity limits: a squat can't produce values outside these, so a value outside
# means the tracking failed and the check is "cannot assess" (app choice).
PLAUSIBLE = {
    "depth_margin": (-1.2, 1.2),                      # thigh lengths
    "bar_midfoot_max_offset": (0.0, 2.0),             # foot lengths
    "bar_horizontal_range": (0.0, 2.0),               # foot lengths
    "back_angle_bottom_deg": (10.0, 88.0),            # degrees
    "back_angle_change_early_ascent_deg": (-40.0, 40.0),
    "knee_ahead_of_toe": (-1.0, 1.5),                 # foot lengths
    "heel_lift": (0.0, 0.6),                          # foot lengths
    "bottom_pause_s": (0.0, 5.0),
}


# ---------------------------------------------------------------- helpers

def _interp_nan(a: np.ndarray) -> np.ndarray:
    a = a.astype(float).copy()
    ok = np.isfinite(a)
    if ok.sum() < 2:
        return a
    idx = np.arange(len(a))
    a[~ok] = np.interp(idx[~ok], idx[ok], a[ok])
    return a


def _smooth(a: np.ndarray, window: int = 9) -> np.ndarray:
    a = _interp_nan(a)
    if np.isfinite(a).sum() < window or len(a) < window:
        return a
    return savgol_filter(a, window, 2)


def _angle_from_floor(dx: float, dy: float) -> float:
    """Angle of a segment from horizontal, 0 = flat, 90 = vertical."""
    return float(np.degrees(np.arctan2(abs(dy), abs(dx))))


@dataclass
class Metric:
    value: float | None
    unit: str
    frame: int | None = None
    observed: bool = True          # False -> inputs were estimated/unseen; rules say "cannot assess"
    extra_uncertainty: float = 0.0  # measurement uncertainty added on top of the rule's band
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "value": None if self.value is None else round(float(self.value), 3),
            "unit": self.unit,
            "frame": self.frame,
            "observed": self.observed,
            "extra_uncertainty": round(float(self.extra_uncertainty), 3),
            "detail": self.detail,
            **({"extra": self.extra} if self.extra else {}),
        }


# ---------------------------------------------------------------- main entry

def analyse(landmarks: np.ndarray, bar: dict, fps: float, frame_size: tuple[int, int],
            estimates: dict, quality: dict | None = None, quality_limits: dict | None = None,
            reps_override: list[tuple[int, int, int]] | None = None,
            torso_override: bool | None = None) -> dict[str, Any]:
    n = landmarks.shape[0]
    width, height = frame_size
    min_vis = float(estimates.get("min_landmark_visibility", 0.5))
    issues: list[dict] = []

    present = np.isfinite(landmarks[:, 23, 0])
    if n == 0 or present.mean() < 0.5:
        return _unsuitable("We couldn't find a person in most of the video. "
                           "Make sure your whole body is in the picture and well lit.", n, fps)

    if quality:
        block, warn = video_quality_problems(quality, quality_limits)
        if block:
            return _unsuitable("We can't measure this video reliably: " + "; ".join(block) + ". "
                               "Please re-record in good light with the camera steady and closer.", n, fps,
                               extra={"quality": quality})
        issues += [{"code": "quality", "message": w} for w in warn]

    # ---- which side faces the camera, and which way the lifter faces
    vis = np.nan_to_num(landmarks[:, :, 2])
    side = "left" if vis[:, [11, 23, 25, 27, 29, 31]].sum() >= vis[:, [12, 24, 26, 28, 30, 32]].sum() else "right"
    S = SIDES[side]
    other = SIDES["right" if side == "left" else "left"]

    def P(name: str) -> np.ndarray:
        return landmarks[:, S[name], :2].astype(float)

    def V(name: str) -> np.ndarray:
        return np.nan_to_num(landmarks[:, S[name], 2])

    hip, knee, ankle, shoulder = P("hip"), P("knee"), P("ankle"), P("shoulder")
    heel_raw, toe_raw = P("heel"), P("toe")

    # Feet don't move during a squat: use each foot point's median over the
    # video. This removes landmark jitter (the toe point jumps at the bottom).
    heel_med = np.nanmedian(heel_raw, axis=0)
    toe_med = np.nanmedian(toe_raw, axis=0)
    fwd = 1 if toe_med[0] > heel_med[0] else -1
    foot_len = float(abs(toe_med[0] - heel_med[0]))
    midfoot = (heel_med + toe_med) / 2.0

    thigh = np.linalg.norm(hip - knee, axis=1)
    thigh_med = float(np.nanmedian(thigh))
    if not np.isfinite(thigh_med) or thigh_med < 20:
        return _unsuitable("The legs are too small or not visible. Move the camera closer "
                           "so your whole body fills most of the picture.", n, fps)
    # Feet must be seen for the midfoot, knee-vs-toe and heel checks. If they
    # aren't, those checks are not judged, and lengths use an estimated foot.
    feet_seen = float(((V("heel") >= min_vis) & (V("toe") >= min_vis)).mean()) >= 0.5 and foot_len >= 10
    if not feet_seen:
        foot_len = float(estimates.get("foot_thigh_ratio", 0.58)) * thigh_med
        issues.append({"code": "feet_hidden", "message": "Your feet are not clearly in the picture, so checks that need "
                       "the feet (bar over midfoot, knee vs toes, heels) are not judged."})

    # ---- side-view check: in a true side view the left and right hips nearly overlap
    hip_dx = np.abs(landmarks[:, 23, 0] - landmarks[:, 24, 0]).astype(float)
    hip_sep = float(np.nanmedian(hip_dx) / thigh_med)
    if hip_sep > 0.45:
        return _unsuitable("This doesn't look like a side view (we can see both hips side by side). "
                           "Film from the side, at hip height, so we can see the bar path and depth.", n, fps)
    if hip_sep > 0.2:
        issues.append({"code": "camera_angle", "message": "The camera seems a little in front of or behind the side. "
                       "Bar-over-midfoot results carry extra uncertainty."})
    # Parallax: the plate is ~30 cm nearer the camera than the foot. When the
    # camera is not square, the plate shifts sideways relative to the foot.
    sin_yaw = float(np.clip(hip_sep / float(estimates.get("hip_width_thigh_ratio", 0.42)), 0, 1))
    parallax_foot = float(estimates.get("plate_to_foot_lateral_foot_lengths", 1.15)) * sin_yaw

    # ---- framing: are the key joints inside the picture and seen?
    for name in ("hip", "knee", "ankle", "heel", "toe"):
        seen = (V(name) >= min_vis).mean()
        if seen < 0.7:
            issues.append({"code": f"{name}_hidden",
                           "message": f"The {name} is hidden or out of the picture in {100 - seen * 100:.0f}% of frames."})

    # ---- bar
    bar_c = bar["center"].astype(float)
    bar_r = bar["radius"].astype(float)
    bar_status = bar["status"]
    bar_ok = np.isin(bar_status, ["detected", "tracked", "interpolated"])
    r_med = float(np.nanmedian(bar_r)) if np.isfinite(bar_r).any() else float("nan")
    px_per_cm = (2 * r_med / PLATE_DIAMETER_CM) if np.isfinite(r_med) else None
    if bar["detected_ratio"] < 0.3:
        issues.append({"code": "bar_not_found", "message": "We couldn't see the weight plate clearly, so bar rules can't be assessed. "
                       "Make sure the end of the bar (the plate) is in view."})

    # Shoulder hidden behind the plate? Visibility scores don't catch this.
    sh_in_plate = np.zeros(n, dtype=bool)
    ok = bar_ok & np.isfinite(shoulder[:, 0])
    sh_in_plate[ok] = np.linalg.norm(shoulder[ok] - bar_c[ok], axis=1) < 0.9 * bar_r[ok]
    use_bar_for_torso = bar["detected_ratio"] >= 0.6 and sh_in_plate[bar_ok].mean() > 0.3 if bar_ok.any() else False
    if torso_override is not None:
        use_bar_for_torso = torso_override
    torso_top = np.where(bar_ok[:, None], bar_c, shoulder) if use_bar_for_torso else shoulder
    torso_label = "hip to bar (shoulder hidden behind the plate; the bar sits on the upper back)" if use_bar_for_torso \
        else "hip to shoulder"

    # ---- smoothed signals
    hip_y = _smooth(hip[:, 1])
    hip_x = _smooth(hip[:, 0])
    knee_x, knee_y = _smooth(knee[:, 0]), _smooth(knee[:, 1])
    heel_y = _smooth(heel_raw[:, 1])
    top_x, top_y = _smooth(torso_top[:, 0]), _smooth(torso_top[:, 1])
    # Light smoothing of the bar track: frame-to-frame circle jitter (a few px)
    # would otherwise inflate the sideways-travel measurement.
    if bar_ok.sum() >= 9:
        bar_c = np.stack([_smooth(bar_c[:, 0], 7), _smooth(bar_c[:, 1], 7)], axis=1)
        bar_c[~bar_ok] = np.nan
    bar_x = np.where(bar_ok, bar_c[:, 0], np.nan)

    back_angle = np.array([_angle_from_floor(top_x[t] - hip_x[t], top_y[t] - hip_y[t]) for t in range(n)])

    # knee angle (hip-knee-ankle): 180 = straight leg. Used to tell a real squat
    # from a tracking glitch that only moves the hip point.
    ankle_x, ankle_y = _smooth(ankle[:, 0]), _smooth(ankle[:, 1])
    v1 = np.stack([hip_x - knee_x, hip_y - knee_y], axis=1)
    v2 = np.stack([ankle_x - knee_x, ankle_y - knee_y], axis=1)
    cosang = (v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1) + 1e-9)
    knee_angle = np.degrees(np.arccos(np.clip(cosang, -1, 1)))

    # ---- reps: each rep is one dip of the bar (when the plate is well tracked)
    # or of the hips. The plate is tracked far more reliably than a hip that the
    # plate itself covers at the bottom: on one real clip the hip point jumped UP
    # at the deepest moment, which split a rep in two and moved "the bottom".
    bar_reps = bar["detected_ratio"] >= 0.8
    sig = _smooth(np.where(bar_ok, bar_c[:, 1], np.nan)) if bar_reps else hip_y
    rejected: list[int] = []
    reps_raw = reps_override if reps_override is not None else \
        _find_reps(sig, thigh_med, fps, None if bar_reps else knee_angle, rejected)
    for p in rejected:
        issues.append({"code": "rep_untracked", "message":
                       f"Something that looks like a rep at {p / fps:.1f} s was not judged: the legs weren't tracked "
                       "reliably there (they may be hidden behind the rack, the plate or another object)."})
    if not reps_raw:
        msg = ("We couldn't find a full squat rep (down and back up). "
               "Make sure the video shows at least one complete rep.")
        if rejected:
            msg = (f"We saw what looks like a rep at {', '.join(f'{p / fps:.1f} s' for p in rejected)}, but couldn't "
                   "track the legs reliably (they may be hidden behind the rack or the plate). "
                   "Film with a clear side view of the whole body.")
        return _unsuitable(msg, n, fps, extra={"side": side})

    min_bar_ratio = float(estimates.get("min_bar_detection_ratio", 0.6))
    persp_factor = float(estimates.get("plate_perspective_factor", 0.15))
    reps = []
    for i, rep_frames in enumerate(reps_raw, start=1):
        s0, b, e = rep_frames[:3]
        sl = slice(s0, e + 1)
        rom = sig[b] - min(sig[s0], sig[e])                    # travel of the rep signal (bar or hips)
        hip_rom = hip_y[b] - min(hip_y[s0], hip_y[e])          # hip travel, compared across pose passes
        m: dict[str, Metric] = {}
        # "The bottom" = the frames where the bar (or hips) are within 3% of
        # their lowest point. Bottom measurements are medians over these frames,
        # so a one-frame shift in where the bottom is found doesn't move the result.
        if len(rep_frames) > 3:   # same bottom frames as the main analysis (pose-pass comparison)
            bot = np.arange(rep_frames[3][0], rep_frames[3][1] + 1)
        else:
            bot = np.where(sig[s0:e + 1] >= sig[b] - 0.03 * rom)[0] + s0
        if bot.size == 0:
            bot = np.array([b])

        # Is the hip or knee behind the plate at the bottom? The pose model
        # still reports a confident point there, but it's a guess.
        def behind_plate(px, py) -> bool:
            return bool(any(bar_ok[t] and np.isfinite(bar_r[t]) and
                            np.hypot(px[t] - bar_c[t, 0], py[t] - bar_c[t, 1]) < bar_r[t] for t in bot))
        hip_hidden = behind_plate(hip_x, hip_y)
        knee_hidden = behind_plate(knee_x, knee_y)
        hidden_note = (" The hip was hidden behind the plate at the bottom, so this can't be measured." if hip_hidden else
                       " The knee was hidden behind the plate at the bottom, so this can't be measured." if knee_hidden
                       else "")

        # Feet for THIS rep (the lifter may walk out of the rack before the
        # set, so whole-video medians can be wrong). Foot checks are skipped if
        # the feet move during the rep.
        h_r = np.nanmedian(heel_raw[sl], axis=0)
        t_r = np.nanmedian(toe_raw[sl], axis=0)
        feet_note = ""
        if feet_seen and np.all(np.isfinite(h_r)) and np.all(np.isfinite(t_r)) and abs(t_r[0] - h_r[0]) >= 10:
            fl = float(abs(t_r[0] - h_r[0]))
            mid_r, toe_r = (h_r + t_r) / 2.0, t_r
            spread = float(np.nanpercentile(heel_raw[sl, 0], 90) - np.nanpercentile(heel_raw[sl, 0], 10))
            feet_still = spread <= 0.35 * fl
            if not feet_still:
                feet_note = " The feet moved during this rep, so foot-based checks are not judged."
        else:
            fl, mid_r, toe_r, feet_still = foot_len, midfoot, toe_med, feet_seen
        feet_ok = bool(feet_seen and feet_still)

        # depth: the document compares the hip crease with the top of the
        # kneecap. Both sit on the top surface of the thigh, about equally far
        # above the hip and knee joint centres the pose model tracks, so we
        # compare the joint centres (the offsets cancel; the rule's uncertainty
        # band covers the difference).
        hip_seen = V("hip")[b] >= min_vis and V("knee")[b] >= min_vis and not hip_hidden and not knee_hidden
        gap_px = float(np.median(hip_y[bot] - knee_y[bot]))
        cm = abs(gap_px) / px_per_cm if px_per_cm else None
        m["depth_margin"] = Metric(
            gap_px / thigh_med, "thigh lengths", b, observed=bool(hip_seen),
            detail=f"At the bottom the hip is {abs(gap_px):.0f} px "
                   + (f"(about {cm:.0f} cm) " if cm else "")
                   + f"{'below' if gap_px > 0 else 'above'} the knee." + hidden_note,
            extra={"hip_y": round(float(hip_y[b]), 1), "knee_y": round(float(knee_y[b]), 1),
                   "approx_cm": None if cm is None else round(cm, 1)},
        )

        # bar vs midfoot and bar path
        det_ratio = float(np.mean(np.isin(bar_status[sl], ["detected", "tracked"])))
        bar_enough = det_ratio >= min_bar_ratio
        bx = bar_x[sl]
        offs = fwd * (bx - mid_r[0]) / fl
        if np.isfinite(offs).any():
            # "Largest" offset = 95th percentile, so one glitchy frame can't decide it.
            p95 = float(np.nanpercentile(np.abs(offs), 95))
            j = int(np.nanargmin(np.abs(np.abs(offs) - p95)))
            off = float(np.sign(offs[j]) * p95)
            cm = (abs(off) * fl / px_per_cm) if px_per_cm else None
            # Perspective: the plate is nearer the camera than the foot, so its
            # picture position is pushed away from the picture centre a little.
            persp = abs(bx[j] - width / 2.0) * persp_factor / fl
            m["bar_midfoot_max_offset"] = Metric(
                abs(off), "foot lengths", s0 + j, observed=bar_enough and feet_ok, extra_uncertainty=parallax_foot + persp,
                detail=f"Largest bar distance from midfoot: {abs(off):.2f} foot lengths "
                       f"{'in front of' if off > 0 else 'behind'} the midfoot"
                       + (f" (about {cm:.0f} cm, using the plate as a ruler)" if cm else "") + "." + feet_note,
                extra={"direction": "forward" if off > 0 else "backward", "approx_cm": None if cm is None else round(cm, 1),
                       "bar_detected_ratio": round(det_ratio, 2)},
            )
            rng = float((np.nanpercentile(bx, 97.5) - np.nanpercentile(bx, 2.5)) / fl)
            cm_r = rng * fl / px_per_cm if px_per_cm else None
            m["bar_horizontal_range"] = Metric(
                rng, "foot lengths", s0 + j, observed=bar_enough,
                detail=f"Bar moved {rng:.2f} foot lengths forward/back in total"
                       + (f" (about {cm_r:.0f} cm)" if cm_r else "") + ".",
                extra={"approx_cm": None if cm_r is None else round(cm_r, 1), "bar_detected_ratio": round(det_ratio, 2)},
            )
        else:
            for k in ("bar_midfoot_max_offset", "bar_horizontal_range"):
                m[k] = Metric(None, "foot lengths", None, observed=False, detail="Bar not tracked in this rep.")

        # back angle at the bottom
        top_seen = ((bar_ok[b] if use_bar_for_torso else V("shoulder")[b] >= min_vis) and V("hip")[b] >= min_vis
                    and not hip_hidden)
        ba_bottom = float(np.median(back_angle[bot]))
        m["back_angle_bottom_deg"] = Metric(
            ba_bottom, "degrees", b, observed=bool(top_seen),
            detail=f"Torso line ({torso_label}) is {ba_bottom:.0f} degrees from the floor at the bottom."
                   + (hidden_note if hip_hidden else ""),
        )

        # hip drive: how much more upright the torso gets in the first third of the ascent
        target_y = sig[b] - rom / 3.0
        after = np.where(sig[b:e + 1] <= target_y)[0]
        if after.size:
            a = b + int(after[0])
            change = back_angle[a] - ba_bottom
            m["back_angle_change_early_ascent_deg"] = Metric(
                change, "degrees", a, observed=bool(top_seen),
                detail=f"In the first third of the way up (frame {b} to {a}), the torso turned "
                       f"{abs(change):.0f} degrees {'more upright' if change > 0 else 'more forward'}."
                       + (hidden_note if hip_hidden else ""),
                extra={"from_frame": b, "to_frame": a},
            )
        else:
            m["back_angle_change_early_ascent_deg"] = Metric(None, "degrees", None, observed=False,
                                                             detail="The rep's ascent is not fully in the video.")

        # heels: rise of the heel point vs its standing height, taken as the
        # median over the bottom part of the rep (hips in the lowest 30% of
        # their travel), where heels come up if they do. A median, not a
        # maximum, so one jittery frame can't fail the rep.
        stand_heel = np.nanmedian(np.r_[heel_y[max(0, s0 - 3):s0 + 4], heel_y[max(0, e - 3):e + 4]])
        low = np.where(sig[sl] >= sig[b] - 0.3 * rom)[0] + s0
        lift_px = float(np.nanmedian(stand_heel - heel_y[low]))
        heel_seen = feet_ok and (V("heel")[sl] >= min_vis).mean() >= 0.8
        cm_h = max(lift_px, 0) / px_per_cm if px_per_cm else None
        m["heel_lift"] = Metric(
            max(lift_px, 0.0) / fl, "foot lengths", b, observed=bool(heel_seen),
            detail=f"Around the bottom the heel rose {max(lift_px, 0):.0f} px"
                   + (f" (about {cm_h:.1f} cm)" if cm_h is not None else "") + " compared with standing." + feet_note,
        )

        # pause at the bottom: time the bar (or hips) stay within 5% of their lowest point
        near = sig[sl] >= sig[b] - 0.05 * rom
        k = b - s0
        lo = k
        while lo > 0 and near[lo - 1]:
            lo -= 1
        hi = k
        while hi < len(near) - 1 and near[hi + 1]:
            hi += 1
        pause = (hi - lo + 1) / fps
        m["bottom_pause_s"] = Metric(pause, "seconds", b, observed=True,
                                     detail=f"The {'bar' if bar_reps else 'hips'} stayed within 5% of the lowest point "
                                            f"for {pause:.2f} s.")

        # knee vs toe at the bottom
        knee_toe = float(np.median(fwd * (knee_x[bot] - toe_r[0]) / fl))
        m["knee_ahead_of_toe"] = Metric(
            knee_toe, "foot lengths", b, observed=bool(V("knee")[b] >= min_vis and feet_ok and not knee_hidden),
            detail=f"Knee joint is {abs(knee_toe):.2f} foot lengths {'in front of' if knee_toe > 0 else 'behind'} the toe point."
                   + feet_note,
        )

        # Physically impossible values mean the tracking failed (seen when a
        # rack crossbar hid the hips and knees): don't judge them.
        for key, (lo_v, hi_v) in PLAUSIBLE.items():
            mm = m.get(key)
            if mm is not None and mm.value is not None and not (lo_v <= mm.value <= hi_v):
                mm.observed = False
                mm.detail += " This value is physically implausible, so it is treated as a tracking error and not judged."

        reps.append({
            "index": i,
            "start_frame": int(s0), "bottom_frame": int(b), "end_frame": int(e),
            "bottom_window": [int(bot.min()), int(bot.max())],
            "start_s": round(s0 / fps, 2), "bottom_s": round(b / fps, 2), "end_s": round(e / fps, 2),
            "bar_detected_ratio": round(det_ratio, 2),
            "rom_px": round(float(hip_rom), 1),
            "rep_signal": "bar" if bar_reps else "hips",
            "knee_angle_bottom": round(float(np.median(knee_angle[bot])), 1),
            "feet": {"heel": [round(float(v), 1) for v in (h_r if feet_ok else heel_med)],
                     "toe": [round(float(v), 1) for v in toe_r],
                     "midfoot": [round(float(v), 1) for v in mid_r], "seen": feet_ok},
            "metrics": {k: v.to_dict() for k, v in m.items()},
        })

    # ---- per-frame overlay data (for the browser player)
    def pt(arr, t):
        x, y = arr[t]
        return None if not (np.isfinite(x) and np.isfinite(y)) else [round(float(x), 1), round(float(y), 1)]

    frames = []
    for t in range(n):
        lm_vis = {nm: float(V(nm)[t]) for nm in ("shoulder", "hip", "knee", "ankle")}
        sh_est = bool(sh_in_plate[t] or lm_vis["shoulder"] < min_vis)
        frames.append({
            "shoulder": pt(shoulder, t), "shoulder_est": sh_est,
            "hip": pt(hip, t), "hip_est": lm_vis["hip"] < min_vis,
            "knee": pt(knee, t), "knee_est": lm_vis["knee"] < min_vis,
            "ankle": pt(ankle, t), "ankle_est": lm_vis["ankle"] < min_vis,
            "torso_top": [round(float(top_x[t]), 1), round(float(top_y[t]), 1)],
            "torso_top_est": bool((not bar_ok[t]) if use_bar_for_torso else sh_est),
            "hip_s": [round(float(hip_x[t]), 1), round(float(hip_y[t]), 1)],
            "knee_s": [round(float(knee_x[t]), 1), round(float(knee_y[t]), 1)],
            "bar": pt(bar_c, t) if bar_ok[t] else None,
            "bar_r": None if not np.isfinite(bar_r[t]) else round(float(bar_r[t]), 1),
            "bar_status": str(bar_status[t]),
            "back_angle": round(float(back_angle[t]), 1),
        })

    return {
        "status": "ok",
        "fps": fps,
        "frame_count": n,
        "frame_size": [width, height],
        "side": side,
        "facing": "right" if fwd > 0 else "left",
        "torso_line": torso_label,
        "torso_uses_bar": bool(use_bar_for_torso),
        "feet": {"heel": [round(float(v), 1) for v in heel_med], "toe": [round(float(v), 1) for v in toe_med],
                 "midfoot": [round(float(v), 1) for v in midfoot], "foot_len_px": round(foot_len, 1),
                 "seen": feet_seen},
        "scale": {"px_per_cm": None if px_per_cm is None else round(px_per_cm, 3),
                  "how": f"plate assumed {PLATE_DIAMETER_CM:.0f} cm across (standard plate); approximate"},
        "camera": {"hip_separation": round(hip_sep, 3), "parallax_foot_lengths": round(parallax_foot, 3)},
        "quality": quality,
        "bar_detected_ratio": round(float(bar["detected_ratio"]), 3),
        "issues": issues,
        "reps": reps,
        "frames": frames,
    }


def _find_reps(hip_y: np.ndarray, thigh: float, fps: float, knee_angle: np.ndarray | None = None,
               rejected: list | None = None) -> list[tuple[int, int, int]]:
    """Each rep = (start, bottom, end) frames. A rep must drop the hips by at
    least 0.4 thigh lengths, bend the knees (a squat, not a tracking glitch that
    only moves the hip point), and come back up within the video."""
    peaks, _ = find_peaks(hip_y, prominence=0.4 * thigh, distance=max(1, int(0.6 * fps)))
    reps = []
    prev_end = 0
    look = int(3.0 * fps)  # a squat goes down (or up) in under ~3 s, even a slow one
    for p in peaks:
        lo0 = max(prev_end, p - look)
        top_before = lo0 + int(np.argmin(hip_y[lo0:p + 1]))
        nxt = peaks[peaks > p]
        hi_lim = min(int(nxt[0]) if nxt.size else len(hip_y) - 1, p + look)
        top_after = p + int(np.argmin(hip_y[p:hi_lim + 1]))
        rom = hip_y[p] - max(hip_y[top_before], hip_y[top_after])
        if rom < 0.4 * thigh:
            continue
        thr_s = hip_y[top_before] + 0.05 * (hip_y[p] - hip_y[top_before])
        thr_e = hip_y[top_after] + 0.05 * (hip_y[p] - hip_y[top_after])
        # Start = the last moment the hips were still near the top before this
        # descent (searching back from the bottom), so a walk-out from the rack
        # or standing around before the rep is not counted as part of it.
        still_top = np.where(hip_y[top_before:p + 1] <= thr_s)[0]
        s = top_before + int(still_top[-1]) if still_top.size else top_before
        after = np.where(hip_y[p:top_after + 1] <= thr_e)[0]
        if not after.size:
            continue  # never came back up inside the video
        e = p + int(after[0])
        # A real rep takes time to go down and come up; a sub-0.3 s "dip" is a
        # tracking glitch (seen when the feet were cropped out of the picture).
        if (p - s) / fps < 0.3 or (e - p) / fps < 0.3:
            continue
        # A squat bends the knees: clearly bent at the bottom, nearly straight at
        # the top. A hip drop without that is the hip point jumping (seen when a
        # rack crossbar hid the hips).
        if knee_angle is not None and np.isfinite(knee_angle[p]):
            top_knee = max(np.nanmax(knee_angle[s:s + 3]), np.nanmax(knee_angle[max(e - 2, p):e + 1]))
            if knee_angle[p] > 135 or top_knee < 150:
                if rejected is not None and rom >= 0.6 * thigh:
                    rejected.append(p)
                continue
        reps.append((s, p, e))
        prev_end = e
    return reps


def _unsuitable(message: str, n: int, fps: float, extra: dict | None = None) -> dict:
    return {"status": "unsuitable", "message": message, "fps": fps, "frame_count": n,
            "reps": [], "frames": [], "issues": [{"code": "unsuitable", "message": message}], **(extra or {})}

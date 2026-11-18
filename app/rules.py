"""Evaluate measurements against the skill's criteria (criteria.yaml).

Result for each rep x criterion is one of:
    meets           - value is inside the target by more than the uncertainty band
    does_not_meet   - value is outside the target by more than the uncertainty band
    cannot_assess   - inputs not seen / not tracked, or value inside the band
                      around a limit (too close to call)

Numbers live in criteria.yaml, not here. Change a target there and the next
analysis uses it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent / "skill" / "squat-standard"

MEETS, FAILS, CANNOT = "meets", "does_not_meet", "cannot_assess"


def load_skill(skill_dir: Path = SKILL_DIR) -> dict[str, Any]:
    criteria = yaml.safe_load((skill_dir / "criteria.yaml").read_text(encoding="utf-8"))
    return {
        "criteria": criteria,
        "criteria_text": (skill_dir / "criteria.yaml").read_text(encoding="utf-8"),
        "instructions": (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
    }


def short_unit(unit: str) -> str:
    """'degrees between torso line (...)' -> 'degrees' (the readable part of a unit)."""
    m = re.match(r"(thigh lengths|foot lengths|degrees|seconds)", unit or "")
    return m.group(1) if m else (unit or "").split(" (")[0]


def _fmt_target(target: dict, unit: str) -> str:
    unit = short_unit(unit)
    lo, hi = target.get("min"), target.get("max")
    if lo is not None and hi is not None:
        return f"between {lo} and {hi} {unit}"
    if lo is not None:
        return f"more than {lo} {unit}"
    return f"at most {hi} {unit}"


def judge(value: float | None, target: dict, band: float) -> tuple[str, str]:
    if value is None:
        return CANNOT, "no measurement"
    lo, hi = target.get("min"), target.get("max")
    if (lo is not None and value < lo - band) or (hi is not None and value > hi + band):
        return FAILS, "outside the target"
    inside = (lo is None or value >= lo + band) and (hi is None or value <= hi - band)
    if inside:
        return MEETS, "inside the target"
    return CANNOT, "too close to the limit to call, given measurement uncertainty"


def evaluate(skill: dict, analysis: dict) -> dict[str, Any]:
    crit = skill["criteria"]["criteria"]
    fps = analysis.get("fps") or 30.0
    findings: list[dict] = []
    not_assessable: list[dict] = []

    for c in crit:
        if not c.get("assessable"):
            not_assessable.append({
                "criterion_id": c["id"], "title": c["title"], "requirement": c["requirement"],
                "sources": c["sources"], "result": CANNOT, "reason": c.get("reason", ""),
            })

    for rep in analysis.get("reps", []):
        for c in crit:
            if not c.get("assessable"):
                continue
            chk = c["check"]
            m = rep["metrics"].get(chk["metric"])
            unit = chk.get("unit", "")
            band = float(chk.get("uncertainty", 0)) + float((m or {}).get("extra_uncertainty", 0))
            if m is None or not m.get("observed", False) or m.get("value") is None:
                result, why = CANNOT, (m or {}).get("detail") or "The points needed for this check were not seen clearly."
                why = "Not measured reliably: " + why
                if m and m.get("value") is not None and not m.get("observed"):
                    why += " (inputs were hidden or estimated, so we won't judge it)"
            else:
                result, why = judge(float(m["value"]), chk["target"], band)
                why = f"{m['detail']} Target: {_fmt_target(chk['target'], unit)}; this is {why}."
            frame = (m or {}).get("frame")
            if frame is None:
                frame = rep["bottom_frame"]
            findings.append({
                "rep": rep["index"],
                "criterion_id": c["id"],
                "title": c["title"],
                "requirement": c["requirement"],
                "sources": c["sources"],
                "result": result,
                "frame": int(frame),
                "time_s": round(frame / fps, 2),
                "measurement": None if m is None else {
                    "value": m.get("value"), "unit": unit, "observed": m.get("observed"),
                    "extra": m.get("extra", {}),
                },
                "target": chk["target"],
                "target_text": _fmt_target(chk["target"], unit),
                "threshold_source": chk.get("source"),
                "threshold_note": chk.get("note", "").strip(),
                "uncertainty_band": round(band, 3),
                "explanation": why,
                "cues": c.get("cues_if_not_met", []) if result == FAILS else [],
            })

    counts = {k: sum(1 for f in findings if f["result"] == k) for k in (MEETS, FAILS, CANNOT)}
    return {
        "skill_version": skill["criteria"].get("version"),
        "findings": findings,
        "not_assessable": not_assessable,
        "counts": counts,
    }

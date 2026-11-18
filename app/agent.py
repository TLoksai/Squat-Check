"""The embedded coach: Claude reads the skill and writes the feedback.

The skill (SKILL.md + criteria.yaml) is the system prompt, so the coach works
from the reference document, not from general squat knowledge. Results are
fixed before the coach runs; the coach only explains them. Its output is
checked: feedback for a (rep, criterion) that doesn't exist, or a page number
the criterion doesn't cite, is dropped and replaced by the rulebook text.

If no API key is set or the call fails, the app falls back to the rulebook
cues directly and labels the feedback as "rulebook tips (AI coach offline)".
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

SCHEMA = {
    "type": "object",
    "properties": {
        "overall_summary": {"type": "string"},
        "top_priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_id": {"type": "string"},
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "cue": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["criterion_id", "title", "why", "cue", "page"],
                "additionalProperties": False,
            },
        },
        "findings_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rep": {"type": "integer"},
                    "criterion_id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "feedback": {"type": "string"},
                    "uncertainty": {"type": "string"},
                },
                "required": ["rep", "criterion_id", "explanation", "feedback", "uncertainty"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall_summary", "top_priorities", "findings_feedback"],
    "additionalProperties": False,
}


def _compact_results(evaluation: dict, analysis: dict) -> dict:
    """What the coach sees: results and evidence, no per-frame data."""
    return {
        "reps": [{"rep": r["index"], "start_s": r["start_s"], "bottom_s": r["bottom_s"], "end_s": r["end_s"]}
                 for r in analysis.get("reps", [])],
        "video_issues": analysis.get("issues", []),
        "torso_line_used": analysis.get("torso_line"),
        "findings": [
            {k: f[k] for k in ("rep", "criterion_id", "result", "time_s", "measurement", "target_text",
                               "threshold_source", "uncertainty_band", "explanation")}
            for f in evaluation["findings"]
        ],
        "not_assessable_from_side_view": [
            {"criterion_id": n["criterion_id"], "title": n["title"], "reason": n["reason"]}
            for n in evaluation["not_assessable"]
        ],
    }


def _system_prompt(skill: dict) -> str:
    return (skill["instructions"] + "\n\n---\n\n# criteria.yaml\n\n```yaml\n"
            + skill["criteria_text"] + "\n```\n")


def rulebook_feedback(evaluation: dict) -> dict:
    """Deterministic fallback straight from the skill's cues (no AI)."""
    fails = [f for f in evaluation["findings"] if f["result"] == "does_not_meet"]
    by_crit: dict[str, list] = {}
    for f in fails:
        by_crit.setdefault(f["criterion_id"], []).append(f)
    prios = []
    for cid, fs in sorted(by_crit.items(), key=lambda kv: -len(kv[1]))[:3]:
        cue = (fs[0]["cues"] or [{"text": fs[0]["requirement"], "page": fs[0]["sources"][0]["page"]}])[0]
        prios.append({"criterion_id": cid, "title": fs[0]["title"],
                      "why": f"Did not meet the standard in {len(fs)} rep(s).", "cue": cue["text"], "page": cue["page"]})
    c = evaluation["counts"]
    summary = (f"{c['meets']} checks met the standard, {c['does_not_meet']} did not, and {c['cannot_assess']} "
               f"could not be assessed from this video.")
    fb = []
    for f in evaluation["findings"]:
        if f["result"] == "does_not_meet" and f["cues"]:
            text = f"{f['cues'][0]['text']} (p. {f['cues'][0]['page']})"
        elif f["result"] == "meets":
            text = "Meets the standard."
        else:
            text = "Not judged. See the explanation for why."
        fb.append({"rep": f["rep"], "criterion_id": f["criterion_id"], "explanation": f["explanation"],
                   "feedback": text, "uncertainty": ""})
    return {"overall_summary": summary, "top_priorities": prios, "findings_feedback": fb,
            "source": "rulebook", "note": "Rulebook tips (AI coach offline or not configured)."}


def _validate(out: dict, evaluation: dict, skill: dict) -> dict:
    pages = {c["id"]: {s["page"] for s in c["sources"]} | {q["page"] for q in c.get("cues_if_not_met", [])}
             for c in skill["criteria"]["criteria"]}
    valid_keys = {(f["rep"], f["criterion_id"]) for f in evaluation["findings"]}
    fallback = {(x["rep"], x["criterion_id"]): x for x in rulebook_feedback(evaluation)["findings_feedback"]}
    dropped = 0
    clean = []
    seen = set()
    for item in out.get("findings_feedback", []):
        key = (item.get("rep"), item.get("criterion_id"))
        cited = {int(p) for p in re.findall(r"p\.\s*(\d+)", item.get("feedback", ""))}
        if key not in valid_keys or key in seen or not cited <= pages.get(key[1], set()):
            dropped += 1
            continue
        seen.add(key)
        clean.append(item)
    for key in valid_keys - seen:  # anything missing or rejected -> rulebook text
        clean.append(fallback[key])
    prios = [p for p in out.get("top_priorities", [])[:3]
             if p.get("criterion_id") in pages and p.get("page") in pages[p["criterion_id"]]]
    out["findings_feedback"] = clean
    out["top_priorities"] = prios
    out["validation"] = {"dropped_items": dropped}
    return out


def coach(skill: dict, evaluation: dict, analysis: dict) -> dict[str, Any]:
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return rulebook_feedback(evaluation)

    client = anthropic.Anthropic()
    system = [{"type": "text", "text": _system_prompt(skill), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content":
                 "Results for this video (JSON). Write the feedback as the skill describes.\n\n"
                 + json.dumps(_compact_results(evaluation, analysis), indent=1)}]
    output_config = {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}

    try:
        try:
            # Server-side fallback: if the model declines, the API retries on a fallback model.
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=system, messages=messages,
                output_config=output_config,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.BadRequestError:
            resp = client.messages.create(model=MODEL, max_tokens=16000, system=system,
                                          messages=messages, output_config=output_config)
        if resp.stop_reason == "refusal":
            out = rulebook_feedback(evaluation)
            out["note"] = "The AI coach declined this request; showing rulebook tips."
            return out
        text = next(b.text for b in resp.content if b.type == "text")
        out = _validate(json.loads(text), evaluation, skill)
        out["source"] = "claude"
        out["model"] = getattr(resp, "model", MODEL)
        u = resp.usage
        out["usage"] = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
        return out
    except (anthropic.APIError, StopIteration, json.JSONDecodeError, KeyError) as e:
        out = rulebook_feedback(evaluation)
        out["note"] = f"AI coach unavailable ({type(e).__name__}); showing rulebook tips."
        return out

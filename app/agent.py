"""The embedded coach: an LLM reads the skill and writes the feedback.

The skill (SKILL.md + criteria.yaml) is the system prompt, so the coach works
from the reference document, not from general squat knowledge. Results are
fixed before the coach runs; the coach only explains them. Its output is
checked: feedback for a (rep, criterion) that doesn't exist, or a page number
the criterion doesn't cite, is dropped and replaced by the rulebook text.

Providers (same skill, same output schema, same validator):
  - Gemini, when GEMINI_API_KEY is set (model: GEMINI_MODEL, else newest flash)
  - Claude, when ANTHROPIC_API_KEY is set (model: CLAUDE_MODEL, default claude-opus-5-5)
  COACH_PROVIDER=gemini|claude|rulebook forces one; with both keys, Claude wins.

If no key is set or the call fails, the app falls back to the rulebook cues
directly and labels the feedback as "rulebook tips (AI coach offline)".
"""

from __future__ import annotations

import json
import os
import re
import traceback
from typing import Any

from pathlib import Path

import anthropic
from dotenv import load_dotenv

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

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


def provider() -> str:
    """Which coach runs: COACH_PROVIDER if set, else whichever key is present."""
    p = os.getenv("COACH_PROVIDER", "").strip().lower()
    if p in ("claude", "gemini", "rulebook"):
        return p
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    return "rulebook"


def _user_prompt(evaluation: dict, analysis: dict) -> str:
    return ("Results for this video (JSON). Write the feedback as the skill describes.\n\n"
            + json.dumps(_compact_results(evaluation, analysis), indent=1))


def coach(skill: dict, evaluation: dict, analysis: dict) -> dict[str, Any]:
    load_dotenv(_ENV_FILE)  # pick up a key added to .env after the server started
    p = provider()
    if p == "gemini":
        return _coach_gemini(skill, evaluation, analysis)
    if p == "claude":
        return _coach_claude(skill, evaluation, analysis)
    return rulebook_feedback(evaluation)


# ------------------------------------------------------------------ Gemini

def _gemini_model(client) -> str:
    """GEMINI_MODEL if set; else the newest stable 'flash' model this key can use."""
    if os.getenv("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    try:
        names = []
        for m in client.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if (re.fullmatch(r"gemini-\d+(\.\d+)?-flash", name)
                    and (not actions or "generateContent" in actions)):
                names.append(name)
        if names:
            return max(names, key=lambda n: float(re.search(r"\d+(\.\d+)?", n).group()))
    except Exception:
        pass
    return "gemini-2.5-flash"


def _coach_gemini(skill: dict, evaluation: dict, analysis: dict) -> dict[str, Any]:
    from google import genai
    from google.genai import errors as gerrors
    from google.genai import types

    try:
        client = genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
        model = _gemini_model(client)
        base = dict(system_instruction=_system_prompt(skill), response_mime_type="application/json")
        try:
            resp = client.models.generate_content(
                model=model, contents=_user_prompt(evaluation, analysis),
                config=types.GenerateContentConfig(**base, response_json_schema=SCHEMA))
        except gerrors.ClientError as e:
            if getattr(e, "code", None) != 400:
                raise
            # Schema not accepted by this model: ask for JSON only; our validator checks the shape.
            resp = client.models.generate_content(
                model=model, contents=_user_prompt(evaluation, analysis),
                config=types.GenerateContentConfig(**base))
        if not resp.text:
            out = rulebook_feedback(evaluation)
            out["note"] = "The AI coach returned no text (possibly blocked); showing rulebook tips."
            return out
        out = _validate(json.loads(resp.text), evaluation, skill)
        out["source"] = "gemini"
        out["model"] = model
        u = resp.usage_metadata
        if u:
            out["usage"] = {"input_tokens": u.prompt_token_count or 0,
                            "output_tokens": (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0),
                            "cache_read_input_tokens": getattr(u, "cached_content_token_count", 0) or 0}
        return out
    except (gerrors.APIError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        traceback.print_exc()  # full detail in the server log; the user sees a plain note
        out = rulebook_feedback(evaluation)
        out["note"] = f"AI coach unavailable ({type(e).__name__}); showing rulebook tips."
        return out


# ------------------------------------------------------------------ Claude

def _coach_claude(skill: dict, evaluation: dict, analysis: dict) -> dict[str, Any]:
    client = anthropic.Anthropic()
    system = [{"type": "text", "text": _system_prompt(skill), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": _user_prompt(evaluation, analysis)}]
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
        traceback.print_exc()
        out = rulebook_feedback(evaluation)
        out["note"] = f"AI coach unavailable ({type(e).__name__}); showing rulebook tips."
        return out

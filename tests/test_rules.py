"""Unit tests for the parts that decide results: the judge, the skill file, and
the coach-output validator. Run: python -m pytest -q"""

import copy

from app import agent
from app.rules import CANNOT, FAILS, MEETS, judge, load_skill


def test_judge_band_logic():
    t = {"min": 35, "max": 55}
    assert judge(45, t, 3)[0] == MEETS
    assert judge(53, t, 3)[0] == CANNOT     # inside target but within the band of the limit
    assert judge(57, t, 3)[0] == CANNOT     # outside target but within the band
    assert judge(60, t, 3)[0] == FAILS
    assert judge(None, t, 3)[0] == CANNOT
    assert judge(-0.12, {"min": 0}, 0.05)[0] == FAILS
    assert judge(0.10, {"min": 0}, 0.05)[0] == MEETS


def test_skill_file_is_complete():
    skill = load_skill()
    crit = skill["criteria"]["criteria"]
    ids = [c["id"] for c in crit]
    assert len(ids) == len(set(ids)), "duplicate criterion ids"
    for c in crit:
        assert c["sources"], f"{c['id']} has no page citation"
        for s in c["sources"]:
            assert 1 <= s["page"] <= 35, f"{c['id']} cites page {s['page']} outside the reference"
        if c["assessable"]:
            chk = c["check"]
            assert chk["source"] in ("document", "app_interpretation")
            assert "min" in chk["target"] or "max" in chk["target"]
            for cue in c.get("cues_if_not_met", []):
                assert 1 <= cue["page"] <= 35
        else:
            assert c.get("reason"), f"{c['id']} not assessable but no reason given"


def _evaluation():
    skill = load_skill()
    depth = next(c for c in skill["criteria"]["criteria"] if c["id"] == "depth")
    f = {"rep": 1, "criterion_id": "depth", "title": depth["title"], "requirement": depth["requirement"],
         "sources": depth["sources"], "result": FAILS, "explanation": "hip above knee",
         "cues": depth["cues_if_not_met"]}
    return skill, {"findings": [f], "not_assessable": [], "counts": {MEETS: 0, FAILS: 1, CANNOT: 0}}


def test_validator_drops_invented_pages_and_unknown_findings():
    skill, ev = _evaluation()
    out = {
        "overall_summary": "x",
        "top_priorities": [{"criterion_id": "depth", "title": "Depth", "why": "", "cue": "go deeper", "page": 99}],
        "findings_feedback": [
            {"rep": 1, "criterion_id": "depth", "explanation": "e", "feedback": "Go deeper (p. 99)", "uncertainty": ""},
            {"rep": 7, "criterion_id": "depth", "explanation": "e", "feedback": "made up rep", "uncertainty": ""},
        ],
    }
    clean = agent._validate(copy.deepcopy(out), ev, skill)
    assert clean["top_priorities"] == []                       # page 99 isn't cited by the skill
    assert len(clean["findings_feedback"]) == 1                # rep 7 dropped, rep 1 replaced
    assert "(p. 99)" not in clean["findings_feedback"][0]["feedback"]
    assert clean["validation"]["dropped_items"] == 2


def test_validator_keeps_grounded_feedback():
    skill, ev = _evaluation()
    out = {"overall_summary": "x", "top_priorities": [],
           "findings_feedback": [{"rep": 1, "criterion_id": "depth", "explanation": "e",
                                  "feedback": "Shove your knees out (p. 33)", "uncertainty": ""}]}
    clean = agent._validate(out, ev, skill)
    assert clean["findings_feedback"][0]["feedback"] == "Shove your knees out (p. 33)"


def test_rulebook_fallback_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    skill, ev = _evaluation()
    out = agent.coach(skill, ev, {"reps": [], "issues": []})
    assert out["source"] == "rulebook"
    assert out["top_priorities"][0]["criterion_id"] == "depth"

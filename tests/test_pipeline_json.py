"""Regression test: the coach prompt must serialise the real pipeline output.

The first live run with an API key failed with a TypeError because the
measurements still held numpy numbers when the coach turned them into JSON.
"""

import json

import numpy as np

from app import agent
from app.pipeline import _to_builtin


def test_coach_prompt_serialises_numpy_after_conversion():
    evaluation = {"findings": [{"rep": np.int64(1), "criterion_id": "depth", "result": "meets",
                                "time_s": np.float32(1.5), "measurement": {"value": np.float64(0.1)},
                                "target_text": "x", "threshold_source": "document",
                                "uncertainty_band": np.float64(0.05), "explanation": "e"}],
                  "not_assessable": []}
    analysis = {"reps": [{"index": 1, "start_s": np.float64(0.1), "bottom_s": 1.0, "end_s": 2.0}],
                "issues": [], "torso_line": "hip to shoulder"}
    evaluation = json.loads(json.dumps(evaluation, default=_to_builtin))
    analysis = json.loads(json.dumps(analysis, default=_to_builtin))
    assert "depth" in agent._user_prompt(evaluation, analysis)

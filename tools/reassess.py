"""Re-check finished analyses against the CURRENT rulebook, without re-tracking.

    python -m tools.reassess data/cli/common_sample [more job dirs...]

Same as the "Re-check with current rulebook" button in the app.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

from app.pipeline import reassess

load_dotenv()

for d in sys.argv[1:]:
    res = reassess(Path(d))
    print(f"== {d}  (skill v{res['skill']['version']}, coach: {res['feedback'].get('source')})")
    for f in res["evaluation"]["findings"]:
        v = f["measurement"]["value"] if f["measurement"] else None
        print(f"  r{f['rep']} {f['criterion_id']:<20} {f['result']:<14} value={v} band={f['uncertainty_band']}")
    print("  counts:", res["evaluation"]["counts"])

"""Run the full pipeline on a file without the web server and print the findings.

    python -m tools.analyse_file samples/common_sample.mp4 [out_dir]
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

from app.pipeline import run

load_dotenv()


def main() -> None:
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/cli") / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = set()

    def progress(stage, pct, msg):
        if stage not in seen:
            seen.add(stage)
            print(f"{pct * 100:5.1f}%  {msg}")

    res = run(out_dir, src, progress)
    a = res["analysis"]
    print("status:", a["status"], "| side:", a.get("side"), "| facing:", a.get("facing"),
          "| bar detected:", a.get("bar_detected_ratio"), "| torso:", a.get("torso_line"))
    print("camera:", a.get("camera"), "| scale:", a.get("scale"))
    for i in a.get("issues", []):
        print("ISSUE:", i["message"])
    for r in a.get("reps", []):
        print(f"rep {r['index']}: {r['start_s']}s -> bottom {r['bottom_s']}s -> {r['end_s']}s  bar det {r['bar_detected_ratio']}")
    for f in res["evaluation"]["findings"]:
        v = f["measurement"]["value"] if f["measurement"] else None
        print(f"  r{f['rep']} {f['criterion_id']:<20} {f['result']:<14} value={v} target={f['target_text']} "
              f"band={f['uncertainty_band']}")
    fb = res["feedback"]
    print("coach:", fb.get("source"), fb.get("model", ""), fb.get("note", ""), fb.get("usage", ""))
    print("summary:", fb.get("overall_summary"))
    for p in fb.get("top_priorities", []):
        print("  priority:", p)
    print("timings:", res["timings"])


if __name__ == "__main__":
    main()

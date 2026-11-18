"""Turn the raw recording into the final demo: speed up the waiting parts
logged in demo/timeline.json and encode an H.264 MP4.

    python -m tools.edit_demo        # -> demo/squat-check-demo.mp4
"""

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo"
FF = imageio_ffmpeg.get_ffmpeg_exe()


def duration(path: Path) -> float:
    r = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError("no duration")


def main() -> None:
    raw = sorted((OUT / "raw").glob("*.webm"))[-1]
    events = json.loads((OUT / "timeline.json").read_text(encoding="utf-8"))
    total = duration(raw)
    # build (start, end, factor) segments
    segs, cur, fast = [], 0.0, None
    for ev in events:
        if ev["kind"] == "fast_start":
            segs.append((cur, ev["t"], 1.0))
            cur, fast = ev["t"], ev.get("factor", 6)
        elif ev["kind"] == "fast_end" and fast:
            segs.append((cur, ev["t"], float(fast)))
            cur, fast = ev["t"], None
    segs.append((cur, total, 1.0))
    segs = [(a, min(b, total), f) for a, b, f in segs if b - a > 0.05]

    parts = []
    filt = []
    for i, (a, b, f) in enumerate(segs):
        filt.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{f},fps=30[v{i}]")
        parts.append(f"[v{i}]")
    filt.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0,format=yuv420p[out]")
    out = OUT / "squat-check-demo.mp4"
    cmd = [FF, "-y", "-v", "error", "-i", str(raw), "-filter_complex", ";".join(filt), "-map", "[out]",
           "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print(f"raw {total:.1f}s -> {out} ({duration(out):.1f}s, {out.stat().st_size / 1e6:.1f} MB)")
    for a, b, f in segs:
        print(f"  {a:6.1f}-{b:6.1f}s  x{f:g}")


if __name__ == "__main__":
    main()

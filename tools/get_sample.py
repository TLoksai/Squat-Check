"""Download the assignment's common sample video to samples/common_sample.mp4.

    python -m tools.get_sample
"""

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

URL = "https://youtube.com/shorts/TRvg083BrXY"  # link from AI_Engineer_Hiring_Assignment.pdf, page 1
OUT = Path(__file__).resolve().parent.parent / "samples" / "common_sample.mp4"


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "yt_dlp", "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
                    "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", "--merge-output-format", "mp4",
                    "-o", str(OUT), URL], check=True)
    print("saved", OUT)


if __name__ == "__main__":
    main()

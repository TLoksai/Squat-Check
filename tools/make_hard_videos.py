"""Make difficult / unsuitable test clips from the common sample (ffmpeg).

Each one checks a specific failure path, so we can see the app degrade
honestly instead of inventing numbers:

  mirrored.mp4      lifter faces the other way -> results should match the original
  feet_cropped.mp4  bottom of the picture cut off -> foot-based checks must not pass/fail
  dark_blurry.mp4   dark, blurred, low resolution -> tracking quality drops
  no_squat.mp4      a still standing clip with no rep -> "no rep found" message

    python -m tools.make_hard_videos
"""

import subprocess
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "samples" / "common_sample.mp4"
OUT = ROOT / "samples" / "hard"

CLIPS = {
    "mirrored.mp4": ["-vf", "hflip"],
    "feet_cropped.mp4": ["-vf", "crop=iw:ih*0.86:0:0"],
    "dark_blurry.mp4": ["-vf", "scale=iw/3:-2,boxblur=3:1,eq=brightness=-0.25:contrast=0.7"],
    "no_squat.mp4": ["-t", "3", "-vf", "select='eq(n\\,0)',loop=90:1:0,setpts=N/30/TB"],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, args in CLIPS.items():
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(SRC), *args,
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(OUT / name)]
        subprocess.run(cmd, check=True)
        print("made", OUT / name)


if __name__ == "__main__":
    main()

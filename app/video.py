"""Video intake: validate the upload and normalise it with ffmpeg.

Every uploaded file is re-encoded once to H.264 MP4, 30 fps, longest side at
most 1280 px, no audio. Analysis and the browser player both use this same
file, so overlay coordinates always line up with what the user sees (phone
rotation, odd codecs and variable frame rates are handled here, once).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import imageio_ffmpeg

# Limits shown in the UI (app choices, not document rules).
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
MAX_UPLOAD_MB = 200
MAX_DURATION_S = 60
MIN_DURATION_S = 2
TARGET_FPS = 30
MAX_SIDE_PX = 1280


class VideoError(Exception):
    """A problem with the user's video that we can explain to them."""


@dataclass
class VideoInfo:
    path: Path
    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    def to_dict(self) -> dict:
        return {
            "fps": self.fps,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "duration_s": round(self.duration_s, 2),
        }


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoError("We couldn't open this file as a video. Try an MP4 or MOV file.")
    info = VideoInfo(
        path=path,
        fps=cap.get(cv2.CAP_PROP_FPS) or 0.0,
        frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
    )
    cap.release()
    return info


def normalise(src: Path, dst: Path) -> VideoInfo:
    """Re-encode to a browser-safe, analysis-friendly MP4."""
    scale = (
        f"scale='if(gt(iw,ih),min({MAX_SIDE_PX},iw),-2)':'if(gt(iw,ih),-2,min({MAX_SIDE_PX},ih))',"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd = [
        ffmpeg_exe(), "-y", "-v", "error",
        "-i", str(src),
        "-t", str(MAX_DURATION_S + 1),
        "-vf", f"{scale},fps={TARGET_FPS}",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists():
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise VideoError(f"We couldn't read this video ({detail[0]}). Try exporting it as MP4.")

    info = probe(dst)
    if info.frame_count == 0:
        raise VideoError("The video has no frames we can read.")
    if info.duration_s < MIN_DURATION_S:
        raise VideoError(f"The video is too short ({info.duration_s:.1f} s). Record at least one full rep.")
    return info


def source_duration_s(path: Path) -> float | None:
    """Duration of the original upload, used to reject over-long clips early."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return n / fps if fps else None


def limits() -> dict:
    return {
        "formats": sorted(e.lstrip(".") for e in ALLOWED_EXTENSIONS),
        "max_upload_mb": MAX_UPLOAD_MB,
        "max_duration_s": MAX_DURATION_S,
        "min_duration_s": MIN_DURATION_S,
    }


if __name__ == "__main__":  # quick manual check: python -m app.video in.mp4 out.mp4
    import sys

    print(json.dumps(normalise(Path(sys.argv[1]), Path(sys.argv[2])).to_dict(), indent=2))

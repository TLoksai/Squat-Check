"""Web server: upload -> background analysis -> status polling -> results.

Run:  uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .rules import SKILL_DIR, load_skill
from .video import ALLOWED_EXTENSIONS, MAX_DURATION_S, MAX_UPLOAD_MB, VideoError, limits, source_duration_s

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "jobs"
WEB = ROOT / "web"
SAMPLE = ROOT / "samples" / "common_sample.mp4"
DATA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Squat Check")
executor = ThreadPoolExecutor(max_workers=1)  # one analysis at a time: it is CPU-heavy
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _set(job_id: str, **kw) -> None:
    with _lock:
        _jobs.setdefault(job_id, {}).update(kw)
        snapshot = dict(_jobs[job_id])
    (DATA / job_id / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")


def _get(job_id: str) -> dict | None:
    with _lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    f = DATA / job_id / "status.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _work(job_id: str, src: Path) -> None:
    job_dir = DATA / job_id
    try:
        dur = source_duration_s(src)
        if dur is not None and dur > MAX_DURATION_S + 0.5:
            raise VideoError(f"The video is {dur:.0f} s long. Please trim it to {MAX_DURATION_S} s or less "
                             "(a few reps is plenty).")
        _set(job_id, status="processing", stage="prepare", progress=0.0, message="Preparing your video")
        pipeline.run(job_dir, src, lambda k, p, m: _set(job_id, stage=k, progress=round(p, 3), message=m))
        _set(job_id, status="done", progress=1.0, message="Done")
    except VideoError as e:
        _set(job_id, status="failed", error=str(e), message="We couldn't use this video")
    except Exception as e:  # unexpected: keep the details in the log, show a plain message
        traceback.print_exc()
        _set(job_id, status="failed", error=f"Something went wrong while analysing the video ({type(e).__name__}). "
                                             "Please try again or try another video.",
             message="Analysis failed")
    finally:
        src.unlink(missing_ok=True)  # keep only the normalised copy


def _new_job(filename: str) -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    (DATA / job_id).mkdir(parents=True)
    _set(job_id, id=job_id, status="queued", progress=0.0, message="Waiting to start", filename=filename)
    return job_id, DATA / job_id


@app.get("/api/config")
def config() -> dict:
    skill = load_skill()
    crit = skill["criteria"]
    return {
        "limits": limits(),
        "skill": {
            "name": crit.get("skill"), "version": crit.get("version"), "source": crit.get("source"),
            "assessable": [{"id": c["id"], "title": c["title"]} for c in crit["criteria"] if c.get("assessable")],
            "not_assessable": [{"id": c["id"], "title": c["title"]} for c in crit["criteria"] if not c.get("assessable")],
        },
        "sample_available": SAMPLE.exists(),
    }


@app.get("/api/skill", response_class=PlainTextResponse)
def skill_text() -> str:
    return (SKILL_DIR / "criteria.yaml").read_text(encoding="utf-8")


@app.post("/api/jobs")
async def create_job(file: UploadFile, request: Request) -> dict:
    name = file.filename or "video"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"'{ext or 'no extension'}' files aren't supported. "
                                 f"Use one of: {', '.join(sorted(e.lstrip('.') for e in ALLOWED_EXTENSIONS))}.")
    job_id, job_dir = _new_job(name)
    dst = job_dir / f"upload{ext}"
    size = 0
    with dst.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"The file is larger than {MAX_UPLOAD_MB} MB.")
            out.write(chunk)
    if size == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "The file is empty.")
    executor.submit(_work, job_id, dst)
    return {"id": job_id}


@app.post("/api/jobs/sample")
def create_sample_job() -> dict:
    if not SAMPLE.exists():
        raise HTTPException(404, "The sample video isn't installed on this server.")
    job_id, job_dir = _new_job("common_sample.mp4")
    dst = job_dir / "upload.mp4"
    shutil.copy(SAMPLE, dst)
    executor.submit(_work, job_id, dst)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    st = _get(job_id) if job_id.isalnum() else None
    if not st:
        raise HTTPException(404, "Unknown job")
    return st


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    f = DATA / job_id / "result.json"
    if not job_id.isalnum() or not f.exists():
        raise HTTPException(404, "Result not ready")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    f = DATA / job_id / "video.mp4"
    if not job_id.isalnum() or not f.exists():
        raise HTTPException(404, "Video not ready")
    return FileResponse(f, media_type="video/mp4")


@app.post("/api/jobs/{job_id}/reassess")
def job_reassess(job_id: str):
    """Re-check an analysed video against the current rulebook (criteria.yaml) without re-tracking."""
    job_dir = DATA / job_id
    if not job_id.isalnum() or not (job_dir / "landmarks.npy").exists():
        raise HTTPException(404, "This analysis can't be re-checked (it was made before re-check was available).")
    try:
        out = pipeline.reassess(job_dir)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(400, f"Re-check failed: {type(e).__name__}: {e}. Is criteria.yaml valid?")
    return JSONResponse(out)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if not job_id.isalnum() or not (DATA / job_id).exists():
        raise HTTPException(404, "Unknown job")
    shutil.rmtree(DATA / job_id, ignore_errors=True)
    with _lock:
        _jobs.pop(job_id, None)
    return {"deleted": job_id}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

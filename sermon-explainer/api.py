"""
HTTP API for Sermon Explainer.

This wraps the existing CLI pipeline into job-based endpoints so a frontend
can submit generation requests, poll status, and download final artifacts.
"""

import os
import re
import uuid
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from steps.fetch_source import fetch_source
from steps.transcribe import transcribe
from steps.summarize import summarize
from steps.narrate import narrate
from steps.illustrate import illustrate
from steps.assemble_video import assemble_video
from steps.background_music import generate_background_music

load_dotenv()


class SocialLinks(BaseModel):
    facebook: Optional[str] = ""
    youtube: Optional[str] = ""
    x: Optional[str] = ""
    instagram: Optional[str] = ""
    tiktok: Optional[str] = ""


class JobOptions(BaseModel):
    enable_music: bool = True
    burn_captions: bool = True


class CreateJobRequest(BaseModel):
    source: str = Field(..., description="YouTube URL or local server path")
    title: Optional[str] = None
    outro_text: Optional[str] = "Follow us for more sermon explainers"
    intro_seconds: Optional[float] = 8.0
    outro_seconds: Optional[float] = 8.0
    socials: Optional[SocialLinks] = None
    confirm_rights: bool = False
    options: Optional[JobOptions] = None


class ErrorPayload(BaseModel):
    error_code: str
    message: str
    retryable: bool
    details: Optional[str] = None


class CreateJobResponse(BaseModel):
    job_id: str
    status: str
    created_at: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: Optional[str]
    progress_percent: int
    message: Optional[str]
    created_at: str
    updated_at: str
    error: Optional[ErrorPayload] = None


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    outputs: Dict[str, object]


class UploadSourceResponse(BaseModel):
    source: str
    filename: str
    size_bytes: int


app = FastAPI(title="Sermon Explainer API", version="1.0.0")

ALLOWED_ORIGINS = ["https://sermon-explainer.vercel.app"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: Dict[str, dict] = {}
JOBS_LOCK = Lock()
UPLOAD_DIR = os.path.join("input", "uploads")
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_upload_basename(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    return cleaned or "upload"


def _derive_title(source: str, explicit_title: Optional[str]) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()

    if source.startswith("http://") or source.startswith("https://"):
        return "Sermon Explainer"

    stem = os.path.splitext(os.path.basename(source))[0].replace("_", " ").replace("-", " ").strip()
    return stem or "Sermon Explainer"


def _set_job_state(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        if job_id not in JOBS:
            return
        JOBS[job_id].update(updates)
        JOBS[job_id]["updated_at"] = _now_iso()


def _parse_error(exc: Exception) -> ErrorPayload:
    message = str(exc)

    if isinstance(exc, PermissionError):
        return ErrorPayload(
            error_code="PERMISSION_NOT_CONFIRMED",
            message=message,
            retryable=False,
        )

    if isinstance(exc, FileNotFoundError):
        return ErrorPayload(
            error_code="SOURCE_NOT_FOUND",
            message=message,
            retryable=False,
        )

    if isinstance(exc, EnvironmentError):
        return ErrorPayload(
            error_code="MISSING_CONFIGURATION",
            message=message,
            retryable=False,
        )

    return ErrorPayload(
        error_code="PIPELINE_FAILED",
        message=message,
        retryable=True,
        details=traceback.format_exc(limit=3),
    )


def _run_pipeline(job_id: str, request: CreateJobRequest) -> None:
    try:
        options = request.options or JobOptions()

        _set_job_state(job_id, status="running", stage="fetch_source", progress_percent=5, message="Fetching source")
        source_info = fetch_source(
            request.source,
            permission_confirmed=request.confirm_rights if request.source.startswith(("http://", "https://")) else None,
        )

        _set_job_state(job_id, stage="transcribe", progress_percent=20, message="Transcribing audio")
        transcript = transcribe(source_info["path"])

        _set_job_state(job_id, stage="summarize", progress_percent=35, message="Summarizing transcript")
        scenes = summarize(transcript["full_text"], source_info["target_minutes"])

        _set_job_state(job_id, stage="narrate", progress_percent=50, message="Generating narration")
        scenes = narrate(scenes)

        _set_job_state(job_id, stage="illustrate", progress_percent=65, message="Generating illustrations")
        scenes = illustrate(scenes)

        _set_job_state(job_id, stage="assemble_base", progress_percent=80, message="Building base video")
        base_result = assemble_video(scenes, render_final=False)

        if options.enable_music:
            _set_job_state(job_id, stage="music", progress_percent=88, message="Generating background music")
            music_status = generate_background_music(target_duration_seconds=base_result["duration_seconds"])
        else:
            _set_job_state(job_id, stage="music", progress_percent=88, message="Skipping background music (disabled)")
            music_status = {
                "enabled": False,
                "music_path": None,
                "reason": "user_disabled",
            }

        _set_job_state(job_id, stage="finalize", progress_percent=95, message="Rendering final video")
        final_result = assemble_video(
            scenes,
            base_video_path=base_result["video_path"],
            base_srt_path=base_result["srt_path"],
            music_path=music_status.get("music_path"),
            render_final=True,
            burn_captions=options.burn_captions,
            intro_title=_derive_title(request.source, request.title),
            outro_text=request.outro_text,
            intro_duration_seconds=request.intro_seconds,
            outro_duration_seconds=request.outro_seconds,
            social_links=(request.socials.model_dump() if request.socials else {}),
        )

        outputs = {
            "video_path": final_result["video_path"],
            "srt_path": final_result["srt_path"],
            "final_video_path": final_result["final_video_path"],
            "duration_seconds": final_result["duration_seconds"],
            "total_duration_seconds": final_result["duration_seconds"],
            "source_requested": request.source,
            "source_resolved_path": source_info["path"],
            "source_kind": "url" if request.source.startswith(("http://", "https://")) else "file",
            "music_enabled": bool(music_status.get("enabled")),
            "background_music_used": bool(music_status.get("enabled")),
            "music_reason": music_status.get("reason"),
            "options_effective": {
                "enable_music": bool(options.enable_music),
                "burn_captions": bool(options.burn_captions),
            },
            "downloads": {
                "base_video": f"/jobs/{job_id}/artifacts/base_video",
                "subtitles": f"/jobs/{job_id}/artifacts/subtitles",
                "final_video": f"/jobs/{job_id}/artifacts/final_video",
            },
        }

        _set_job_state(
            job_id,
            status="completed",
            stage="completed",
            progress_percent=100,
            message="Completed",
            result=outputs,
            error=None,
        )
    except Exception as exc:
        error_payload = _parse_error(exc)
        _set_job_state(
            job_id,
            status="failed",
            stage="failed",
            progress_percent=100,
            message=error_payload.message,
            error=error_payload.model_dump(),
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/uploads", response_model=UploadSourceResponse)
async def upload_source(file: UploadFile = File(...)) -> UploadSourceResponse:
    filename = file.filename or ""
    extension = os.path.splitext(filename)[1].lower()

    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "error_code": "UNSUPPORTED_MEDIA_TYPE",
                "message": "Unsupported file type. Upload an audio or video file (for example: .mp3, .wav, .m4a, .mp4, .mov).",
                "retryable": False,
            },
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_base = _safe_upload_basename(filename)
    saved_name = f"{uuid.uuid4().hex}_{safe_base}{extension}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)

    size_bytes = 0
    try:
        with open(saved_path, "wb") as output_file:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error_code": "FILE_TOO_LARGE",
                            "message": "File is too large. Maximum upload size is 500 MB.",
                            "retryable": False,
                        },
                    )
                output_file.write(chunk)
    except Exception:
        if os.path.exists(saved_path):
            os.remove(saved_path)
        raise
    finally:
        await file.close()

    if size_bytes == 0:
        if os.path.exists(saved_path):
            os.remove(saved_path)
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "EMPTY_UPLOAD",
                "message": "Uploaded file was empty.",
                "retryable": False,
            },
        )

    return UploadSourceResponse(source=saved_path, filename=filename, size_bytes=size_bytes)


@app.post("/jobs", response_model=CreateJobResponse)
def create_job(request: CreateJobRequest) -> CreateJobResponse:
    if request.source.startswith(("http://", "https://")) and not request.confirm_rights:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "PERMISSION_NOT_CONFIRMED",
                "message": "Set confirm_rights=true to confirm you have permission to use this YouTube source.",
                "retryable": False,
            },
        )

    job_id = str(uuid.uuid4())
    now = _now_iso()

    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress_percent": 0,
            "message": "Queued",
            "created_at": now,
            "updated_at": now,
            "request": request.model_dump(),
            "result": None,
            "error": None,
        }

    Thread(target=_run_pipeline, args=(job_id, request), daemon=True).start()

    return CreateJobResponse(job_id=job_id, status="queued", created_at=now)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    with JOBS_LOCK:
        job = deepcopy(JOBS.get(job_id))

    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "JOB_NOT_FOUND",
                "message": f"No job found for id '{job_id}'.",
                "retryable": False,
            },
        )

    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        stage=job.get("stage"),
        progress_percent=job.get("progress_percent", 0),
        message=job.get("message"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        error=ErrorPayload(**job["error"]) if job.get("error") else None,
    )


@app.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str) -> JobResultResponse:
    with JOBS_LOCK:
        job = deepcopy(JOBS.get(job_id))

    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "JOB_NOT_FOUND",
                "message": f"No job found for id '{job_id}'.",
                "retryable": False,
            },
        )

    if job["status"] != "completed":
        if job["status"] == "failed":
            raise HTTPException(
                status_code=422,
                detail=job.get("error") or {
                    "error_code": "PIPELINE_FAILED",
                    "message": "Job failed.",
                    "retryable": True,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "JOB_NOT_COMPLETED",
                "message": f"Job is currently '{job['status']}'.",
                "retryable": True,
            },
        )

    return JobResultResponse(job_id=job_id, status="completed", outputs=job["result"])


@app.get("/jobs/{job_id}/artifacts/{artifact}")
def download_artifact(job_id: str, artifact: str):
    allowed = {
        "base_video": "video_path",
        "subtitles": "srt_path",
        "final_video": "final_video_path",
    }

    if artifact not in allowed:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "OUTPUT_NOT_FOUND",
                "message": f"Unknown artifact '{artifact}'.",
                "retryable": False,
            },
        )

    with JOBS_LOCK:
        job = deepcopy(JOBS.get(job_id))

    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "JOB_NOT_FOUND",
                "message": f"No job found for id '{job_id}'.",
                "retryable": False,
            },
        )

    if job.get("status") != "completed" or not job.get("result"):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "JOB_NOT_COMPLETED",
                "message": "Artifact not available until job completes.",
                "retryable": True,
            },
        )

    path_key = allowed[artifact]
    file_path = job["result"].get(path_key)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "OUTPUT_NOT_FOUND",
                "message": f"Artifact '{artifact}' not found on disk.",
                "retryable": False,
            },
        )

    return FileResponse(path=file_path, filename=os.path.basename(file_path))

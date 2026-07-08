"""
HTTP API for Sermon Explainer.

This wraps the existing CLI pipeline into job-based endpoints so a frontend
can submit generation requests, poll status, and download final artifacts.
"""

import os
import re
import subprocess
import time
import uuid
import traceback
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event, Lock, Semaphore, Thread
from typing import Deque, Dict, Optional

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
    queue_position: Optional[int] = None
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
JOB_QUEUE: Deque[str] = deque()
JOB_QUEUE_LOCK = Lock()
JOB_QUEUE_EVENT = Event()

MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
MAX_JOB_DURATION_MINUTES = float(os.environ.get("MAX_JOB_DURATION_MINUTES", "45"))
JOB_SLOTS = Semaphore(MAX_CONCURRENT_JOBS)

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


def _log_job(job_id: str, message: str) -> None:
    print(f"[job:{job_id}] {message}")


def create_job(request: CreateJobRequest) -> str:
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
            "cancel_requested": False,
            "processing_started_at": None,
        }
    _log_job(job_id, "created")
    return job_id


def _get_job_snapshot(job_id: str) -> Optional[dict]:
    with JOBS_LOCK:
        record = JOBS.get(job_id)
        return deepcopy(record) if record else None


def _get_queue_position(job_id: str) -> Optional[int]:
    with JOB_QUEUE_LOCK:
        for index, queued_job_id in enumerate(JOB_QUEUE, start=1):
            if queued_job_id == job_id:
                return index
    return None


def get_job_status(job_id: str) -> Optional[dict]:
    job = _get_job_snapshot(job_id)
    if not job:
        return None

    if job.get("status") == "queued":
        job["queue_position"] = _get_queue_position(job_id)
        if job["queue_position"] is not None:
            job["message"] = f"Queued (position {job['queue_position']})"

    return job


def update_job_status(job_id: str, **updates) -> None:
    force = bool(updates.pop("_force", False))
    terminal_statuses = {"completed", "failed"}

    with JOBS_LOCK:
        if job_id not in JOBS:
            return
        current_status = JOBS[job_id].get("status")
        if current_status in terminal_statuses and not force:
            return
        JOBS[job_id].update(updates)
        JOBS[job_id]["updated_at"] = _now_iso()


def mark_job_complete(job_id: str, outputs: Dict[str, object]) -> None:
    update_job_status(
        job_id,
        status="completed",
        stage="completed",
        progress_percent=100,
        message="Completed",
        result=outputs,
        error=None,
        _force=True,
    )
    _log_job(job_id, "completed")


def mark_job_failed(job_id: str, error: ErrorPayload) -> None:
    existing = _get_job_snapshot(job_id)
    if not existing:
        return
    if existing.get("status") == "completed":
        return

    update_job_status(
        job_id,
        status="failed",
        stage="failed",
        progress_percent=100,
        message=error.message,
        error=error.model_dump(),
        _force=True,
    )
    _log_job(job_id, f"failed: {error.error_code} - {error.message}")


def _get_job_request(job_id: str) -> Optional[CreateJobRequest]:
    job = _get_job_snapshot(job_id)
    if not job:
        return None
    request_data = job.get("request")
    if not request_data:
        return None
    return CreateJobRequest(**request_data)


def _is_cancel_requested(job_id: str) -> bool:
    job = _get_job_snapshot(job_id)
    return bool(job and job.get("cancel_requested"))


def _request_job_cancel(job_id: str, reason: str) -> None:
    update_job_status(job_id, cancel_requested=True, message=reason)
    _log_job(job_id, f"cancel requested: {reason}")


def _enqueue_job(job_id: str) -> None:
    with JOB_QUEUE_LOCK:
        JOB_QUEUE.append(job_id)
        queue_position = len(JOB_QUEUE)
    _log_job(job_id, f"queued (position={queue_position})")
    JOB_QUEUE_EVENT.set()


def _dequeue_next_job_if_slot_available() -> Optional[str]:
    if not JOB_SLOTS.acquire(blocking=False):
        return None

    with JOB_QUEUE_LOCK:
        if not JOB_QUEUE:
            JOB_SLOTS.release()
            return None
        return JOB_QUEUE.popleft()


def _release_slot(job_id: str) -> None:
    JOB_SLOTS.release()
    _log_job(job_id, "slot released")
    JOB_QUEUE_EVENT.set()


def _kill_job_children(job_id: str) -> None:
    # Best-effort child process cleanup for timeout handling, especially ffmpeg.
    try:
        subprocess.run(["pkill", "-TERM", "-P", str(os.getpid()), "ffmpeg"], check=False)
        time.sleep(0.2)
        subprocess.run(["pkill", "-KILL", "-P", str(os.getpid()), "ffmpeg"], check=False)
        _log_job(job_id, "sent TERM/KILL to ffmpeg child processes")
    except Exception as exc:
        _log_job(job_id, f"child process cleanup warning: {exc}")


def _job_timeout_watchdog(job_id: str, stop_event: Event) -> None:
    timeout_seconds = max(1.0, MAX_JOB_DURATION_MINUTES * 60.0)
    if stop_event.wait(timeout_seconds):
        return

    if _get_job_snapshot(job_id) is None:
        return

    timeout_message = (
        f"Job exceeded timeout of {MAX_JOB_DURATION_MINUTES} minute(s) and was terminated."
    )
    _request_job_cancel(job_id, timeout_message)
    _kill_job_children(job_id)
    mark_job_failed(
        job_id,
        ErrorPayload(
            error_code="JOB_TIMEOUT",
            message=timeout_message,
            retryable=True,
        ),
    )
    _log_job(job_id, "timed out")


def _check_job_cancelled(job_id: str) -> None:
    if _is_cancel_requested(job_id):
        raise TimeoutError("Job cancelled due to timeout.")


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

    if isinstance(exc, TimeoutError):
        return ErrorPayload(
            error_code="JOB_TIMEOUT",
            message=message,
            retryable=True,
        )

    return ErrorPayload(
        error_code="PIPELINE_FAILED",
        message=message,
        retryable=True,
        details=traceback.format_exc(limit=3),
    )


def _run_pipeline(job_id: str, request: CreateJobRequest) -> Dict[str, object]:
    options = request.options or JobOptions()

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="fetch_source", progress_percent=5, message="Fetching source")
    source_info = fetch_source(
        request.source,
        permission_confirmed=request.confirm_rights if request.source.startswith(("http://", "https://")) else None,
    )

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="transcribe", progress_percent=20, message="Transcribing audio")
    transcript = transcribe(source_info["path"])

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="summarize", progress_percent=35, message="Summarizing transcript")
    scenes = summarize(transcript["full_text"], source_info["target_minutes"])

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="narrate", progress_percent=50, message="Generating narration")
    scenes = narrate(scenes)

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="illustrate", progress_percent=65, message="Generating illustrations")
    scenes = illustrate(scenes)

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="assemble_base", progress_percent=80, message="Building base video")
    base_result = assemble_video(scenes, render_final=False)

    _check_job_cancelled(job_id)
    if options.enable_music:
        update_job_status(job_id, stage="music", progress_percent=88, message="Generating background music")
        music_status = generate_background_music(target_duration_seconds=base_result["duration_seconds"])
    else:
        update_job_status(job_id, stage="music", progress_percent=88, message="Skipping background music (disabled)")
        music_status = {
            "enabled": False,
            "music_path": None,
            "reason": "user_disabled",
        }

    _check_job_cancelled(job_id)
    update_job_status(job_id, stage="finalize", progress_percent=95, message="Rendering final video")
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

    return {
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


def _process_job(job_id: str) -> None:
    timeout_stop_event = Event()
    timeout_thread = Thread(target=_job_timeout_watchdog, args=(job_id, timeout_stop_event), daemon=True)
    timeout_thread.start()

    try:
        request = _get_job_request(job_id)
        if not request:
            raise RuntimeError("Job request payload was missing.")

        update_job_status(
            job_id,
            status="running",
            stage="starting",
            progress_percent=1,
            message="Starting job processing",
            processing_started_at=_now_iso(),
            cancel_requested=False,
        )
        _log_job(job_id, "started processing")

        outputs = _run_pipeline(job_id, request)
        if not _is_cancel_requested(job_id):
            mark_job_complete(job_id, outputs)
    except Exception as exc:
        if not _is_cancel_requested(job_id):
            mark_job_failed(job_id, _parse_error(exc))
    finally:
        timeout_stop_event.set()
        _release_slot(job_id)


def start_job_processing(job_id: str) -> None:
    Thread(target=_process_job, args=(job_id,), daemon=True).start()


def _queue_dispatcher_loop() -> None:
    while True:
        JOB_QUEUE_EVENT.wait()
        started_any = False
        while True:
            next_job_id = _dequeue_next_job_if_slot_available()
            if not next_job_id:
                break
            started_any = True
            _log_job(next_job_id, "dequeued for processing")
            start_job_processing(next_job_id)

        with JOB_QUEUE_LOCK:
            has_waiting_jobs = bool(JOB_QUEUE)

        if not has_waiting_jobs:
            JOB_QUEUE_EVENT.clear()
        elif not started_any:
            # Queue has jobs but no free slots yet. Wait briefly for slot release signal.
            time.sleep(0.05)


Thread(target=_queue_dispatcher_loop, daemon=True).start()


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
def submit_job(request: CreateJobRequest) -> CreateJobResponse:
    if request.source.startswith(("http://", "https://")) and not request.confirm_rights:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "PERMISSION_NOT_CONFIRMED",
                "message": "Set confirm_rights=true to confirm you have permission to use this YouTube source.",
                "retryable": False,
            },
        )

    job_id = create_job(request)
    _enqueue_job(job_id)
    snapshot = get_job_status(job_id)
    created_at = snapshot["created_at"] if snapshot else _now_iso()

    return CreateJobResponse(job_id=job_id, status="queued", created_at=created_at)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    job = get_job_status(job_id)

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
        queue_position=job.get("queue_position"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        error=ErrorPayload(**job["error"]) if job.get("error") else None,
    )


@app.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str) -> JobResultResponse:
    job = _get_job_snapshot(job_id)

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

    job = _get_job_snapshot(job_id)

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

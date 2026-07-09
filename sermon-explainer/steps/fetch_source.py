"""
STATION 1: Fetch the source media.

Beginner explanation:
This file has ONE job: get an audio/video file sitting on disk, no matter
whether the person gave us a YouTube link or an already-uploaded file.
It also measures how long the recording is, because that decides whether
the final explainer should be ~10 minutes or ~5-6 minutes (our rule from
the plan: over 1 hour -> 10 min, else -> 5-6 min).

Edge cases handled here (see our plan's edge case list):
  - #13/#16: YouTube link requires an explicit "I have permission" confirmation
    before we download anything, since only a human can verify that.
  - Duration exactly at the 1-hour boundary is handled with a clear, explicit rule.
  - YouTube blocking/failing download raises a clear error instead of silently
    producing an empty/broken file.
"""

import os
import subprocess
import sys
import uuid
import ffmpeg
import shutil


class SourceIngestionError(RuntimeError):
    """Typed error with machine-readable code/retryability for API diagnostics."""

    def __init__(self, error_code: str, message: str, retryable: bool, details: str = None):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.details = details


def is_youtube_url(source: str) -> bool:
    """Simple check: does this look like a YouTube link or a local file path?"""
    return source.startswith("http://") or source.startswith("https://")


def _log_fetch(message: str, job_id: str = None) -> None:
    if job_id:
        print(f"[job:{job_id}] [fetch_source] {message}")
    else:
        print(f"[fetch_source] {message}")


def _classify_ytdlp_failure(output: str) -> tuple:
    text = (output or "").lower()

    bot_patterns = [
        "sign in to confirm you're not a bot",
        "confirm you're not a bot",
        "not a bot",
        "automated queries",
    ]
    if any(p in text for p in bot_patterns):
        return (
            "YTDLP_BOT_DETECTED",
            "YouTube blocked the request with bot-detection."
            " This can be environment/IP dependent.",
            True,
        )

    # Some YouTube client variants may expose only storyboard/image formats
    # for a given request/session. Retrying with another player client often helps.
    format_unavailable_patterns = [
        "only images are available",
        "requested format is not available",
        "no video formats found",
    ]
    if any(p in text for p in format_unavailable_patterns):
        return (
            "YTDLP_FORMATS_UNAVAILABLE",
            "YouTube did not expose playable media formats for this client/session.",
            True,
        )

    timeout_patterns = [
        "timed out",
        "time-out",
        "network is unreachable",
        "temporary failure in name resolution",
        "connection reset",
        "connection refused",
    ]
    if any(p in text for p in timeout_patterns):
        return (
            "YTDLP_NETWORK_TIMEOUT",
            "Network timeout/connectivity issue while contacting YouTube.",
            True,
        )

    malformed_patterns = [
        "unsupported url",
        "invalid url",
        "is not a valid url",
        "unable to extract video id",
    ]
    if any(p in text for p in malformed_patterns):
        return (
            "YTDLP_INVALID_URL",
            "The provided YouTube URL appears malformed or unsupported.",
            False,
        )

    restricted_patterns = [
        "private video",
        "video unavailable",
        "this video is private",
        "age-restricted",
        "members-only",
        "login to confirm your age",
        "not available in your country",
        "geo-restricted",
    ]
    if any(p in text for p in restricted_patterns):
        return (
            "YTDLP_PRIVATE_OR_RESTRICTED",
            "YouTube reports this video is private/restricted/unavailable.",
            False,
        )

    return (
        "YTDLP_DOWNLOAD_FAILED",
        "yt-dlp failed to download audio from YouTube.",
        True,
    )


def _yt_dlp_base_command() -> list:
    configured = os.environ.get("YTDLP_COMMAND", "").strip()
    if configured:
        return configured.split()

    if shutil.which("yt-dlp"):
        return ["yt-dlp"]

    # Fall back to module execution when the console script is not on PATH.
    return [sys.executable, "-m", "yt_dlp"]


def confirm_permission(source: str, permission_confirmed: bool = None) -> None:
    """
    Beginner explanation: the software cannot know if you have the right
    to use a given YouTube video - only you can know that. So for any
    YouTube link, we stop and require a typed confirmation before downloading.
    """
    if permission_confirmed is True:
        return

    if permission_confirmed is False:
        raise PermissionError(
            "Stopped: permission not confirmed. Re-run and confirm rights to use this content."
        )

    print(f"\nYou're about to download from: {source}")
    answer = input(
        "Do you have the rights/permission to use this recording? (yes/no): "
    ).strip().lower()
    if answer != "yes":
        raise PermissionError(
            "Stopped: permission not confirmed. Re-run and type 'yes' only if "
            "you own this content or have explicit permission to use it."
        )


def download_youtube_audio(url: str, output_dir: str, job_id: str = None) -> str:
    """
    Uses yt-dlp to pull just the audio track (we don't need video quality
    for a sermon - audio is enough for transcription, and it's faster/cheaper
    to download and process).
    """
    os.makedirs(output_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex
    output_template = os.path.join(output_dir, f"source_audio_{unique_id}.%(ext)s")

    primary_client = os.environ.get("YTDLP_PRIMARY_PLAYER_CLIENT", "web").strip() or "web"
    fallback_clients_env = os.environ.get("YTDLP_FALLBACK_PLAYER_CLIENTS", "android,ios")
    fallback_clients = [c.strip() for c in fallback_clients_env.split(",") if c.strip()]

    clients = []
    for client in [primary_client, *fallback_clients]:
        if client not in clients:
            clients.append(client)

    last_error = None
    for attempt, player_client in enumerate(clients, start=1):
        command = [
            *_yt_dlp_base_command(),
            "-x",                       # extract audio only
            "--audio-format", "mp3",
            "--socket-timeout", "20",
            "--extractor-args", f"youtube:player_client={player_client}",
            "-o", output_template,
            url,
        ]

        _log_fetch(
            f"yt-dlp attempt {attempt}/{len(clients)} with player_client={player_client}",
            job_id=job_id,
        )
        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode == 0:
            _log_fetch(
                f"yt-dlp attempt {attempt} succeeded with player_client={player_client}",
                job_id=job_id,
            )
            last_error = None
            break

        combined_output = "\n".join([result.stderr or "", result.stdout or ""]).strip()
        error_code, user_message, retryable = _classify_ytdlp_failure(combined_output)
        _log_fetch(
            f"yt-dlp attempt {attempt} failed with player_client={player_client} "
            f"error_code={error_code}",
            job_id=job_id,
        )
        last_error = SourceIngestionError(
            error_code=error_code,
            message=user_message,
            retryable=retryable,
            details=combined_output,
        )

        retryable_client_fallback_codes = {
            "YTDLP_BOT_DETECTED",
            "YTDLP_FORMATS_UNAVAILABLE",
        }
        should_retry_with_next_client = (
            error_code in retryable_client_fallback_codes and attempt < len(clients)
        )
        if should_retry_with_next_client:
            _log_fetch(
                f"{error_code} encountered; retrying with alternate player client",
                job_id=job_id,
            )
            continue
        break

    if last_error is not None:
        details = last_error.details or "(no yt-dlp output)"
        raise SourceIngestionError(
            error_code=last_error.error_code,
            message=f"{last_error} See yt-dlp diagnostics for details.",
            retryable=last_error.retryable,
            details=details,
        )

    downloaded_path = os.path.join(output_dir, f"source_audio_{unique_id}.mp3")
    if not os.path.exists(downloaded_path):
        raise SourceIngestionError(
            error_code="YTDLP_OUTPUT_MISSING",
            message=(
                "yt-dlp reported success but no output file was found. "
                "Try updating yt-dlp with: pip install -U yt-dlp"
            ),
            retryable=True,
        )

    return downloaded_path


def get_duration_seconds(file_path: str) -> float:
    """
    Asks ffmpeg (via ffprobe) how long the media file is, in seconds.
    This is the number that decides the 10-min vs 5-6-min target length.
    """
    try:
        probe = ffmpeg.probe(file_path)
        return float(probe["format"]["duration"])
    except ffmpeg.Error as e:
        raise RuntimeError(
            f"Could not read the duration of '{file_path}'. "
            "The file may be corrupted or not a valid audio/video file.\n"
            f"ffmpeg said: {e.stderr.decode() if e.stderr else 'unknown error'}"
        )


def get_target_minutes(duration_seconds: float) -> int:
    """
    Our explicit rule (agreed in planning):
      - MORE than 60 minutes of source -> 10 minute explainer
      - 60 minutes or LESS            -> 5-6 minute explainer (we use 6 as the target)
    Being explicit about the "exactly 60 minutes" boundary avoids ambiguity.
    """
    one_hour = 60 * 60
    if duration_seconds > one_hour:
        return 10
    return 6


def fetch_source(
    source: str,
    output_dir: str = "input",
    permission_confirmed: bool = None,
    job_id: str = None,
) -> dict:
    """
    Main entry point for Station 1.

    `source` can be either:
      - a YouTube URL (starts with http:// or https://), or
      - a path to a local audio/video file already on disk.

    Returns a dictionary with everything later stations need:
      { "path": ..., "duration_seconds": ..., "target_minutes": ... }
    """
    if is_youtube_url(source):
        confirm_permission(source, permission_confirmed=permission_confirmed)
        _log_fetch("Downloading audio from YouTube... (this can take a minute)", job_id=job_id)
        file_path = download_youtube_audio(source, output_dir, job_id=job_id)
    else:
        if not os.path.exists(source):
            raise FileNotFoundError(
                f"Could not find the file '{source}'. Check the path and try again."
            )
        file_path = source

    duration_seconds = get_duration_seconds(file_path)
    target_minutes = get_target_minutes(duration_seconds)

    _log_fetch(
        f"Source ready: {file_path} "
        f"({duration_seconds / 60:.1f} minutes long -> "
        f"targeting a {target_minutes}-minute explainer)",
        job_id=job_id,
    )

    return {
        "path": file_path,
        "duration_seconds": duration_seconds,
        "target_minutes": target_minutes,
    }

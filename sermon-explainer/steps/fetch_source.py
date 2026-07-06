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
import uuid
import ffmpeg


def is_youtube_url(source: str) -> bool:
    """Simple check: does this look like a YouTube link or a local file path?"""
    return source.startswith("http://") or source.startswith("https://")


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


def download_youtube_audio(url: str, output_dir: str) -> str:
    """
    Uses yt-dlp to pull just the audio track (we don't need video quality
    for a sermon - audio is enough for transcription, and it's faster/cheaper
    to download and process).
    """
    os.makedirs(output_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex
    output_template = os.path.join(output_dir, f"source_audio_{unique_id}.%(ext)s")

    command = [
        "yt-dlp",
        "-x",                       # extract audio only
        "--audio-format", "mp3",
        "-o", output_template,
        url,
    ]

    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            "YouTube download failed. This usually means yt-dlp needs updating, "
            "or the video is private/region-locked/age-restricted.\n"
            f"Details from yt-dlp:\n{result.stderr}"
        )

    downloaded_path = os.path.join(output_dir, f"source_audio_{unique_id}.mp3")
    if not os.path.exists(downloaded_path):
        raise RuntimeError(
            "yt-dlp reported success but no output file was found. "
            "Try updating yt-dlp with: pip install -U yt-dlp"
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
        print("Downloading audio from YouTube... (this can take a minute)")
        file_path = download_youtube_audio(source, output_dir)
    else:
        if not os.path.exists(source):
            raise FileNotFoundError(
                f"Could not find the file '{source}'. Check the path and try again."
            )
        file_path = source

    duration_seconds = get_duration_seconds(file_path)
    target_minutes = get_target_minutes(duration_seconds)

    print(
        f"Source ready: {file_path} "
        f"({duration_seconds / 60:.1f} minutes long -> "
        f"targeting a {target_minutes}-minute explainer)"
    )

    return {
        "path": file_path,
        "duration_seconds": duration_seconds,
        "target_minutes": target_minutes,
    }

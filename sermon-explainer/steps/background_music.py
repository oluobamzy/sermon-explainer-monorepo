"""
STATION 8: Background music generation.

Beginner explanation:
This station asks ElevenLabs Music to generate a single instrumental track
for the whole explainer. We keep this separate from assembly because it is
an enhancement step: even if music fails, the base narrated video can still
be produced.
"""

import os
from typing import Dict, Optional

import ffmpeg
from elevenlabs import ElevenLabs


DEFAULT_PROMPT = (
    "Instrumental cinematic underscore, warm and uplifting, subtle piano and strings, "
    "gentle rhythm, no vocals, suitable for educational sermon explainer narration"
)


def _write_audio_chunks(chunks, out_path: str) -> None:
    with open(out_path, "wb") as f:
        for chunk in chunks:
            f.write(chunk)


def _probe_duration_seconds(audio_path: str) -> float:
    probe = ffmpeg.probe(audio_path)
    return float(probe["format"]["duration"])


def generate_background_music(
    target_duration_seconds: float,
    output_dir: str = "output",
    prompt: Optional[str] = None,
) -> Dict[str, Optional[object]]:
    """
    Generates instrumental background music for the final video.

    Returns a status dictionary. On failure, returns enabled=False with reason,
    so the pipeline can continue without background music.
    """
    os.makedirs(output_dir, exist_ok=True)

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        return {
            "enabled": False,
            "music_path": None,
            "duration_seconds": None,
            "reason": "ELEVENLABS_API_KEY is missing",
        }

    requested_ms = int(max(3000, min(600000, target_duration_seconds * 1000)))
    music_prompt = prompt or os.environ.get("ELEVENLABS_MUSIC_PROMPT") or DEFAULT_PROMPT
    model_id = os.environ.get("ELEVENLABS_MUSIC_MODEL", "music_v2")
    music_path = os.path.join(output_dir, "background_music.mp3")

    try:
        client = ElevenLabs(api_key=api_key)
        chunks = client.music.compose(
            prompt=music_prompt,
            music_length_ms=requested_ms,
            model_id=model_id,
            force_instrumental=True,
            output_format="mp3_44100_128",
        )
        _write_audio_chunks(chunks, music_path)
        duration_seconds = _probe_duration_seconds(music_path)
        return {
            "enabled": True,
            "music_path": music_path,
            "duration_seconds": duration_seconds,
            "reason": None,
        }
    except Exception as e:
        return {
            "enabled": False,
            "music_path": None,
            "duration_seconds": None,
            "reason": str(e),
        }

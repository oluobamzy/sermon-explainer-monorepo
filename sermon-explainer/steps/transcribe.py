"""
STATION 2: Transcription (speech -> text with timestamps).

Beginner explanation:
This file takes the audio file from Station 1 and asks OpenAI's speech
model to "listen" and write down everything said, along with WHEN
(in seconds) each piece was said. We need the timestamps later so we
know how to divide the sermon into "scenes" for illustrations.

Edge cases handled here:
  - OpenAI's API has a file size limit (~25MB). A 1-2 hour sermon's audio
    file is often bigger than that, so we automatically split long audio
    into smaller chunks first, transcribe each chunk, then stitch the
    text and timestamps back together correctly.
  - Low-confidence stretches (e.g. silence, mumbling, background noise)
    are flagged in the output so you can spot-check them rather than
    trusting them blindly - this directly addresses the "hallucinated
    transcript on silence" risk we identified in planning.
"""

import os
import math
import ffmpeg
from openai import OpenAI

CHUNK_LENGTH_SECONDS = 10 * 60  # 10-minute chunks keep us safely under the size limit
LOW_CONFIDENCE_THRESHOLD = -1.0  # avg_logprob below this gets flagged for review


def split_audio_into_chunks(file_path: str, chunk_dir: str) -> list:
    """
    Cuts the audio into 10-minute pieces using ffmpeg, so each piece is
    small enough for OpenAI's API to accept. Returns a list of
    (chunk_file_path, start_offset_seconds) tuples.
    """
    os.makedirs(chunk_dir, exist_ok=True)

    probe = ffmpeg.probe(file_path)
    total_duration = float(probe["format"]["duration"])
    num_chunks = math.ceil(total_duration / CHUNK_LENGTH_SECONDS)

    chunks = []
    for i in range(num_chunks):
        start = i * CHUNK_LENGTH_SECONDS
        chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}.mp3")
        (
            ffmpeg
            .input(file_path, ss=start, t=CHUNK_LENGTH_SECONDS)
            .output(chunk_path, acodec="libmp3lame", loglevel="quiet")
            .overwrite_output()
            .run()
        )
        chunks.append((chunk_path, start))

    return chunks


def transcribe_chunk(client: OpenAI, chunk_path: str) -> dict:
    """Sends one small audio chunk to OpenAI and gets back text + segment timestamps."""
    with open(chunk_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
        )
    return response.model_dump()


def transcribe(file_path: str, work_dir: str = "input/chunks") -> dict:
    """
    Main entry point for Station 2.

    Returns:
      {
        "full_text": "...",
        "segments": [
            {"start": 0.0, "end": 4.2, "text": "...", "low_confidence": False},
            ...
        ]
      }
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is missing. Check your .env file was created "
            "from .env.example and filled in correctly."
        )
    client = OpenAI(api_key=api_key)

    print("Splitting audio into manageable chunks...")
    chunks = split_audio_into_chunks(file_path, work_dir)

    all_segments = []
    full_text_parts = []
    flagged_count = 0

    for i, (chunk_path, offset) in enumerate(chunks):
        print(f"Transcribing chunk {i + 1}/{len(chunks)}...")
        result = transcribe_chunk(client, chunk_path)
        full_text_parts.append(result.get("text", ""))

        for seg in result.get("segments", []):
            low_confidence = seg.get("avg_logprob", 0) < LOW_CONFIDENCE_THRESHOLD
            if low_confidence:
                flagged_count += 1
            all_segments.append({
                "start": seg["start"] + offset,
                "end": seg["end"] + offset,
                "text": seg["text"].strip(),
                "low_confidence": low_confidence,
            })

    if flagged_count > 0:
        print(
            f"Note: {flagged_count} segment(s) were flagged as low-confidence "
            "(possible mumbling/silence/noise). These are marked in the "
            "transcript output - worth a quick manual check before publishing."
        )

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "segments": all_segments,
    }

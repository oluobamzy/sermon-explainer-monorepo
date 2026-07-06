"""
STATION 5: Narration (script text -> spoken audio).

Beginner explanation:
For each "scene" from Station 3+4, we send its narration text to
ElevenLabs and get back an actual audio recording of a voice reading it.
We do this scene-by-scene (not all at once) because Station 7 needs to
line up each audio clip with its matching image - having them as
separate files makes that lining-up simple and reliable.

Edge cases handled here:
  - Spoken audio almost never matches written word-count exactly (people
    speak at different paces). So after generating each clip, we MEASURE
    its real duration with ffmpeg rather than assuming - later stations
    use this real number, not a guess.
  - Clear error if the API key is missing or a request fails, naming
    exactly which scene failed.
"""

import os
import requests
import ffmpeg

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# A default, natural-sounding pre-made ElevenLabs voice ("Rachel").
# You can override this by setting ELEVENLABS_VOICE_ID in your .env file
# to any voice ID from your ElevenLabs "Voices" library.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


def generate_scene_audio(api_key: str, voice_id: str, text: str, out_path: str) -> None:
    """Calls ElevenLabs for one scene's text and saves the resulting mp3."""
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs request failed (status {response.status_code}) "
            f"for text: '{text[:60]}...'.\nDetails: {response.text}"
        )

    with open(out_path, "wb") as f:
        f.write(response.content)


def narrate(scenes: list, work_dir: str = "input/narration") -> list:
    """
    Main entry point for Station 5.

    Takes the scenes list from summarize.py and adds an "audio_path" and
    real "duration_seconds" to each scene.

    Returns the same scenes list, enriched with audio info.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise EnvironmentError("ELEVENLABS_API_KEY is missing. Check your .env file.")

    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID)

    os.makedirs(work_dir, exist_ok=True)

    total_duration = 0.0
    for i, scene in enumerate(scenes):
        print(f"Generating narration for scene {i + 1}/{len(scenes)}...")
        out_path = os.path.join(work_dir, f"scene_{i:03d}.mp3")
        generate_scene_audio(api_key, voice_id, scene["narration"], out_path)

        probe = ffmpeg.probe(out_path)
        duration = float(probe["format"]["duration"])

        scene["audio_path"] = out_path
        scene["duration_seconds"] = duration
        total_duration += duration

    print(
        f"Narration complete: {len(scenes)} clips, "
        f"~{total_duration / 60:.1f} minutes total spoken length."
    )
    return scenes

"""
STATION 3+4: Summarize the transcript into a scene-by-scene script.

Beginner explanation:
This is the "thinking" part of the whole pipeline. We hand the full
sermon transcript to Claude (Anthropic's AI) and ask it to write a much
shorter narration script that still captures the sermon's real message -
sized to fit our target length (6 or 10 minutes, decided back in Station 1).

We also ask it to break the script into "scenes" - short chunks of a
few sentences each - because each scene will later get its own
illustration (Station 6) and its own chunk of narration audio (Station 5).

Edge cases handled here:
  - Scripture quotes: explicitly instructed to preserve these exactly,
    not paraphrase them, since exact wording matters for sermons.
  - Target length: we calculate a word budget based on average speaking
    speed, so the AI has a concrete number to aim for instead of a vague
    "make it short" instruction.
  - Model output isn't always perfectly clean JSON (sometimes wrapped in
    markdown code fences) - we strip that defensively before parsing.
"""

import os
import json
import anthropic

WORDS_PER_MINUTE = 140  # average comfortable narration speaking pace


def build_prompt(transcript: str, target_minutes: int) -> str:
    word_budget = target_minutes * WORDS_PER_MINUTE

    return f"""You are helping condense a sermon into a short narrated explainer video script.

TARGET LENGTH: approximately {target_minutes} minutes of spoken narration
(roughly {word_budget} words total across all scenes - stay close to this).

RULES:
1. Capture the sermon's core message, main points, and any key illustrations
   or stories the speaker used - cut repetition, filler words, and long pauses.
2. If the speaker quotes scripture directly, preserve that quote's wording
   EXACTLY as spoken - do not paraphrase scripture references.
3. Break the script into scenes of 2-4 sentences each (a natural video
   "beat" - roughly 15-25 seconds of narration per scene).
4. For each scene, also write a short visual description (image_prompt)
   describing a simple, respectful illustration that matches that scene's
   idea. Keep image prompts concrete and concise (one sentence).
5. Do not invent facts, names, or details that are not in the transcript.

Respond with ONLY valid JSON in this exact structure, no other text:
{{
  "scenes": [
    {{"narration": "...", "image_prompt": "..."}},
    {{"narration": "...", "image_prompt": "..."}}
  ]
}}

TRANSCRIPT:
{transcript}
"""


def clean_json_response(raw_text: str) -> str:
    """
    Claude sometimes wraps JSON in ```json ... ``` markdown fences.
    This strips those defensively so json.loads() doesn't fail.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def summarize(transcript: str, target_minutes: int) -> list:
    """
    Main entry point for Station 3+4.

    Returns a list of scenes:
      [{"narration": "...", "image_prompt": "..."}, ...]
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is missing. Check your .env file. Note: this is "
            "a separate developer-console key, not your claude.ai chat login."
        )
    client = anthropic.Anthropic(api_key=api_key)

    print(f"Summarizing transcript into a {target_minutes}-minute script...")

    prompt = build_prompt(transcript, target_minutes)
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    # Some Anthropic responses include a leading "thinking" block where text is None.
    # Use the first non-empty text block so downstream JSON parsing is stable.
    raw_text = next(
      (
        block.text
        for block in response.content
        if getattr(block, "text", None)
      ),
      None,
    )
    if not raw_text:
      raise RuntimeError(
        "Claude returned no text content to parse. Try running this step again."
      )
    cleaned = clean_json_response(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "Claude's response wasn't valid JSON, so it couldn't be parsed. "
            "This is rare but can happen - try running this step again.\n"
            f"Raw response was:\n{raw_text}\n\nError: {e}"
        )

    scenes = parsed.get("scenes", [])
    if not scenes:
        raise RuntimeError("Claude returned no scenes - nothing to build a video from.")

    print(f"Script ready: {len(scenes)} scenes.")
    return scenes

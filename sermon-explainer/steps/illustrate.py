"""
STATION 6: Illustrations (one image per scene).

Beginner explanation:
For each scene, we send its short "image_prompt" (written back in
Station 3+4) to Recraft, which generates a matching illustration.

The most important detail here: we attach the SAME fixed style
instruction to every single request. Without this, scene 1 might come
back as a photo-realistic style and scene 5 as a cartoon - visually
inconsistent and jarring in the final video. Locking the style keeps
every scene looking like part of the same video.

Edge cases handled here:
  - Fixed style template applied to every scene (visual consistency).
  - Recraft bills via prepaid, non-refundable credits (unlike the other
    services) - if a request fails partway through a long sermon, we
    don't want to silently waste credits, so failures stop with a clear
    message naming which scene failed rather than retrying blindly forever.
"""

import os
import requests

RECRAFT_API_URL = "https://external.api.recraft.ai/v1/images/generations"

# Applied to every single image request so all scenes share one visual identity.
# Feel free to adjust this once - not per scene - to change the whole video's look.
STYLE_SUFFIX = (
    ", flat minimalist digital illustration, warm muted earth tones, "
    "simple shapes, no text or words in the image, respectful and calm mood"
)


def generate_scene_image(api_key: str, prompt: str, out_path: str) -> None:
    """Calls Recraft for one scene's image prompt and saves the resulting file."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt + STYLE_SUFFIX,
        "style": "digital_illustration",
        "size": "1820x1024",  # close to 16:9, good for YouTube
    }

    response = requests.post(RECRAFT_API_URL, headers=headers, json=payload, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"Recraft request failed (status {response.status_code}) "
            f"for prompt: '{prompt[:60]}...'.\nDetails: {response.text}\n"
            "Note: Recraft credits are prepaid and non-refundable, so check "
            "your account balance before retrying repeatedly."
        )

    data = response.json()
    image_url = data["data"][0]["url"]

    image_response = requests.get(image_url, timeout=60)
    with open(out_path, "wb") as f:
        f.write(image_response.content)


def illustrate(scenes: list, work_dir: str = "input/images") -> list:
    """
    Main entry point for Station 6.

    Adds an "image_path" to each scene dict. Returns the same scenes list.
    """
    api_key = os.environ.get("RECRAFT_API_KEY")
    if not api_key:
        raise EnvironmentError("RECRAFT_API_KEY is missing. Check your .env file.")

    os.makedirs(work_dir, exist_ok=True)

    for i, scene in enumerate(scenes):
        print(f"Generating illustration for scene {i + 1}/{len(scenes)}...")
        out_path = os.path.join(work_dir, f"scene_{i:03d}.png")
        generate_scene_image(api_key, scene["image_prompt"], out_path)
        scene["image_path"] = out_path

    print(f"Illustrations complete: {len(scenes)} images generated.")
    return scenes

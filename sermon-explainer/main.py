"""
MAIN - runs the whole sermon-explainer pipeline, start to finish.

Beginner explanation:
This is the one file you actually run. It calls each "station" file in
steps/ in the correct order, passing the output of one into the next -
like a conveyor belt. Each station prints what it's doing, so you can
watch progress in the terminal and know exactly which stage you're on.

HOW TO RUN THIS (from VS Code's terminal):
    python main.py "https://www.youtube.com/watch?v=SOME_VIDEO"
or
    python main.py "input/my_sermon.mp3"
"""

import argparse
import os
import sys
from dotenv import load_dotenv

from steps.fetch_source import fetch_source
from steps.transcribe import transcribe
from steps.summarize import summarize
from steps.narrate import narrate
from steps.illustrate import illustrate
from steps.assemble_video import assemble_video
from steps.background_music import generate_background_music


def _derive_title(source: str, explicit_title: str = None) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()

    if source.startswith("http://") or source.startswith("https://"):
        return "Sermon Explainer"

    stem = os.path.splitext(os.path.basename(source))[0].replace("_", " ").replace("-", " ").strip()
    return stem or "Sermon Explainer"


def run(
    source: str,
    title: str = None,
    outro_text: str = None,
    intro_seconds: float = 8.0,
    outro_seconds: float = 8.0,
    social_links: dict = None,
) -> None:
    print("=" * 60)
    print("STATION 1/6: Fetching source media")
    print("=" * 60)
    source_info = fetch_source(source)

    print("\n" + "=" * 60)
    print("STATION 2/6: Transcribing audio")
    print("=" * 60)
    transcript = transcribe(source_info["path"])

    print("\n" + "=" * 60)
    print("STATION 3/6: Writing the condensed script")
    print("=" * 60)
    scenes = summarize(transcript["full_text"], source_info["target_minutes"])

    print("\n" + "=" * 60)
    print("STATION 4/6: Generating narration voice")
    print("=" * 60)
    scenes = narrate(scenes)

    print("\n" + "=" * 60)
    print("STATION 5/6: Generating illustrations")
    print("=" * 60)
    scenes = illustrate(scenes)

    print("\n" + "=" * 60)
    print("STATION 6/6: Assembling base video")
    print("=" * 60)
    result = assemble_video(scenes, render_final=False)

    print("\n" + "=" * 60)
    print("POST-PROCESS: Enhancing final video (music + burned captions)")
    print("=" * 60)

    music_status = generate_background_music(
        target_duration_seconds=result["duration_seconds"],
    )

    if music_status["enabled"]:
        print(f"Background music generated: {music_status['music_path']}")
    else:
        print(f"Background music unavailable, continuing without it: {music_status['reason']}")

    result = assemble_video(
        scenes,
        base_video_path=result["video_path"],
        base_srt_path=result["srt_path"],
        music_path=music_status["music_path"],
        render_final=True,
        intro_title=_derive_title(source, title),
        outro_text=outro_text,
        social_links=social_links,
        intro_duration_seconds=intro_seconds,
        outro_duration_seconds=outro_seconds,
    )

    print("\n" + "=" * 60)
    print("ALL DONE")
    print("=" * 60)
    print(f"Base Video:     {result['video_path']}")
    print(f"Subtitles: {result['srt_path']}")
    print(f"Final Video:    {result['final_video_path']}")


if __name__ == "__main__":
    load_dotenv()  # reads your .env file and makes the keys available

    parser = argparse.ArgumentParser(description="Generate sermon explainer videos")
    parser.add_argument("source", help="YouTube URL or local media file path")
    parser.add_argument("--title", help="Intro title text")
    parser.add_argument("--outro-text", help="Outro closing text")
    parser.add_argument("--intro-seconds", type=float, default=8.0, help="Intro duration in seconds (max 10)")
    parser.add_argument("--outro-seconds", type=float, default=8.0, help="Outro duration in seconds (max 10)")
    parser.add_argument("--facebook", default="", help="Facebook link or handle for outro card")
    parser.add_argument("--youtube", default="", help="YouTube link or handle for outro card")
    parser.add_argument("--x", default="", help="X (Twitter) link or handle for outro card")
    parser.add_argument("--instagram", default="", help="Instagram link or handle for outro card")
    parser.add_argument("--tiktok", default="", help="TikTok link or handle for outro card")
    args = parser.parse_args()

    social_links_arg = {
        "facebook": args.facebook,
        "youtube": args.youtube,
        "x": args.x,
        "instagram": args.instagram,
        "tiktok": args.tiktok,
    }

    try:
        run(
            source=args.source,
            title=args.title,
            outro_text=args.outro_text,
            intro_seconds=args.intro_seconds,
            outro_seconds=args.outro_seconds,
            social_links=social_links_arg,
        )
    except Exception as e:
        print("\n" + "!" * 60)
        print(f"STOPPED: {e}")
        print("!" * 60)
        sys.exit(1)

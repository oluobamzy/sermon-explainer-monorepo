"""
STATION 7: Assemble the final video.

Beginner explanation:
By this point, every scene has three things: narration audio, a matching
illustration, and its exact duration. This file's job is purely
mechanical: show each image for exactly as long as its narration audio
plays, one scene after another, and export one finished video file -
plus a subtitle (.srt) file alongside it for accessibility and YouTube's
auto-captioning quality.

We use ffmpeg for this because it is the free, industry-standard tool
for exactly this kind of "combine audio and images into a video" task.

Edge cases handled here:
  - Each scene becomes its own small video clip first, then all clips are
    joined together. This is more reliable than one giant complex command,
    and if one scene's assembly fails, we know exactly which one.
  - Output is standard YouTube-ready format: 1080p-height, 16:9, H.264 video
    with AAC audio.
"""

import os
import subprocess
import ffmpeg


SOCIAL_PLATFORM_ORDER = [
    ("facebook", "Facebook"),
    ("youtube", "YouTube"),
    ("x", "X"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
]


def _probe_duration_seconds(media_path: str) -> float:
    probe = ffmpeg.probe(media_path)
    return float(probe["format"]["duration"])


def _ffmpeg_has_filter(filter_name: str) -> bool:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    return f" {filter_name} " in result.stdout


def _escape_subtitles_path(path: str) -> str:
    # Escaping for ffmpeg subtitles filter argument parsing.
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_drawtext_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", "\\n")
    )


def _clamp_duration_seconds(value, default_seconds: float = 8.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default_seconds
    return min(10.0, max(1.0, parsed))


def _truncate_text(text: str, max_chars: int) -> str:
    stripped = (text or "").strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3].rstrip() + "..."


def _format_social_lines(social_links: dict) -> list:
    if not social_links:
        return []

    lines = []
    for key, label in SOCIAL_PLATFORM_ORDER:
        value = (social_links.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")

    return lines


def _render_title_card(
    output_path: str,
    title_text: str,
    subtitle_text: str,
    duration_seconds: float,
    social_lines: list = None,
) -> None:
    social_lines = social_lines or []

    escaped_title = _escape_drawtext_text(_truncate_text(title_text, 90))
    escaped_subtitle = _escape_drawtext_text(_truncate_text(subtitle_text, 80))

    filters = [
        "drawbox=x=80:y=80:w=1760:h=920:color=white@0.05:t=fill",
        (
            "drawtext="
            f"text='{escaped_subtitle}':"
            "fontcolor=white:fontsize=48:"
            "x=(w-text_w)/2:y=180"
        ),
        (
            "drawtext="
            f"text='{escaped_title}':"
            "fontcolor=white:fontsize=74:"
            "x=(w-text_w)/2:y=(h/2)-70"
        ),
    ]

    social_start_y = 600
    for idx, line in enumerate(social_lines[:5]):
        escaped_line = _escape_drawtext_text(_truncate_text(line, 95))
        filters.append(
            "drawtext="
            f"text='{escaped_line}':"
            "fontcolor=white:fontsize=40:"
            f"x=(w-text_w)/2:y={social_start_y + (idx * 58)}"
        )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#1f2a44:s=1920x1080:d={duration_seconds}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-shortest",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        output_path,
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to render title card '{output_path}'.\nffmpeg said:\n{result.stderr}")


def _concatenate_videos_with_reencode(video_paths: list, out_path: str) -> None:
    command = ["ffmpeg", "-y"]
    for path in video_paths:
        command += ["-i", path]

    filter_inputs = "".join(f"[{idx}:v][{idx}:a]" for idx in range(len(video_paths)))
    filter_complex = f"{filter_inputs}concat=n={len(video_paths)}:v=1:a=1[vout][aout]"

    command += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-pix_fmt",
        "yuv420p",
        out_path,
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Final intro/body/outro concat failed.\nffmpeg said:\n{result.stderr}")


def _enhance_video(
    base_video_path: str,
    srt_path: str,
    final_video_path: str,
    music_path: str = None,
    burn_captions: bool = True,
) -> None:
    if burn_captions and not _ffmpeg_has_filter("subtitles"):
        raise RuntimeError(
            "Your ffmpeg build does not include the subtitles filter (libass required). "
            "Install an ffmpeg build with libass support."
        )

    total_duration = _probe_duration_seconds(base_video_path)
    escaped_srt = _escape_subtitles_path(os.path.abspath(srt_path))

    command = ["ffmpeg", "-y", "-i", base_video_path]
    filter_complex = []
    map_args = []

    if burn_captions:
        filter_complex.append(f"[0:v]subtitles='{escaped_srt}'[vout]")
        map_args += ["-map", "[vout]"]
    else:
        map_args += ["-map", "0:v:0"]

    use_music = bool(music_path and os.path.exists(music_path) and os.path.getsize(music_path) > 0)

    if use_music:
        music_volume = float(os.environ.get("BGM_VOLUME", "0.18"))
        fade_seconds = min(3.0, max(0.5, total_duration * 0.1))
        fade_out_start = max(0.0, total_duration - fade_seconds)

        command += ["-stream_loop", "-1", "-i", music_path]
        filter_complex.append(
            "[1:a]"
            f"atrim=0:{total_duration},"
            f"afade=t=in:st=0:d={fade_seconds},"
            f"afade=t=out:st={fade_out_start}:d={fade_seconds},"
            f"volume={music_volume}"
            "[bgm]"
        )
        filter_complex.append("[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]")
        map_args += ["-map", "[aout]"]
    else:
        map_args += ["-map", "0:a:0"]

    if filter_complex:
        command += ["-filter_complex", ";".join(filter_complex)]

    command += [
        *map_args,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        final_video_path,
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Final enhancement render failed.\nffmpeg said:\n{result.stderr}")


def format_srt_timestamp(seconds: float) -> str:
    """Converts seconds (e.g. 75.2) into SRT's required HH:MM:SS,mmm format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(scenes: list, out_path: str) -> None:
    """Writes a standard .srt subtitle file, one entry per scene."""
    cursor = 0.0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, scene in enumerate(scenes, start=1):
            start = cursor
            end = cursor + scene["duration_seconds"]
            f.write(f"{i}\n")
            f.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")
            f.write(f"{scene['narration']}\n\n")
            cursor = end


def build_scene_clip(scene: dict, out_path: str) -> None:
    """
    Combines one scene's still image + its narration audio into a short
    video clip, sized for YouTube (1920x1080, 16:9).
    """
    image_input = ffmpeg.input(scene["image_path"], loop=1, t=scene["duration_seconds"])
    audio_input = ffmpeg.input(scene["audio_path"])

    (
        ffmpeg
        .output(
            image_input,
            audio_input,
            out_path,
            vcodec="libx264",
            acodec="aac",
            pix_fmt="yuv420p",
            vf="scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            shortest=None,
            loglevel="quiet",
        )
        .overwrite_output()
        .run()
    )


def concatenate_clips(clip_paths: list, out_path: str, work_dir: str) -> None:
    """Joins all scene clips into one final video using ffmpeg's concat feature."""
    list_file = os.path.join(work_dir, "concat_list.txt")
    with open(list_file, "w") as f:
        for path in clip_paths:
            f.write(f"file '{os.path.abspath(path)}'\n")

    command = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        out_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Final video assembly failed.\nffmpeg said:\n{result.stderr}")


def assemble_video(
    scenes: list,
    output_dir: str = "output",
    work_dir: str = "input/clips",
    base_video_path: str = None,
    base_srt_path: str = None,
    music_path: str = None,
    render_final: bool = True,
    burn_captions: bool = True,
    intro_title: str = None,
    outro_text: str = None,
    social_links: dict = None,
    intro_duration_seconds: float = 8.0,
    outro_duration_seconds: float = 8.0,
) -> dict:
    """
    Main entry point for Station 7.

    Returns:
            {
                "video_path": "output/explainer.mp4",
                "srt_path": "output/explainer.srt",
                "final_video_path": "output/explainer_final.mp4",
            }
    """
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    if base_video_path and base_srt_path:
        video_path = base_video_path
        srt_path = base_srt_path
    else:
        clip_paths = []
        for i, scene in enumerate(scenes):
            print(f"Building clip for scene {i + 1}/{len(scenes)}...")
            clip_path = os.path.join(work_dir, f"clip_{i:03d}.mp4")
            build_scene_clip(scene, clip_path)
            clip_paths.append(clip_path)

        video_path = os.path.join(output_dir, "explainer.mp4")
        print("Joining all scenes into the base narrated video...")
        concatenate_clips(clip_paths, video_path, work_dir)

        srt_path = os.path.join(output_dir, "explainer.srt")
        write_srt(scenes, srt_path)

        print(f"Done! Base video: {video_path}")
        print(f"Subtitles: {srt_path}")

    if not render_final:
        return {
            "video_path": video_path,
            "srt_path": srt_path,
            "final_video_path": None,
            "duration_seconds": _probe_duration_seconds(video_path),
        }

    enhanced_body_path = os.path.join(output_dir, "explainer_enhanced_body.mp4")
    print("Rendering enhanced body video (burned captions + optional music)...")
    _enhance_video(
        video_path,
        srt_path,
        enhanced_body_path,
        music_path=music_path,
        burn_captions=burn_captions,
    )

    intro_card_path = os.path.join(work_dir, "intro_card.mp4")
    outro_card_path = os.path.join(work_dir, "outro_card.mp4")

    intro_duration = _clamp_duration_seconds(intro_duration_seconds, default_seconds=8.0)
    outro_duration = _clamp_duration_seconds(outro_duration_seconds, default_seconds=8.0)
    social_lines = _format_social_lines(social_links)

    resolved_intro_title = (intro_title or "Sermon Explainer").strip()
    resolved_outro_text = (outro_text or "Follow us for more sermon explainers").strip()

    _render_title_card(
        output_path=intro_card_path,
        title_text=resolved_intro_title,
        subtitle_text="Welcome",
        duration_seconds=intro_duration,
    )
    _render_title_card(
        output_path=outro_card_path,
        title_text=resolved_outro_text,
        subtitle_text="Connect with us",
        duration_seconds=outro_duration,
        social_lines=social_lines,
    )

    final_video_path = os.path.join(output_dir, "explainer_final.mp4")
    print("Concatenating intro + body + outro...")
    _concatenate_videos_with_reencode(
        [intro_card_path, enhanced_body_path, outro_card_path],
        final_video_path,
    )
    print(f"Enhanced final video: {final_video_path}")

    return {
        "video_path": video_path,
        "srt_path": srt_path,
        "final_video_path": final_video_path,
        "duration_seconds": _probe_duration_seconds(video_path),
    }

import json
import os
import time
from math import pi, sin
from textwrap import wrap

from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip

VIDEO_HEIGHT = 540
VIDEO_FPS = 20
VIDEO_BITRATE = "900k"
AUDIO_BITRATE = "96k"
VIDEO_PRESET = "veryfast"
SUBTITLE_WIDTH_RATIO = 0.82
SUBTITLE_BOTTOM_MARGIN = 70
SUBTITLE_WRAP = 42
SUBTITLE_TEXT_COLOR = "white"
VIDEO_RETRY_ATTEMPTS = 2
VIDEO_RETRY_DELAY_SECONDS = 2
ANIMATED_COVER_SCALE = 1.08
PULSE_BEATS_PER_SECOND = 1.9
PULSE_SCALE_AMOUNT = 0.035


def _validate_rendered_video(output_path):
    if not os.path.exists(output_path):
        raise ValueError("Video render did not create an output file")

    if os.path.getsize(output_path) <= 0:
        raise ValueError("Video render produced an empty output file")


def _is_retryable_video_error(error):
    error_text = str(error or "").strip().lower()
    retry_markers = (
        "timeout",
        "timed out",
        "broken pipe",
        "temporarily unavailable",
        "resource busy",
        "device busy",
        "i/o error",
    )
    return any(marker in error_text for marker in retry_markers)


def _build_subtitle_lines(lyrics):
    if not lyrics:
        return []

    subtitle_lines = []
    raw_lines = [line.strip() for line in str(lyrics).splitlines()]

    for raw_line in raw_lines:
        if not raw_line:
            continue

        wrapped_parts = wrap(" ".join(raw_line.split()), width=SUBTITLE_WRAP) or [raw_line]
        current_group = []

        for part in wrapped_parts:
            current_group.append(part)
            if len(current_group) == 2:
                subtitle_lines.append("\n".join(current_group))
                current_group = []

        if current_group:
            subtitle_lines.append("\n".join(current_group))

    return subtitle_lines


def _load_subtitle_segments(subtitle_timing):
    if not subtitle_timing:
        return []

    if isinstance(subtitle_timing, list):
        return subtitle_timing

    try:
        parsed = json.loads(subtitle_timing)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    return parsed if isinstance(parsed, list) else []


def _build_subtitle_clips(subtitle_lines, duration, frame_size):
    if not subtitle_lines:
        return []

    frame_width, frame_height = frame_size
    segment_duration = max(duration / len(subtitle_lines), 0.1)
    subtitle_width = int(frame_width * SUBTITLE_WIDTH_RATIO)
    font_size = max(int(frame_height * 0.055), 28)

    subtitle_clips = []
    for index, subtitle_line in enumerate(subtitle_lines):
        start_time = index * segment_duration
        remaining = max(duration - start_time, 0.1)

        subtitle_clip = TextClip(
            text=subtitle_line,
            font_size=font_size,
            color=SUBTITLE_TEXT_COLOR,
            stroke_color="black",
            stroke_width=1,
            method="caption",
            size=(subtitle_width, None),
            margin=(28, 18),
            text_align="center",
        )
        subtitle_y = max(frame_height - SUBTITLE_BOTTOM_MARGIN - subtitle_clip.h, 0)
        subtitle_clips.append(
            subtitle_clip
            .with_start(start_time)
            .with_duration(min(segment_duration, remaining))
            .with_position(("center", subtitle_y))
        )

    return subtitle_clips


def _build_timed_subtitle_clips(subtitle_segments, duration, frame_size):
    if not subtitle_segments:
        return []

    frame_width, frame_height = frame_size
    subtitle_width = int(frame_width * SUBTITLE_WIDTH_RATIO)
    font_size = max(int(frame_height * 0.055), 28)

    subtitle_clips = []
    for segment in subtitle_segments:
        subtitle_text = str(segment.get("text") or "").strip()
        start_time = float(segment.get("start", 0.0) or 0.0)
        end_time = float(segment.get("end", start_time) or start_time)
        if not subtitle_text or end_time <= start_time:
            continue

        subtitle_clip = TextClip(
            text=subtitle_text,
            font_size=font_size,
            color=SUBTITLE_TEXT_COLOR,
            stroke_color="black",
            stroke_width=1,
            method="caption",
            size=(subtitle_width, None),
            margin=(28, 18),
            text_align="center",
        )
        subtitle_y = max(frame_height - SUBTITLE_BOTTOM_MARGIN - subtitle_clip.h, 0)
        subtitle_clips.append(
            subtitle_clip
            .with_start(max(start_time, 0.0))
            .with_duration(min(end_time - start_time, max(duration - start_time, 0.1)))
            .with_position(("center", subtitle_y))
        )

    return subtitle_clips


def _normalize_animation_style(animation_style):
    normalized = str(animation_style or "").strip().lower()
    if normalized in {"pan", "pulse", "pan_pulse", "none"}:
        return normalized
    return "pan_pulse"


def _build_animated_cover_clip(image_path, duration, animation_style="pan_pulse"):
    animation_style = _normalize_animation_style(animation_style)
    base_image = (
        ImageClip(image_path)
        .with_duration(duration)
        .resized(height=VIDEO_HEIGHT)
    )
    frame_size = base_image.size

    if animation_style == "none":
        return base_image, frame_size

    frame_width, frame_height = frame_size
    duration = max(float(duration or 0.0), 0.1)

    def scale_at_time(t):
        base_scale = ANIMATED_COVER_SCALE if animation_style in {"pan", "pan_pulse"} else 1.0
        pulse = max(0.0, sin(2 * pi * PULSE_BEATS_PER_SECOND * t)) if animation_style in {"pulse", "pan_pulse"} else 0.0
        return base_scale + (PULSE_SCALE_AMOUNT * pulse)

    def position_at_time(t):
        progress = min(max(t / duration, 0.0), 1.0)
        x_factor = progress if animation_style in {"pan", "pan_pulse"} else 0.5
        y_factor = 0.35 * progress if animation_style in {"pan", "pan_pulse"} else 0.5
        scale_offset = scale_at_time(t) - 1.0
        return (
            -(frame_width * scale_offset * x_factor),
            -(frame_height * scale_offset * y_factor),
        )

    animated_cover = base_image.resized(scale_at_time)

    animated_clip = CompositeVideoClip(
        [
            animated_cover.with_position(position_at_time)
        ],
        size=frame_size,
    ).with_duration(duration)
    base_image.close()
    return animated_clip, frame_size


def create_music_video(audio_path, image_path, output_path, animation_style="pan_pulse", lyrics=None, subtitle_timing=None, subtitles_enabled=True, progress_callback=None):
    last_error = None

    for attempt in range(1, VIDEO_RETRY_ATTEMPTS + 1):
        if progress_callback:
            if attempt == 1:
                progress_callback("⏳ Creating music video...\nPreparing video layers...")
            else:
                progress_callback(f"⏳ Creating music video...\nRetrying render ({attempt}/{VIDEO_RETRY_ATTEMPTS})...")

        audio = AudioFileClip(audio_path)
        cover_video, frame_size = _build_animated_cover_clip(image_path, audio.duration, animation_style=animation_style)

        subtitle_clips = []
        video = cover_video.with_audio(audio)

        try:
            if subtitles_enabled:
                subtitle_segments = _load_subtitle_segments(subtitle_timing)
                subtitle_clips = _build_timed_subtitle_clips(subtitle_segments, audio.duration, frame_size)
                if not subtitle_clips:
                    subtitle_lines = _build_subtitle_lines(lyrics)
                    subtitle_clips = _build_subtitle_clips(subtitle_lines, audio.duration, frame_size)
                if subtitle_clips:
                    video = CompositeVideoClip([video, *subtitle_clips]).with_audio(audio)

            if progress_callback:
                progress_callback("⏳ Creating music video...\nRendering video...")

            video.write_videofile(
                output_path,
                fps=VIDEO_FPS,
                codec="libx264",
                audio_codec="aac",
                bitrate=VIDEO_BITRATE,
                audio_bitrate=AUDIO_BITRATE,
                preset=VIDEO_PRESET,
            )
            _validate_rendered_video(output_path)
            if progress_callback:
                progress_callback("✅ Video created 100%")
            return output_path
        except Exception as exc:
            last_error = exc
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

            if attempt == VIDEO_RETRY_ATTEMPTS or not _is_retryable_video_error(exc):
                raise

            time.sleep(VIDEO_RETRY_DELAY_SECONDS)
        finally:
            video.close()
            cover_video.close()
            audio.close()
            for subtitle_clip in subtitle_clips:
                subtitle_clip.close()

    raise last_error or Exception("Video creation failed")
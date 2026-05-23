import json
import logging
import os
import re
import subprocess
import time
from functools import lru_cache
from math import pi, sin
from textwrap import wrap

from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)

logger = logging.getLogger(__name__)

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

WINDOWS_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_SEARCH_DIRS = [
    os.environ.get("SUBTITLE_FONT_DIR", "").strip(),
    WINDOWS_FONTS_DIR,
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
]
DEFAULT_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans",
    "DejaVu Sans",
    "NotoSans-Regular.ttf",
    "DejaVuSans.ttf",
    "segoeui.ttf",
    "arial.ttf",
]
UNICODE_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans Symbols",
    "NotoSansSymbols-Regular.ttf",
    "arialuni.ttf",
    *DEFAULT_SUBTITLE_FONT_CANDIDATES,
]
KHMER_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans Khmer",
    "Noto Sans Khmer UI",
    "NotoSansKhmer-Regular.ttf",
    "NotoSansKhmerUI-Regular.ttf",
    "KhmerUI.ttf",
    "DaunPenh.ttf",
    "MoolBoran.ttf",
    *UNICODE_SUBTITLE_FONT_CANDIDATES,
]
THAI_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans Thai",
    "Noto Serif Thai",
    "NotoSansThai-Regular.ttf",
    "NotoSerifThai-Regular.ttf",
    "LeelawUI.ttf",
    "LeelaUIb.ttf",
    "tahoma.ttf",
    *UNICODE_SUBTITLE_FONT_CANDIDATES,
]
JAPANESE_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans CJK JP",
    "Noto Sans JP",
    "Source Han Sans",
    "NotoSansCJK-Regular.ttc",
    "NotoSansJP-Regular.otf",
    "SourceHanSans-Regular.otf",
    "YuGothR.ttc",
    "YuGothM.ttc",
    "msgothic.ttc",
    "meiryo.ttc",
    *UNICODE_SUBTITLE_FONT_CANDIDATES,
]
CHINESE_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "Noto Serif CJK SC",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "Noto Sans CJK",
    "NotoSansCJK-Regular.ttc",
    "NotoSansSC-Regular.otf",
    "NotoSansSC-Regular.ttf",
    "NotoSerifCJK-Regular.ttc",
    "SourceHanSansSC-Regular.otf",
    "SourceHanSans-Regular.otf",
    "WenQuanYi Zen Hei.ttf",
    "wqy-zenhei.ttc",
    "WenQuanYi Micro Hei.ttf",
    "wqy-microhei.ttc",
    "msyh.ttc",
    "msyhl.ttc",
    "msyhbd.ttc",
    "msjh.ttc",
    "msjhbd.ttc",
    "SimsunExtG.ttf",
    "simsun.ttc",
    "mingliub.ttc",
    "simhei.ttf",
    *UNICODE_SUBTITLE_FONT_CANDIDATES,
]
KOREAN_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans CJK KR",
    "Noto Sans KR",
    "Source Han Sans",
    "NotoSansCJK-Regular.ttc",
    "NotoSansKR-Regular.otf",
    "SourceHanSans-Regular.otf",
    "malgun.ttf",
    "malgunbd.ttf",
    *UNICODE_SUBTITLE_FONT_CANDIDATES,
]
ARABIC_SUBTITLE_FONT_CANDIDATES = [
    "Noto Naskh Arabic",
    "Noto Sans Arabic",
    "NotoNaskhArabic-Regular.ttf",
    "NotoSansArabic-Regular.ttf",
    "arialuni.ttf",
    "tahoma.ttf",
    *DEFAULT_SUBTITLE_FONT_CANDIDATES,
]
HEBREW_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans Hebrew",
    "NotoSansHebrew-Regular.ttf",
    "arialuni.ttf",
    "tahoma.ttf",
    *DEFAULT_SUBTITLE_FONT_CANDIDATES,
]
DEVANAGARI_SUBTITLE_FONT_CANDIDATES = [
    "Noto Sans Devanagari",
    "Noto Serif Devanagari",
    "NotoSansDevanagari-Regular.ttf",
    "NotoSerifDevanagari-Regular.ttf",
    "arialuni.ttf",
    *DEFAULT_SUBTITLE_FONT_CANDIDATES,
]

CHINESE_SCRIPT_RANGES = (
    ("\u3100", "\u312f"),
    ("\u31a0", "\u31bf"),
    ("\u3400", "\u4dbf"),
    ("\u4e00", "\u9fff"),
    ("\uf900", "\ufaff"),
    ("\u3000", "\u303f"),
    ("\uff00", "\uffef"),
    ("\U00020000", "\U0002a6df"),
    ("\U0002a700", "\U0002b73f"),
    ("\U0002b740", "\U0002b81f"),
    ("\U0002b820", "\U0002ceaf"),
    ("\U0002ceb0", "\U0002ebef"),
    ("\U00030000", "\U0003134f"),
)
THAI_SCRIPT_RANGES = (
    ("\u0e00", "\u0e7f"),
)
KOREAN_SCRIPT_RANGES = (
    ("\u1100", "\u11ff"),
    ("\u3130", "\u318f"),
    ("\uac00", "\ud7af"),
)
ARABIC_SCRIPT_RANGES = (
    ("\u0600", "\u06ff"),
    ("\u0750", "\u077f"),
    ("\u08a0", "\u08ff"),
    ("\ufb50", "\ufdff"),
    ("\ufe70", "\ufeff"),
)
HEBREW_SCRIPT_RANGES = (
    ("\u0590", "\u05ff"),
)
DEVANAGARI_SCRIPT_RANGES = (
    ("\u0900", "\u097f"),
)


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


@lru_cache(maxsize=1)
def _available_font_index():
    font_index = {}

    for search_dir in FONT_SEARCH_DIRS:
        if not search_dir or not os.path.isdir(search_dir):
            continue

        for root, _dirs, files in os.walk(search_dir):
            for file_name in files:
                lower_name = file_name.lower()
                if lower_name not in font_index:
                    font_index[lower_name] = os.path.join(root, file_name)

    return font_index


def _font_path(font_name):
    if not font_name:
        return None

    if os.path.isabs(font_name):
        return font_name if os.path.exists(font_name) else None

    indexed_path = _available_font_index().get(str(font_name).lower())
    if indexed_path:
        return indexed_path

    return _fontconfig_match(font_name)


@lru_cache(maxsize=256)
def _fontconfig_match(font_name):
    if not font_name or os.name == "nt":
        return None

    if os.path.sep in str(font_name):
        return None

    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", str(font_name)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    matched_path = (result.stdout or "").strip()
    return matched_path if matched_path and os.path.exists(matched_path) else None


def _contains_range(text, start, end):
    return any(start <= char <= end for char in str(text or ""))


def _contains_any_range(text, ranges):
    text = str(text or "")
    return any(start <= char <= end for char in text for start, end in ranges)


def _uses_cjk_subtitle_layout(text):
    return (
        _contains_any_range(text, CHINESE_SCRIPT_RANGES)
        or _contains_range(text, "\u3040", "\u30ff")
        or _contains_any_range(text, KOREAN_SCRIPT_RANGES)
    )


def _wrap_cjk_subtitle_text(text, max_chars_per_line=18):
    compact_text = re.sub(r"\s+", "", str(text or "")).strip()
    if not compact_text:
        return ""

    lines = [compact_text[index:index + max_chars_per_line] for index in range(0, len(compact_text), max_chars_per_line)]
    return "\n".join(lines[:2])


def _subtitle_preview(text, limit=80):
    normalized_text = str(text or "").replace("\n", " ").strip()
    if len(normalized_text) <= limit:
        return normalized_text
    return f"{normalized_text[:limit]}..."


@lru_cache(maxsize=512)
def _log_subtitle_render_choice(source_text, display_text, font_path, method):
    logger.info(
        "Subtitle render choice: method=%s cjk=%s font=%s source=%r display=%r",
        method,
        _uses_cjk_subtitle_layout(source_text),
        font_path or "<default>",
        _subtitle_preview(source_text),
        _subtitle_preview(display_text),
    )


def _resolve_subtitle_font(text):
    candidates = DEFAULT_SUBTITLE_FONT_CANDIDATES

    if _contains_range(text, "\u1780", "\u17ff"):
        candidates = KHMER_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, THAI_SCRIPT_RANGES):
        candidates = THAI_SUBTITLE_FONT_CANDIDATES
    elif _contains_range(text, "\u3040", "\u30ff"):
        candidates = JAPANESE_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, KOREAN_SCRIPT_RANGES):
        candidates = KOREAN_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, CHINESE_SCRIPT_RANGES):
        candidates = CHINESE_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, ARABIC_SCRIPT_RANGES):
        candidates = ARABIC_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, HEBREW_SCRIPT_RANGES):
        candidates = HEBREW_SUBTITLE_FONT_CANDIDATES
    elif _contains_any_range(text, DEVANAGARI_SCRIPT_RANGES):
        candidates = DEVANAGARI_SUBTITLE_FONT_CANDIDATES

    for candidate in candidates:
        font_path = _font_path(candidate)
        if font_path:
            return font_path

    return None


def _make_subtitle_text_clip(text, font_size, subtitle_width):
    font_path = _resolve_subtitle_font(text)

    if _uses_cjk_subtitle_layout(text):
        display_text = _wrap_cjk_subtitle_text(text)
        _log_subtitle_render_choice(text, display_text, font_path, "label")
        return TextClip(
            text=display_text,
            font=font_path,
            font_size=font_size,
            color=SUBTITLE_TEXT_COLOR,
            stroke_color="black",
            stroke_width=1,
            method="label",
            margin=(28, 18),
            text_align="center",
        )

    _log_subtitle_render_choice(text, text, font_path, "caption")
    return TextClip(
        text=text,
        font=font_path,
        font_size=font_size,
        color=SUBTITLE_TEXT_COLOR,
        stroke_color="black",
        stroke_width=1,
        method="caption",
        size=(subtitle_width, None),
        margin=(28, 18),
        text_align="center",
    )


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

        subtitle_clip = _make_subtitle_text_clip(subtitle_line, font_size, subtitle_width)
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

        subtitle_clip = _make_subtitle_text_clip(subtitle_text, font_size, subtitle_width)
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


def _build_source_video_clip(source_video_path, duration):
    base_video = VideoFileClip(source_video_path).without_audio()
    if base_video.duration <= 0:
        base_video.close()
        raise ValueError("Uploaded source video has no duration")

    clip_duration = max(float(duration or 0.0), 0.1)
    frame_size = base_video.size

    if base_video.duration >= clip_duration:
        return base_video.subclipped(0, clip_duration), frame_size, [base_video]

    segments = []
    remaining = clip_duration
    while remaining > 0:
        segment_duration = min(base_video.duration, remaining)
        segments.append(base_video.subclipped(0, segment_duration))
        remaining -= segment_duration

    looped_video = concatenate_videoclips(segments, method="compose").with_duration(clip_duration)
    return looped_video, looped_video.size, [base_video, *segments]


def create_music_video(audio_path, image_path=None, output_path=None, animation_style="pan_pulse", lyrics=None, subtitle_timing=None, subtitles_enabled=True, progress_callback=None, source_video_path=None):
    last_error = None

    for attempt in range(1, VIDEO_RETRY_ATTEMPTS + 1):
        if progress_callback:
            if attempt == 1:
                progress_callback("⏳ Creating music video...\nPreparing video layers...")
            else:
                progress_callback(f"⏳ Creating music video...\nRetrying render ({attempt}/{VIDEO_RETRY_ATTEMPTS})...")

        audio = AudioFileClip(audio_path)
        source_cleanup_clips = []
        if source_video_path:
            cover_video, frame_size, source_cleanup_clips = _build_source_video_clip(source_video_path, audio.duration)
        elif image_path:
            cover_video, frame_size = _build_animated_cover_clip(image_path, audio.duration, animation_style=animation_style)
        else:
            audio.close()
            raise ValueError("Either image_path or source_video_path is required")

        subtitle_clips = []
        video = cover_video.with_audio(audio)

        try:
            if subtitles_enabled:
                subtitle_segments = _load_subtitle_segments(subtitle_timing)
                logger.info(
                    "Preparing subtitles: timed_segments=%s lyrics_present=%s output=%s",
                    len(subtitle_segments),
                    bool(str(lyrics or "").strip()),
                    output_path,
                )
                subtitle_clips = _build_timed_subtitle_clips(subtitle_segments, audio.duration, frame_size)
                if not subtitle_clips:
                    subtitle_lines = _build_subtitle_lines(lyrics)
                    logger.info("Falling back to line-based subtitles: lines=%s output=%s", len(subtitle_lines), output_path)
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
            if output_path and os.path.exists(output_path):
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
            for cleanup_clip in source_cleanup_clips:
                cleanup_clip.close()

    raise last_error or Exception("Video creation failed")
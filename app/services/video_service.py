import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
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
ZERO_WIDTH_SUBTITLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

WINDOWS_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PROJECT_FONT_DIR = os.path.join(PROJECT_ROOT_DIR, "fonts")
FONT_SEARCH_DIRS = [
    os.environ.get("SUBTITLE_FONT_DIR", "").strip(),
    PROJECT_FONT_DIR,
    WINDOWS_FONTS_DIR,
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/nix/var/nix/profiles/default/share/fonts",
    "/root/.nix-profile/share/fonts",
    "/etc/profiles/per-user/root/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
]
FONTCONFIG_MATCH_BINARY_CANDIDATES = [
    "/usr/bin/fc-match",
    "/usr/local/bin/fc-match",
    "/nix/var/nix/profiles/default/bin/fc-match",
    "/root/.nix-profile/bin/fc-match",
    "/etc/profiles/per-user/root/bin/fc-match",
    "/nix/store/*-fontconfig-*/bin/fc-match",
]
FONTCONFIG_LIST_BINARY_CANDIDATES = [
    "/usr/bin/fc-list",
    "/usr/local/bin/fc-list",
    "/nix/var/nix/profiles/default/bin/fc-list",
    "/root/.nix-profile/bin/fc-list",
    "/etc/profiles/per-user/root/bin/fc-list",
    "/nix/store/*-fontconfig-*/bin/fc-list",
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
    "NotoSansKhmer-Regular.ttf",  # downloaded at startup
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
    "NotoSansCJKjp-Regular.otf",
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
    "NotoSansCJKsc-Regular.otf",
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

DEFAULT_FONTCONFIG_PATTERNS = (
    "sans-serif",
)
KHMER_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=km",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
THAI_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=th",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
JAPANESE_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=ja",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
CHINESE_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=zh-cn",
    "sans-serif:lang=zh",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
KOREAN_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=ko",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
ARABIC_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=ar",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
HEBREW_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=he",
    *DEFAULT_FONTCONFIG_PATTERNS,
)
DEVANAGARI_FONTCONFIG_PATTERNS = (
    "sans-serif:lang=hi",
    *DEFAULT_FONTCONFIG_PATTERNS,
)


_STARTUP_FONT_DOWNLOADS = (
    (
        "NotoSansCJKsc-Regular.otf",
        "https://github.com/notofonts/noto-cjk/raw/main"
        "/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    ),
    (
        "NotoSansCJKjp-Regular.otf",
        "https://github.com/notofonts/noto-cjk/raw/main"
        "/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf",
    ),
    (
        "NotoSansKhmer-Regular.ttf",
        "https://github.com/notofonts/noto-fonts/raw/main"
        "/hinted/ttf/NotoSansKhmer/NotoSansKhmer-Regular.ttf",
    ),
)


def _ensure_cjk_font():
    """Download CJK subtitle fonts to PROJECT_FONT_DIR at startup if missing."""
    os.makedirs(PROJECT_FONT_DIR, exist_ok=True)
    for filename, url in _STARTUP_FONT_DOWNLOADS:
        target = os.path.join(PROJECT_FONT_DIR, filename)
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            continue
        try:
            logger.info("Downloading subtitle font to %s", target)
            urllib.request.urlretrieve(url, target)
            if os.path.getsize(target) > 0:
                logger.info("Subtitle font downloaded: %s (%d bytes)", filename, os.path.getsize(target))
            else:
                os.remove(target)
                logger.warning("Font download produced empty file; removed: %s", filename)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Font download failed for %s: %s", filename, exc)
            try:
                if os.path.isfile(target):
                    os.remove(target)
            except OSError:
                pass


_ensure_cjk_font()


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
def _fontconfig_binary(binary_name):
    resolved_path = shutil.which(binary_name)
    if resolved_path:
        return resolved_path

    candidates = FONTCONFIG_MATCH_BINARY_CANDIDATES if binary_name == "fc-match" else FONTCONFIG_LIST_BINARY_CANDIDATES
    for candidate in candidates:
        for expanded_path in glob.glob(candidate):
            if os.path.isfile(expanded_path) and os.access(expanded_path, os.X_OK):
                return expanded_path

    return None


@lru_cache(maxsize=1)
def _fontconfig_known_fonts():
    if os.name == "nt":
        return ()

    fc_list_path = _fontconfig_binary("fc-list")
    if not fc_list_path:
        return ()

    try:
        result = subprocess.run(
            [fc_list_path, "-f", "%{file}\n"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()

    font_paths = []
    for line in (result.stdout or "").splitlines():
        normalized_path = line.strip()
        if normalized_path and os.path.exists(normalized_path):
            font_paths.append(normalized_path)

    return tuple(font_paths)


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

    for font_path in _fontconfig_known_fonts():
        file_name = os.path.basename(font_path).lower()
        if file_name and file_name not in font_index:
            font_index[file_name] = font_path

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

    fc_match_path = _fontconfig_binary("fc-match")
    if not fc_match_path:
        return None

    try:
        result = subprocess.run(
            [fc_match_path, "-f", "%{file}", str(font_name)],
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
        or _contains_range(text, "\u1780", "\u17ff")   # Khmer
        or _contains_any_range(text, THAI_SCRIPT_RANGES)  # Thai
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


def _clean_subtitle_text(text):
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = ZERO_WIDTH_SUBTITLE_CHARS_RE.sub("", normalized)
    cleaned_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    return "\n".join(cleaned_lines).strip()


def _normalize_subtitle_segments(subtitle_segments, duration):
    if not subtitle_segments:
        return []

    max_duration = max(float(duration or 0.0), 0.0)
    normalized_segments = []
    for segment in subtitle_segments:
        if not isinstance(segment, dict):
            continue

        subtitle_text = _clean_subtitle_text(segment.get("text"))
        if not subtitle_text:
            continue

        try:
            start_time = float(segment.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start_time = 0.0

        try:
            end_time = float(segment.get("end", start_time) or start_time)
        except (TypeError, ValueError):
            end_time = start_time

        start_time = max(start_time, 0.0)

        if end_time <= start_time:
            if max_duration > start_time:
                end_time = min(start_time + 2.0, max_duration)
            else:
                continue

        normalized_segments.append(
            {
                "text": subtitle_text,
                "start": round(start_time, 3),
                "end": round(max(end_time, start_time + 0.1), 3),
            }
        )

    return normalized_segments


@lru_cache(maxsize=1)
def _log_project_font_dir_state():
    try:
        font_entries = sorted(os.listdir(PROJECT_FONT_DIR)) if os.path.isdir(PROJECT_FONT_DIR) else []
    except OSError:
        font_entries = []

    logger.info(
        "Project font dir state: dir=%s exists=%s files=%s",
        PROJECT_FONT_DIR,
        os.path.isdir(PROJECT_FONT_DIR),
        font_entries,
    )


@lru_cache(maxsize=512)
def _log_subtitle_render_choice(source_text, display_text, font_path, method):
    logger.debug(
        "Subtitle render choice: method=%s cjk=%s font=%s source=%r display=%r",
        method,
        _uses_cjk_subtitle_layout(source_text),
        font_path or "<default>",
        _subtitle_preview(source_text),
        _subtitle_preview(display_text),
    )


def _resolve_subtitle_font(text):
    candidates = DEFAULT_SUBTITLE_FONT_CANDIDATES
    fontconfig_patterns = DEFAULT_FONTCONFIG_PATTERNS

    if _contains_range(text, "\u1780", "\u17ff"):
        candidates = KHMER_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = KHMER_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, THAI_SCRIPT_RANGES):
        candidates = THAI_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = THAI_FONTCONFIG_PATTERNS
    elif _contains_range(text, "\u3040", "\u30ff"):
        candidates = JAPANESE_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = JAPANESE_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, KOREAN_SCRIPT_RANGES):
        candidates = KOREAN_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = KOREAN_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, CHINESE_SCRIPT_RANGES):
        candidates = CHINESE_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = CHINESE_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, ARABIC_SCRIPT_RANGES):
        candidates = ARABIC_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = ARABIC_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, HEBREW_SCRIPT_RANGES):
        candidates = HEBREW_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = HEBREW_FONTCONFIG_PATTERNS
    elif _contains_any_range(text, DEVANAGARI_SCRIPT_RANGES):
        candidates = DEVANAGARI_SUBTITLE_FONT_CANDIDATES
        fontconfig_patterns = DEVANAGARI_FONTCONFIG_PATTERNS

    for candidate in candidates:
        font_path = _font_path(candidate)
        if font_path:
            return font_path

    for pattern in fontconfig_patterns:
        font_path = _fontconfig_match(pattern)
        if font_path:
            return font_path

    return None


def _uses_cjk_char_layout(text):
    """True for scripts where spaces are not word separators (CJK), so space-stripping is safe."""
    return (
        _contains_any_range(text, CHINESE_SCRIPT_RANGES)
        or _contains_range(text, "\u3040", "\u30ff")
        or _contains_any_range(text, KOREAN_SCRIPT_RANGES)
    )


def _make_subtitle_text_clip(text, font_size, subtitle_width):
    font_path = _resolve_subtitle_font(text)
    uses_khmer_or_thai = _contains_range(text, "\u1780", "\u17ff") or _contains_any_range(text, THAI_SCRIPT_RANGES)
    stroke_width = 0 if uses_khmer_or_thai else 1

    if _uses_cjk_subtitle_layout(text):
        # Use ImageMagick 'label' method for all complex scripts.
        # For CJK, strip spaces and chunk; for Khmer/Thai keep the text as-is.
        if _uses_cjk_char_layout(text):
            display_text = _wrap_cjk_subtitle_text(text)
            _log_subtitle_render_choice(text, display_text, font_path, "label")
            return TextClip(
                text=display_text,
                font=font_path,
                font_size=font_size,
                color=SUBTITLE_TEXT_COLOR,
                stroke_color="black",
                stroke_width=stroke_width,
                method="label",
                margin=(28, 18),
                text_align="center",
            )
        else:
            display_text = text
            # Complex scripts like Khmer/Thai can clip in tightly-fit label mode.
            _log_subtitle_render_choice(text, display_text, font_path, "caption")
            return TextClip(
                text=display_text,
                font=font_path,
                font_size=font_size,
                color=SUBTITLE_TEXT_COLOR,
                stroke_color="black",
                stroke_width=stroke_width,
                method="caption",
                size=(subtitle_width, None),
                margin=(34, 24),
                text_align="center",
            )

    _log_subtitle_render_choice(text, text, font_path, "caption")
    return TextClip(
        text=text,
        font=font_path,
        font_size=font_size,
        color=SUBTITLE_TEXT_COLOR,
        stroke_color="black",
        stroke_width=stroke_width,
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
    for index, segment in enumerate(subtitle_segments):
        subtitle_text = _clean_subtitle_text(segment.get("text"))
        start_time = float(segment.get("start", 0.0) or 0.0)
        end_time = float(segment.get("end", start_time) or start_time)
        if not subtitle_text or end_time <= start_time:
            continue

        try:
            subtitle_clip = _make_subtitle_text_clip(subtitle_text, font_size, subtitle_width)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Skipping subtitle segment render failure: index=%s text=%r error=%s",
                index,
                _subtitle_preview(subtitle_text),
                exc,
            )
            continue

        subtitle_y = max(frame_height - SUBTITLE_BOTTOM_MARGIN - subtitle_clip.h, 0)
        subtitle_clips.append(
            subtitle_clip
            .with_start(max(start_time, 0.0))
            .with_duration(min(end_time - start_time, max(duration - start_time, 0.1)))
            .with_position(("center", subtitle_y))
        )

    return subtitle_clips


def _segments_from_subtitle_lines(subtitle_lines, duration):
    if not subtitle_lines:
        return []

    segment_duration = max(float(duration or 0.0) / len(subtitle_lines), 0.1)
    segments = []
    for index, subtitle_line in enumerate(subtitle_lines):
        start_time = index * segment_duration
        remaining = max(float(duration or 0.0) - start_time, 0.1)
        segments.append({
            "text": subtitle_line,
            "start": round(max(start_time, 0.0), 3),
            "end": round(max(start_time, 0.0) + min(segment_duration, remaining), 3),
        })

    return segments


def _contains_complex_shaping_script(text):
    # Khmer/Thai need OpenType shaping support for stacked/combined glyphs.
    return (
        _contains_range(text, "\u1780", "\u17ff")
        or _contains_any_range(text, THAI_SCRIPT_RANGES)
    )


def _should_use_ass_renderer(subtitle_segments):
    return any(
        _contains_complex_shaping_script(segment.get("text", ""))
        for segment in subtitle_segments
        if isinstance(segment, dict)
    )


def _ass_timestamp(seconds):
    safe_seconds = max(float(seconds or 0.0), 0.0)
    hours = int(safe_seconds // 3600)
    minutes = int((safe_seconds % 3600) // 60)
    whole_seconds = int(safe_seconds % 60)
    centiseconds = int(round((safe_seconds - int(safe_seconds)) * 100))
    if centiseconds >= 100:
        whole_seconds += 1
        centiseconds = 0
    if whole_seconds >= 60:
        minutes += 1
        whole_seconds = 0
    if minutes >= 60:
        hours += 1
        minutes = 0
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _ass_escape_text(text):
    escaped_text = str(text or "")
    escaped_text = escaped_text.replace("\\", r"\\")
    escaped_text = escaped_text.replace("{", r"\{").replace("}", r"\}")
    escaped_text = escaped_text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")
    return escaped_text


def _ass_font_name_for_segments(subtitle_segments):
    combined_text = "\n".join(str(segment.get("text") or "") for segment in subtitle_segments)
    if _contains_range(combined_text, "\u1780", "\u17ff"):
        return "Noto Sans Khmer"
    if _contains_any_range(combined_text, THAI_SCRIPT_RANGES):
        return "Noto Sans Thai"
    return "Noto Sans"


def _write_ass_subtitle_file(subtitle_segments, frame_size):
    frame_width, frame_height = frame_size
    font_name = _ass_font_name_for_segments(subtitle_segments)
    font_size = max(int(frame_height * 0.055), 28)

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {frame_width}",
        f"PlayResY: {frame_height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,0,2,30,30,{SUBTITLE_BOTTOM_MARGIN},1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    for segment in subtitle_segments:
        subtitle_text = _clean_subtitle_text(segment.get("text"))
        start_time = float(segment.get("start", 0.0) or 0.0)
        end_time = float(segment.get("end", start_time) or start_time)
        if not subtitle_text or end_time <= start_time:
            continue

        ass_lines.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(start_time)},"
            f"{_ass_timestamp(end_time)},"
            "Default,,0,0,0,,"
            f"{_ass_escape_text(subtitle_text)}"
        )

    ass_file = tempfile.NamedTemporaryFile(mode="w", suffix=".ass", delete=False, encoding="utf-8")
    try:
        ass_file.write("\n".join(ass_lines))
        ass_file.flush()
        return ass_file.name
    finally:
        ass_file.close()


def _ffmpeg_filter_path(path):
    normalized = str(path or "").replace("\\", "/")
    normalized = normalized.replace(":", r"\:")
    normalized = normalized.replace("'", r"\'")
    normalized = normalized.replace(",", r"\,")
    normalized = normalized.replace("[", r"\[").replace("]", r"\]")
    return normalized


@lru_cache(maxsize=1)
def _ffmpeg_binary():
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    try:
        import imageio_ffmpeg  # type: ignore

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_path and os.path.isfile(ffmpeg_path) and os.access(ffmpeg_path, os.X_OK):
            return ffmpeg_path
    except Exception:  # noqa: BLE001
        pass

    return None


def extract_audio_from_video(video_path: str) -> str:
    """Extract audio track from a video file and save it as an MP3. Returns the MP3 path."""
    ffmpeg_path = _ffmpeg_binary()
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not available for audio extraction")

    mp3_path = str(video_path).rsplit(".", 1)[0] + "_extracted.mp3"
    result = subprocess.run(
        [ffmpeg_path, "-y", "-i", str(video_path), "-vn", "-acodec", "mp3", "-q:a", "2", mp3_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not os.path.exists(mp3_path):
        raise RuntimeError(f"Audio extraction failed:\n{result.stderr[-500:]}")
    return mp3_path


def _burn_subtitles_with_ass(input_video_path, output_video_path, subtitle_segments, frame_size):
    ffmpeg_path = _ffmpeg_binary()
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not available for ASS subtitle rendering")

    ass_path = _write_ass_subtitle_file(subtitle_segments, frame_size)
    tmp_output_path = f"{output_video_path}.assburn.mp4"
    escaped_ass_path = _ffmpeg_filter_path(ass_path)
    escaped_fonts_dir = _ffmpeg_filter_path(PROJECT_FONT_DIR)
    # Force full unicode wrapping and OpenType complex shaping for Khmer/Thai.
    subtitle_filter = f"subtitles={escaped_ass_path}:fontsdir={escaped_fonts_dir}:wrap_unicode=1:shaping=complex"

    command = [
        ffmpeg_path,
        "-y",
        "-i",
        input_video_path,
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        VIDEO_PRESET,
        "-b:v",
        VIDEO_BITRATE,
        "-c:a",
        "copy",
        tmp_output_path,
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ASS subtitle burn failed: {result.stderr.strip() or result.stdout.strip()}")

        _validate_rendered_video(tmp_output_path)
        os.replace(tmp_output_path, output_video_path)
    finally:
        try:
            if os.path.exists(ass_path):
                os.remove(ass_path)
        except OSError:
            pass
        try:
            if os.path.exists(tmp_output_path):
                os.remove(tmp_output_path)
        except OSError:
            pass


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
    output_path = output_path or os.path.join(PROJECT_ROOT_DIR, "media", "generated", "videos", f"music_{int(time.time())}.mp4")
    _log_project_font_dir_state()

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
        subtitle_segments = []
        use_ass_renderer = False
        render_output_path = output_path
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
                if not subtitle_segments:
                    subtitle_lines = _build_subtitle_lines(lyrics)
                    logger.info("Falling back to line-based subtitles: lines=%s output=%s", len(subtitle_lines), output_path)
                    subtitle_segments = _segments_from_subtitle_lines(subtitle_lines, audio.duration)

                subtitle_segments = _normalize_subtitle_segments(subtitle_segments, audio.duration)
                if not subtitle_segments:
                    logger.warning("No valid subtitle segments after normalization; rendering video without subtitles")

                use_ass_renderer = _should_use_ass_renderer(subtitle_segments)
                if use_ass_renderer:
                    if _ffmpeg_binary():
                        render_output_path = f"{output_path}.base.mp4"
                        logger.info("Using ASS/libass subtitle renderer for complex script shaping")
                    else:
                        use_ass_renderer = False
                        logger.warning(
                            "ASS subtitle renderer unavailable (ffmpeg missing); falling back to MoviePy subtitle renderer"
                        )
                else:
                    subtitle_clips = _build_timed_subtitle_clips(subtitle_segments, audio.duration, frame_size)
                if not use_ass_renderer and not subtitle_clips:
                    subtitle_clips = _build_timed_subtitle_clips(subtitle_segments, audio.duration, frame_size)
                if subtitle_clips:
                    video = CompositeVideoClip([video, *subtitle_clips]).with_audio(audio)

            if progress_callback:
                progress_callback("⏳ Creating music video...\nRendering video...")

            video.write_videofile(
                render_output_path,
                fps=VIDEO_FPS,
                codec="libx264",
                audio_codec="aac",
                bitrate=VIDEO_BITRATE,
                audio_bitrate=AUDIO_BITRATE,
                preset=VIDEO_PRESET,
            )
            if use_ass_renderer:
                try:
                    _burn_subtitles_with_ass(render_output_path, output_path, subtitle_segments, frame_size)
                except Exception as ass_error:  # noqa: BLE001
                    logger.warning(
                        "ASS subtitle burn failed; falling back to MoviePy subtitle renderer: %s",
                        ass_error,
                    )
                    subtitle_clips = _build_timed_subtitle_clips(subtitle_segments, audio.duration, frame_size)
                    if subtitle_clips:
                        video = CompositeVideoClip([cover_video.with_audio(audio), *subtitle_clips]).with_audio(audio)
                        video.write_videofile(
                            output_path,
                            fps=VIDEO_FPS,
                            codec="libx264",
                            audio_codec="aac",
                            bitrate=VIDEO_BITRATE,
                            audio_bitrate=AUDIO_BITRATE,
                            preset=VIDEO_PRESET,
                        )
                    elif render_output_path != output_path:
                        os.replace(render_output_path, output_path)
            elif render_output_path != output_path:
                os.replace(render_output_path, output_path)
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
            if render_output_path and render_output_path != output_path and os.path.exists(render_output_path):
                try:
                    os.remove(render_output_path)
                except OSError:
                    pass
            video.close()
            cover_video.close()
            audio.close()
            for subtitle_clip in subtitle_clips:
                subtitle_clip.close()
            for cleanup_clip in source_cleanup_clips:
                cleanup_clip.close()

    raise last_error or Exception("Video creation failed")
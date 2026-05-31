import base64
import json
import re
import time
from difflib import SequenceMatcher

from moviepy import AudioFileClip
from openai import OpenAI

from app.config.settings import OPENAI_API_KEY

# -----------------------------------
# OPENAI CLIENT
# -----------------------------------
client = OpenAI(api_key=OPENAI_API_KEY)
LYRICS_RETRY_ATTEMPTS = 4
LYRICS_RETRY_DELAY_SECONDS = 2
MAX_FINAL_SUBTITLE_EXTENSION_SECONDS = 12.0
MIN_SUBTITLE_LINE_DURATION_SECONDS = 0.15
MIN_SUBTITLE_LINE_GAP_SECONDS = 0.02


def _is_retryable_openai_error(error):
    error_text = str(error or "").strip().lower()
    retry_markers = (
        "timeout",
        "timed out",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "server error",
        "service unavailable",
        "connection",
        "overloaded",
    )
    return any(marker in error_text for marker in retry_markers)


def _extract_json_object(content):
    if not content:
        return {}

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        return {}

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def analyze_payment_screenshot(image_bytes, credits, price, payment_method):
    if not OPENAI_API_KEY or not image_bytes:
        return {
            "status": "unavailable",
            "confidence": 0,
            "summary": "AI receipt check unavailable.",
            "amount_found": "",
            "reference": "",
            "reasons": [],
        }

    encoded_image = base64.b64encode(image_bytes).decode("ascii")

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You review payment receipt screenshots for a Telegram bot. "
                    "Decide whether the screenshot is likely valid proof of payment, but stay conservative. "
                    "If the image is unclear, suspicious, incomplete, or does not clearly match the expected amount, return review or reject. "
                    "Return JSON only with keys: status, confidence, summary, amount_found, reference, reasons. "
                    "status must be one of approve, review, reject. confidence must be an integer from 0 to 100. "
                    "reasons must be an array of short strings."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Expected credits: {credits}\n"
                            f"Expected price: {price}\n"
                            f"Payment method: {payment_method}\n\n"
                            "Review this screenshot. Look for visible amount, payment confirmation cues, transaction reference, and whether the screenshot appears complete. "
                            "Be strict. If anything important is missing or unclear, prefer review instead of approve."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded_image}",
                        },
                    },
                ],
            },
        ],
        temperature=0.1,
        max_tokens=300,
    )

    content = (response.choices[0].message.content or "").strip()
    payload = _extract_json_object(content)

    status = str(payload.get("status") or "review").strip().lower()
    if status not in {"approve", "review", "reject"}:
        status = "review"

    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0

    reasons = payload.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []

    return {
        "status": status,
        "confidence": max(0, min(confidence, 100)),
        "summary": str(payload.get("summary") or "No summary provided.").strip(),
        "amount_found": str(payload.get("amount_found") or "").strip(),
        "reference": str(payload.get("reference") or "").strip(),
        "reasons": [str(item).strip() for item in reasons if str(item).strip()][:3],
    }


def _clean_lyric_lines(lyrics):
    if not lyrics:
        return []

    lyric_lines = []
    for raw_line in str(lyrics).splitlines():
        line = raw_line.strip()
        if not line or (line.startswith("[") and line.endswith("]")):
            continue
        lyric_lines.append(line)

    return lyric_lines


def _normalize_alignment_text(text):
    normalized = re.sub(r"\[[^\]]+\]", " ", str(text or ""))
    normalized = re.sub(r"[^\w\s'\u1780-\u17ff]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _normalize_alignment_tokens(text):
    normalized = _normalize_alignment_text(text)
    if not normalized:
        return []
    return [part for part in normalized.split(" ") if part]


def _word_weight(text):
    normalized = _normalize_alignment_text(text)
    if not normalized:
        return 1

    if (
        _contains_khmer_script(normalized)
        or _contains_thai_script(normalized)
        or any("\u3040" <= char <= "\u30ff" for char in normalized)
        or any("\u4e00" <= char <= "\u9fff" for char in normalized)
        or any("\uac00" <= char <= "\ud7af" for char in normalized)
    ):
        # Character-count weighting is more stable for scripts where word boundaries are unreliable.
        return max(len(normalized.replace(" ", "")), 1)

    words = [part for part in normalized.split(" ") if part]
    return len(words) or max(len(normalized), 1)


def _extract_transcription_words(response_data):
    words = []

    top_level_words = response_data.get("words") or []
    if top_level_words:
        words.extend(top_level_words)

    if not words:
        for segment in response_data.get("segments") or []:
            words.extend(segment.get("words") or [])

    usable_words = []
    for word in words:
        word_text = (word.get("word") or word.get("text") or "").strip()
        word_start = word.get("start")
        word_end = word.get("end")
        if not word_text or word_start is None or word_end is None:
            continue

        usable_words.append({
            "text": word_text,
            "start": float(word_start),
            "end": float(word_end),
            "weight": _word_weight(word_text),
            "tokens": _normalize_alignment_tokens(word_text),
        })

    return usable_words


def _segment_portion_time(segment, consumed_weight, take_weight, total_weight):
    segment_start = float(segment.get("start", 0.0) or 0.0)
    segment_end = float(segment.get("end", segment_start) or segment_start)
    segment_duration = max(segment_end - segment_start, 0.0)

    if segment_duration <= 0 or total_weight <= 0:
        return segment_start, segment_end

    start_time = segment_start + (consumed_weight / total_weight) * segment_duration
    end_time = segment_start + ((consumed_weight + take_weight) / total_weight) * segment_duration
    return start_time, end_time


def _align_lyric_lines_to_segments(lyric_lines, segments):
    if not lyric_lines or not segments:
        return []

    usable_segments = []
    for segment in segments:
        segment_text = (segment.get("text") or "").strip()
        segment_start = segment.get("start")
        segment_end = segment.get("end")
        if not segment_text or segment_start is None or segment_end is None:
            continue
        usable_segments.append({
            "text": segment_text,
            "start": float(segment_start),
            "end": float(segment_end),
            "weight": _word_weight(segment_text),
        })

    if not usable_segments:
        return []

    total_segment_duration = sum(
        max(segment["end"] - segment["start"], 0.0)
        for segment in usable_segments
    )
    if total_segment_duration <= 0:
        return []

    aligned_lines = []
    segment_index = 0
    segment_consumed_weight = 0.0
    timeline_cursor = usable_segments[0]["start"]

    for line_index, line in enumerate(lyric_lines):
        line_weight = _word_weight(line)
        remaining_weight = float(line_weight)
        line_start = None
        line_end = None

        while remaining_weight > 0 and segment_index < len(usable_segments):
            segment = usable_segments[segment_index]
            segment_total_weight = max(float(segment["weight"]), 1.0)
            segment_remaining_weight = max(segment_total_weight - segment_consumed_weight, 0.0)

            if segment_remaining_weight <= 0:
                segment_index += 1
                segment_consumed_weight = 0.0
                continue

            take_weight = min(remaining_weight, segment_remaining_weight)
            take_start, take_end = _segment_portion_time(
                segment,
                segment_consumed_weight,
                take_weight,
                segment_total_weight,
            )

            if line_start is None:
                line_start = take_start
            line_end = take_end
            timeline_cursor = take_end

            remaining_weight -= take_weight
            segment_consumed_weight += take_weight

            if segment_consumed_weight >= segment_total_weight:
                segment_index += 1
                segment_consumed_weight = 0.0

        if line_start is None:
            remaining_lines = len(lyric_lines) - line_index
            remaining_duration = max(usable_segments[-1]["end"] - timeline_cursor, 0.6)
            fallback_duration = max(remaining_duration / max(remaining_lines, 1), 0.6)
            line_start = timeline_cursor
            line_end = min(line_start + fallback_duration, usable_segments[-1]["end"])

        line_end = max(line_end or line_start, line_start + 0.35)
        aligned_lines.append({
            "text": line,
            "start": round(line_start, 3),
            "end": round(line_end, 3),
        })

    aligned_lines = _normalize_aligned_line_boundaries(aligned_lines, min_duration=0.2)

    final_end = usable_segments[-1]["end"]

    aligned_lines[-1]["end"] = round(max(aligned_lines[-1]["end"], final_end), 3)
    return aligned_lines


def _align_lyric_lines_to_words(lyric_lines, words):
    if not lyric_lines or not words:
        return []

    aligned_lines = []
    word_index = 0
    word_consumed_weight = 0.0
    timeline_cursor = words[0]["start"]

    for line_index, line in enumerate(lyric_lines):
        line_tokens = _normalize_alignment_tokens(line)
        matched_window = _find_best_line_window(line_tokens, words, word_index)
        if matched_window:
            match_start, match_end, next_word_index, _score = matched_window
            line_start = words[match_start]["start"]
            line_end = words[match_end]["end"]
            aligned_lines.append({
                "text": line,
                "start": round(line_start, 3),
                "end": round(max(line_end, line_start + 0.15), 3),
            })
            word_index = next_word_index
            word_consumed_weight = 0.0
            timeline_cursor = line_end
            continue

        line_weight = float(_word_weight(line))
        remaining_weight = line_weight
        line_start = None
        line_end = None

        while remaining_weight > 0 and word_index < len(words):
            word = words[word_index]
            word_total_weight = max(float(word["weight"]), 1.0)
            word_remaining_weight = max(word_total_weight - word_consumed_weight, 0.0)

            if word_remaining_weight <= 0:
                word_index += 1
                word_consumed_weight = 0.0
                continue

            take_weight = min(remaining_weight, word_remaining_weight)
            take_start, take_end = _segment_portion_time(
                word,
                word_consumed_weight,
                take_weight,
                word_total_weight,
            )

            if line_start is None:
                line_start = take_start
            line_end = take_end
            timeline_cursor = take_end

            remaining_weight -= take_weight
            word_consumed_weight += take_weight

            if word_consumed_weight >= word_total_weight:
                word_index += 1
                word_consumed_weight = 0.0

        if line_start is None:
            remaining_lines = len(lyric_lines) - line_index
            remaining_duration = max(words[-1]["end"] - timeline_cursor, 0.6)
            fallback_duration = max(remaining_duration / max(remaining_lines, 1), 0.6)
            line_start = timeline_cursor
            line_end = min(line_start + fallback_duration, words[-1]["end"])

        line_end = max(line_end or line_start, line_start + 0.2)
        aligned_lines.append({
            "text": line,
            "start": round(line_start, 3),
            "end": round(line_end, 3),
        })

    aligned_lines = _normalize_aligned_line_boundaries(aligned_lines, min_duration=0.15)

    final_end = words[-1]["end"]

    aligned_lines[-1]["end"] = round(max(aligned_lines[-1]["end"], final_end), 3)
    return aligned_lines


def _score_word_window(line_tokens, window_tokens):
    if not line_tokens or not window_tokens:
        return 0.0

    line_text = " ".join(line_tokens)
    window_text = " ".join(window_tokens)
    sequence_score = SequenceMatcher(None, line_text, window_text).ratio()

    line_token_set = set(line_tokens)
    window_token_set = set(window_tokens)
    overlap_score = len(line_token_set & window_token_set) / max(len(line_token_set), 1)

    return (sequence_score * 0.7) + (overlap_score * 0.3)


def _find_best_line_window(line_tokens, words, start_index):
    if not line_tokens:
        return None

    normalized_words = [word.get("tokens") or _normalize_alignment_tokens(word.get("text")) for word in words]
    token_count = len(line_tokens)
    max_start = min(len(words), start_index + max(token_count * 3, 12))
    min_window = max(1, token_count - 2)
    max_window = max(token_count + 3, token_count)

    best_match = None
    best_score = 0.0

    for candidate_start in range(start_index, max_start):
        for window_size in range(min_window, max_window + 1):
            candidate_end = candidate_start + window_size
            if candidate_end > len(words):
                break

            window_tokens = []
            for candidate_index in range(candidate_start, candidate_end):
                window_tokens.extend(normalized_words[candidate_index])

            score = _score_word_window(line_tokens, window_tokens)
            if score > best_score:
                best_score = score
                best_match = (candidate_start, candidate_end - 1, candidate_end, score)

    if best_match and best_score >= 0.52:
        return best_match

    return None


def _get_audio_duration_seconds(mp3_path):
    if not mp3_path:
        return None

    audio = None
    try:
        audio = AudioFileClip(mp3_path)
        return float(audio.duration or 0.0) or None
    except Exception:
        return None
    finally:
        if audio is not None:
            audio.close()


def _normalize_aligned_line_boundaries(aligned_lines, min_duration=MIN_SUBTITLE_LINE_DURATION_SECONDS):
    if not aligned_lines:
        return aligned_lines

    minimum_duration = max(float(min_duration or 0.0), 0.05)
    gap_seconds = max(float(MIN_SUBTITLE_LINE_GAP_SECONDS), 0.0)

    for item in aligned_lines:
        start_time = float(item.get("start", 0.0) or 0.0)
        end_time = float(item.get("end", start_time) or start_time)
        item["start"] = round(start_time, 3)
        item["end"] = round(max(end_time, start_time + 0.05), 3)

    for index, item in enumerate(aligned_lines[:-1]):
        next_start = float(aligned_lines[index + 1].get("start", item["end"]) or item["end"])
        start_time = float(item.get("start", 0.0) or 0.0)
        current_end = float(item.get("end", start_time) or start_time)

        available_duration = max(next_start - start_time - gap_seconds, 0.05)
        minimum_end = start_time + min(minimum_duration, available_duration)
        latest_end = max(start_time + 0.05, next_start - gap_seconds)
        clamped_end = min(current_end, latest_end)
        item["end"] = round(max(clamped_end, minimum_end), 3)

    return aligned_lines


def _extend_final_subtitle_coverage(aligned_lines, audio_duration):
    if not aligned_lines or not audio_duration:
        return aligned_lines

    final_line = aligned_lines[-1]
    final_end = float(final_line.get("end", 0.0) or 0.0)
    final_start = float(final_line.get("start", 0.0) or 0.0)
    trailing_gap = max(float(audio_duration) - final_end, 0.0)

    if trailing_gap <= 0:
        return aligned_lines

    if trailing_gap > MAX_FINAL_SUBTITLE_EXTENSION_SECONDS:
        return aligned_lines

    final_line["end"] = round(max(final_end, final_start + 0.2, float(audio_duration)), 3)
    return aligned_lines


def generate_subtitle_timing(mp3_path, lyrics, language="", progress_callback=None):
    lyric_lines = _clean_lyric_lines(lyrics)
    if not lyric_lines:
        return []

    audio_duration = _get_audio_duration_seconds(mp3_path)

    if progress_callback:
        progress_callback("⏳ Generating subtitles...\nPreparing lyric lines...")

    transcription_language = _normalize_transcription_language(language)
    transcription_prompt = (
        "Transcribe the sung lyrics with timestamps. Keep the original words and order. "
        "Return the best possible alignment for each sung phrase."
    )

    with open(mp3_path, "rb") as audio_file:
        if progress_callback:
            progress_callback("⏳ Generating subtitles...\nTranscribing audio with timestamps...")
        transcription_kwargs = {
            "model": "whisper-1",
            "file": audio_file,
            "prompt": transcription_prompt,
            "response_format": "verbose_json",
            "timestamp_granularities": ["word", "segment"],
        }
        if transcription_language:
            transcription_kwargs["language"] = transcription_language

        response = client.audio.transcriptions.create(**transcription_kwargs)

    if hasattr(response, "model_dump"):
        response_data = response.model_dump()
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = json.loads(response)

    if progress_callback:
        progress_callback("⏳ Generating subtitles...\nAligning lyrics to transcription...")

    segments = response_data.get("segments") or []
    has_khmer_or_thai = (
        _is_khmer_language(language)
        or _contains_khmer_script("\n".join(lyric_lines))
        or _contains_thai_script("\n".join(lyric_lines))
    )

    if has_khmer_or_thai:
        # Segment alignment is more reliable for Khmer/Thai than word windows.
        aligned_segments = _align_lyric_lines_to_segments(lyric_lines, segments)
        aligned_segments = _extend_final_subtitle_coverage(aligned_segments, audio_duration)
        if progress_callback:
            progress_callback("✅ Subtitles generated 100%")
        return aligned_segments

    words = _extract_transcription_words(response_data)
    aligned_lines = _align_lyric_lines_to_words(lyric_lines, words)
    if aligned_lines:
        aligned_lines = _extend_final_subtitle_coverage(aligned_lines, audio_duration)
        if progress_callback:
            progress_callback("✅ Subtitles generated 100%")
        return aligned_lines

    aligned_segments = _align_lyric_lines_to_segments(lyric_lines, segments)
    aligned_segments = _extend_final_subtitle_coverage(aligned_segments, audio_duration)
    if progress_callback:
        progress_callback("✅ Subtitles generated 100%")
    return aligned_segments


def _is_khmer_language(language):
    if not language:
        return False

    normalized = language.strip().lower()
    return normalized in {"khmer", "cambodian", "km"}


def _contains_khmer_script(text):
    return any("\u1780" <= char <= "\u17ff" for char in text)


def _contains_thai_script(text):
    return any("\u0e00" <= char <= "\u0e7f" for char in text)


def _normalize_transcription_language(language):
    if not language:
        return None

    normalized = language.strip().lower()
    language_map = {
        "english": "en",
        "en": "en",
        "khmer": "km",
        "cambodian": "km",
        "km": "km",
        "thai": "th",
        "th": "th",
    }

    if normalized in language_map:
        return language_map[normalized]

    if len(normalized) == 2 and normalized.isalpha():
        return normalized

    return None


def _format_transcribed_lyrics(text, language=""):
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You format raw song transcriptions into readable lyric layout. "
                    "Do not translate, summarize, or invent lines. Keep the original words, "
                    "but split them into lyric-style lines and short stanzas. Remove obvious "
                    "filler transcription artifacts only when clearly not part of the song. "
                    "Return only the formatted lyrics text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Language: {language or 'unknown'}\n\n"
                    "Format this raw transcription as song lyrics with line breaks:\n\n"
                    f"{text}"
                ),
            },
        ],
        temperature=0.1,
        max_tokens=1400,
    )

    formatted_text = (response.choices[0].message.content or "").strip()
    return formatted_text or text


def transcribe_lyrics_from_mp3(mp3_path, language="", progress_callback=None):

    if progress_callback:
        progress_callback("⏳ Recovering lyrics from MP3...\nPreparing audio transcription...")

    transcription_language = _normalize_transcription_language(language)
    transcription_prompt = (
        "Transcribe the song lyrics as accurately as possible. "
        "Keep the original wording and line breaks when possible."
    )

    if _is_khmer_language(language):
        transcription_prompt += (
            " The song is in Khmer. Return Khmer lyrics in Khmer script only. "
            "Do not convert the lyrics to Thai script or translate them."
        )

    with open(mp3_path, "rb") as audio_file:
        if progress_callback:
            progress_callback("⏳ Recovering lyrics from MP3...\nTranscribing vocals from audio...")
        transcription_kwargs = {
            "model": "whisper-1",
            "file": audio_file,
            "prompt": transcription_prompt,
        }
        if transcription_language:
            transcription_kwargs["language"] = transcription_language

        response = client.audio.transcriptions.create(**transcription_kwargs)

    if isinstance(response, str):
        text = response
    else:
        text = getattr(response, "text", "")

    text = text.strip()
    if not text:
        raise Exception("No lyrics were detected in the MP3.")

    if _is_khmer_language(language) and _contains_thai_script(text) and not _contains_khmer_script(text):
        if progress_callback:
            progress_callback("⏳ Recovering lyrics from MP3...\nRepairing Khmer script output...")
        repair_response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You repair Khmer lyric transcriptions. Convert Thai-script phonetic "
                        "renderings of Khmer lyrics into Khmer script only. Do not translate "
                        "the meaning into Thai or English. Preserve the original line breaks."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Convert this text into Khmer script only. If a line is uncertain, keep "
                        "the closest Khmer-sounding lyric without changing the song structure.\n\n"
                        f"{text}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=1200,
        )

        repaired_text = (repair_response.choices[0].message.content or "").strip()
        if repaired_text:
            text = repaired_text

    if progress_callback:
        progress_callback("⏳ Recovering lyrics from MP3...\nFormatting recovered lyrics...")
    text = _format_transcribed_lyrics(text, language)

    if progress_callback:
        progress_callback("✅ Lyrics recovered 100%")

    return text


# -----------------------------------
# GENERATE SONG TITLES
# -----------------------------------
def generate_title(topic, mood):

    prompt = f"""
    Create 5 short emotional song titles.

    Topic: {topic}
    Mood: {mood}

    Requirements:
    - Make titles catchy
    - Make titles modern
    - Suitable for music platforms
    - Emotional and memorable
    """

    response = client.chat.completions.create(
        model="gpt-4.1-mini",

        messages=[
            {
                "role": "system",
                "content": """
                You are a professional music producer
                and songwriter who creates catchy song titles.
                """
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0.8,
        max_tokens=150
    )

    return response.choices[0].message.content


# -----------------------------------
# GENERATE SONG LYRICS
# -----------------------------------

_STYLE_PROFILES = {
    "rap": {
        "structure": "[Hook]\n[Verse 1 - 16 bars]\n[Hook]\n[Verse 2 - 16 bars]\n[Bridge]\n[Outro]",
        "tone": "confident, storytelling bars, wordplay, internal rhymes, multisyllabic end-rhymes",
        "rhyme": "AABB with internal rhymes; hook lines should all end-rhyme",
    },
    "ballad": {
        "structure": "[Verse 1]\n[Pre-Chorus]\n[Chorus]\n[Verse 2]\n[Pre-Chorus]\n[Chorus]\n[Bridge]\n[Final Chorus]",
        "tone": "slow, emotionally raw, conversational, long breath phrases",
        "rhyme": "ABAB or ABCB per verse; chorus AABB",
    },
    "k-pop": {
        "structure": "[Intro]\n[Verse 1]\n[Pre-Chorus]\n[Chorus]\n[Verse 2]\n[Pre-Chorus]\n[Chorus]\n[Bridge]\n[Drop Chorus]",
        "tone": "punchy, rhythmic, high energy, youthful, polished",
        "rhyme": "AABB, tight syllable counts, short punchy lines",
    },
    "pop": {
        "structure": "[Verse 1]\n[Pre-Chorus]\n[Chorus]\n[Verse 2]\n[Pre-Chorus]\n[Chorus]\n[Bridge]\n[Final Chorus]",
        "tone": "relatable, catchy, hook-driven, radio-friendly",
        "rhyme": "ABAB verse, AABB chorus",
    },
    "r&b": {
        "structure": "[Intro]\n[Verse 1]\n[Chorus]\n[Verse 2]\n[Chorus]\n[Bridge]\n[Final Chorus]",
        "tone": "smooth, sensual, soulful, groove-driven, introspective",
        "rhyme": "loose ABCB; chorus should feel like a singalong hook",
    },
    "khmer remix": {
        "structure": "[Intro]\n[Verse 1]\n[Chorus]\n[Verse 2]\n[Chorus]\n[Drop]\n[Final Chorus]",
        "tone": "energetic, modern Khmer youth street culture, TikTok-ready, danceable, mix of emotion and hype",
        "rhyme": "Lines end on matching vowel sounds (not English consonant rhymes). Each line 6-8 syllables. Chorus repeatable in one breath.",
    },
    "khmer": {
        "structure": "[Verse 1]\n[Chorus]\n[Verse 2]\n[Chorus]\n[Bridge]\n[Final Chorus]",
        "tone": "heartfelt, smooth, natural-sounding Khmer everyday speech rhythm, emotionally direct",
        "rhyme": "Lines end on matching vowel sounds (e.g. ា រ ។ patterns). Lines 2 and 4 of each verse rhyme. 6-8 syllables per line.",
    },
    "tiktok remix": {
        "structure": "[Hook]\n[Verse 1]\n[Hook]\n[Verse 2]\n[Hook]\n[Outro]",
        "tone": "viral, punchy, fast-paced, instantly memorable within 15 seconds",
        "rhyme": "AABB; hook must be a single repeatable phrase",
    },
}

_DEFAULT_PROFILE = {
    "structure": "[Intro]\n[Verse 1]\n[Chorus]\n[Verse 2]\n[Bridge]\n[Final Chorus]\n[Ending]",
    "tone": "emotional, modern, professional, singable",
    "rhyme": "ABAB verse, AABB chorus",
}

# Language-specific rules injected when language matches, regardless of style.
_LANGUAGE_INSTRUCTIONS = {
    "khmer": (
        "KHMER LANGUAGE RULES (mandatory):\n"
        "- Write ALL lyrics in Khmer Unicode script (ខ្មែរ). Do NOT use Roman transliteration.\n"
        "- Each line must have 6 to 8 syllables for natural singability.\n"
        "- Rhyme is based on matching final VOWEL SOUNDS (e.g. ា, ិ, ុ, ើ endings), NOT on English-style end-consonant rhymes.\n"
        "- Lines 2 and 4 of every verse must share the same final vowel sound.\n"
        "- Use everyday conversational Khmer vocabulary — the kind spoken by young people in Phnom Penh, not formal or literary Khmer.\n"
        "- Ground the story in recognizable Khmer life: rainy season, riverside, street food, family home, province roads, phone calls late at night, waiting at a bus station.\n"
        "- Do NOT literally translate English idioms. Use Khmer equivalents or imagery that feels natural to a Khmer speaker.\n"
        "- Avoid overusing loan words from French or English (unless the style is Khmer Remix, where 1-2 hook words in English is acceptable).\n"
        "- The chorus must feel like something a Cambodian listener would naturally hum or sing along to."
    ),
}


def _get_style_profile(style: str) -> dict:
    key = (style or "").strip().lower()
    for profile_key, profile in _STYLE_PROFILES.items():
        if profile_key in key:
            return profile
    return _DEFAULT_PROFILE


def _get_language_instructions(language: str) -> str:
    key = (language or "").strip().lower()
    for lang_key, instructions in _LANGUAGE_INSTRUCTIONS.items():
        if lang_key in key:
            return instructions
    return ""


def _generate_song_brief(style, topic, mood, language, description, progress_callback):
    """Phase 1: generate a focused creative brief before writing lyrics."""
    description_line = f'\nUser\'s specific situation: "{description}"' if description else ""
    lang_lower = (language or "").strip().lower()

    # For Khmer, instruct the brief to use Khmer cultural context
    if "khmer" in lang_lower:
        culture_note = (
            "\nIMPORTANT: The song is in Khmer (Cambodian). "
            "The story, imagery, and hook phrase must be rooted in Cambodian everyday life — "
            "real places, feelings, and situations that resonate with a young Cambodian listener. "
            "The hook phrase should be in natural spoken Khmer (write it in Khmer script)."
        )
    else:
        culture_note = ""

    brief_prompt = (
        f"You are a creative director briefing a songwriter for a {style} song in {language}.\n"
        f"Topic: {topic}. Mood: {mood}.{description_line}{culture_note}\n\n"
        f"In exactly 4 short points, define:\n"
        f"1. The SPECIFIC story or situation the song is about (concrete, not abstract — name a real moment, place, or detail)\n"
        f"2. The central IMAGE or METAPHOR that will run through every section\n"
        f"3. The EMOTIONAL ARC: how the feeling changes from verse 1 → chorus → bridge → final chorus\n"
        f"4. The HOOK PHRASE: one killer line (4-8 words) that the listener will remember forever\n\n"
        f"Be specific. No generic ideas. No clichés."
    )

    if progress_callback:
        progress_callback("⏳ Generating lyrics...\nCrafting song concept...")

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a sharp creative director for hit songwriters. You give brutally specific, non-generic briefs."},
            {"role": "user", "content": brief_prompt},
        ],
        temperature=0.85,
        max_tokens=250,
    )
    return response.choices[0].message.content.strip()


def generate_lyrics(style, topic, mood, language, description="", progress_callback=None):

    description = str(description or "").strip()
    profile = _get_style_profile(style)
    language_instructions = _get_language_instructions(language)

    last_error = None

    if progress_callback:
        progress_callback("⏳ Generating lyrics...\nPreparing prompt...")

    # Phase 1 — creative brief
    try:
        brief = _generate_song_brief(style, topic, mood, language, description, progress_callback)
    except Exception:
        # If brief generation fails, fall back to a minimal anchor so Phase 2 still runs
        brief = f"A {mood} {style} song about {topic} in {language}."

    # Build the description anchor — give it strong weight when provided
    if description:
        story_anchor = (
            f"\nCRITICAL — The user's exact situation:\n"
            f'"{description}"\n'
            f"Every section must be grounded in this. Use specific details, feelings, and moments from it.\n"
            f"Do NOT write generic lyrics that could apply to anyone else.\n"
        )
    else:
        story_anchor = ""

    prompt = f"""You are an award-winning songwriter writing a {style} song in {language}.

CREATIVE BRIEF (your anchor for the whole song):
{brief}
{story_anchor}
SONG DETAILS:
- Style: {style}
- Topic: {topic}
- Mood: {mood}
- Language: {language}

TONE & APPROACH: {profile["tone"]}
RHYME SCHEME: {profile["rhyme"]}

SONGWRITING ORDER — follow this to build maximum impact:
1. Write the [Chorus] / [Hook] FIRST — this is the most important part. It must contain the hook phrase from the brief.
2. Write [Verse 1] to set up the emotion that leads INTO the chorus.
3. Write [Verse 2] to deepen the story with new detail or perspective.
4. Write the [Bridge] as an emotional peak or unexpected twist.
5. Fill in [Intro] and [Ending] last.

STRUCTURE TO OUTPUT:
{profile["structure"]}

{language_instructions}
HARD RULES:
- ALL lyrics must be in {language} only. Zero mixing of other languages.
- Section headers stay in English: [Verse 1], [Chorus], etc.
- Verse lines 2 and 4 must rhyme. Chorus lines must end-rhyme per the scheme above.
- No filler lines ("la la la", "oh oh oh") unless it's a deliberate hook device.
- No line may be a cliché ("you light up my world", "dancing in the rain") — replace with the specific imagery from the brief.
- Each verse must contain at least one concrete detail (a name, place, object, or specific moment).
- The chorus must be short enough to memorize in one listen (max 4–6 lines).
"""

    for attempt in range(1, LYRICS_RETRY_ATTEMPTS + 1):
        try:
            if progress_callback:
                if attempt == 1:
                    progress_callback("⏳ Generating lyrics...\nWriting song...")
                else:
                    progress_callback(f"⏳ Generating lyrics...\nRetrying ({attempt}/{LYRICS_RETRY_ATTEMPTS})...")

            lang_lower = (language or "").strip().lower()
            system_msg = (
                "You are an award-winning songwriter. "
                "You write specific, vivid, emotionally authentic lyrics. "
                "You never use clichés. Every line earns its place."
            )
            if "khmer" in lang_lower:
                system_msg = (
                    "You are an award-winning Khmer songwriter based in Phnom Penh. "
                    "You have written hit songs for Cambodian artists for 15 years. "
                    "You write in natural, flowing Khmer script (ខ្មែរ) that sounds smooth when sung — "
                    "never stilted, never like a translation. "
                    "You understand Khmer vowel-based rhyme, 6-8 syllable line rhythm, "
                    "and what imagery resonates deeply with Cambodian listeners."
                )

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.92,
                max_tokens=1100,
            )
            if progress_callback:
                progress_callback("✅ Lyrics generated 100%")
            return response.choices[0].message.content
        except Exception as exc:
            last_error = exc
            if attempt == LYRICS_RETRY_ATTEMPTS or not _is_retryable_openai_error(exc):
                raise

            time.sleep(LYRICS_RETRY_DELAY_SECONDS * attempt)

    raise last_error or Exception("Lyrics generation failed")


# -----------------------------------
# TRANSLATE LYRICS
# -----------------------------------
def translate_lyrics(lyrics, source_language, target_language, progress_callback=None):
    if progress_callback:
        progress_callback(f"⏳ Translating lyrics to {target_language}...")

    prompt = (
        f"You are a professional songwriter and translator.\n\n"
        f"Translate and adapt the following song lyrics from {source_language} to {target_language}.\n\n"
        f"Requirements:\n"
        f"- Preserve the song structure (verses, chorus, bridge labels)\n"
        f"- Keep the emotional meaning and feeling intact\n"
        f"- Make the lyrics sound natural and singable in {target_language}\n"
        f"- Match the syllable rhythm as closely as possible\n"
        f"- Output ONLY the translated lyrics, no explanations\n\n"
        f"Original Lyrics:\n{lyrics}"
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": "You are an expert lyricist and translator who specializes in making song lyrics sound natural and singable across languages.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=800,
    )

    if progress_callback:
        progress_callback(f"✅ Lyrics translated to {target_language}")

    return response.choices[0].message.content
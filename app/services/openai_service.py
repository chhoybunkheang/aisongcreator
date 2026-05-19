import json
import re
from difflib import SequenceMatcher

from openai import OpenAI

from app.config.settings import OPENAI_API_KEY

# -----------------------------------
# OPENAI CLIENT
# -----------------------------------
client = OpenAI(api_key=OPENAI_API_KEY)


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

    final_end = usable_segments[-1]["end"]
    for index, item in enumerate(aligned_lines[:-1]):
        next_start = aligned_lines[index + 1]["start"]
        item["end"] = round(max(min(item["end"], next_start), item["start"] + 0.2), 3)

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

    final_end = words[-1]["end"]
    for index, item in enumerate(aligned_lines[:-1]):
        next_start = aligned_lines[index + 1]["start"]
        item["end"] = round(max(min(item["end"], next_start), item["start"] + 0.15), 3)

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


def generate_subtitle_timing(mp3_path, lyrics, language=""):
    lyric_lines = _clean_lyric_lines(lyrics)
    if not lyric_lines:
        return []

    transcription_language = _normalize_transcription_language(language)
    transcription_prompt = (
        "Transcribe the sung lyrics with timestamps. Keep the original words and order. "
        "Return the best possible alignment for each sung phrase."
    )

    with open(mp3_path, "rb") as audio_file:
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

    words = _extract_transcription_words(response_data)
    aligned_lines = _align_lyric_lines_to_words(lyric_lines, words)
    if aligned_lines:
        return aligned_lines

    segments = response_data.get("segments") or []
    return _align_lyric_lines_to_segments(lyric_lines, segments)


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
        "khmer": None,
        "cambodian": None,
        "km": None,
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

    formatted_text = response.choices[0].message.content.strip()
    return formatted_text or text


def transcribe_lyrics_from_mp3(mp3_path, language=""):

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

        repaired_text = repair_response.choices[0].message.content.strip()
        if repaired_text:
            text = repaired_text

    text = _format_transcribed_lyrics(text, language)

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
def generate_lyrics(style, topic, mood, language):

    prompt = f"""
    You are a professional songwriter.

    Create a COMPLETE high-quality song.

    Song Information:
    - Style: {style}
    - Topic: {topic}
    - Mood: {mood}
    - Language: {language}

    Requirements:
    - Write ALL lyrics strictly in {language} language only
    - Do NOT mix any other language into the lyrics
    - Create emotional and memorable lyrics
    - Make the chorus catchy
    - Use natural wording
    - Make it sound modern and professional
    - Add strong storytelling
    - Avoid repeating lines too much
    - Make it suitable for singing
    - Use clean formatting
    - Keep section headers in English (e.g. [Verse 1], [Chorus]) but the lyrics content must be in {language}

    Song Structure:
    [Intro]
    [Verse 1]
    [Chorus]
    [Verse 2]
    [Bridge]
    [Final Chorus]
    [Ending]

    Special Instructions:

    If style is Khmer Remix:
    - make the chorus energetic
    - use modern Khmer youth style
    - suitable for TikTok remix
    - make it exciting and danceable

    If mood is sad:
    - use emotional heartbreak wording

    If mood is happy:
    - use uplifting and exciting wording

    If mood is romantic:
    - make lyrics sweet and emotional
    """

    response = client.chat.completions.create(
        model="gpt-4.1-mini",

        messages=[
            {
                "role": "system",
                "content": """
                You are an award-winning songwriter
                and music producer.

                You specialize in:
                - emotional lyrics
                - catchy choruses
                - modern song structures
                - viral music styles
                - Khmer remix music
                - TikTok-style songs
                """
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0.9,
        max_tokens=800
    )

    return response.choices[0].message.content
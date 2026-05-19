import asyncio
import html
import os
import re
import time
import uuid

import requests
from dotenv import load_dotenv
from moviepy import AudioFileClip, CompositeAudioClip

load_dotenv()

_api_key = os.getenv("SUNO_API_KEY")
_api_url = os.getenv("SUNO_API_URL")

if not _api_key:
    raise Exception("SUNO_API_KEY missing")

if not _api_url:
    raise Exception("SUNO_API_URL missing")

API_KEY: str = _api_key
API_URL: str = _api_url
KHMER_MALE_VOICE = "km-KH-PisethNeural"
KHMER_FEMALE_VOICE = "km-KH-SreymomNeural"
TARGET_MP3_BITRATE = "128k"
PIAPI_CREATE_RETRIES = 3
PIAPI_POLL_RETRIES = 3
PIAPI_RETRY_DELAY_SECONDS = 3


def _contains_khmer(text):
    return any("\u1780" <= char <= "\u17ff" for char in text)


def _clean_lyrics_for_tts(lyrics):
    text = re.sub(r"\[[^\]]+\]", "", lyrics)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _normalize_singer_gender(singer_gender):
    normalized = (singer_gender or "").strip().lower()
    if normalized in {"male", "female"}:
        return normalized
    return "female"


def _summarize_api_response(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return "No response body."

    condensed = re.sub(r"<[^>]+>", " ", raw_text)
    condensed = html.unescape(condensed)
    condensed = re.sub(r"\s+", " ", condensed).strip()

    if not condensed:
        return "Received an HTML error page from the music API."

    if len(condensed) > 240:
        condensed = condensed[:237] + "..."

    return condensed


def _is_retryable_status(status_code):
    return status_code in {502, 503, 504}


def _raise_api_error(prefix, response_text, status_code):
    summary = _summarize_api_response(response_text)
    raise Exception(f"{prefix} ({status_code}). {summary}")


def _optimize_mp3_file(mp3_path):
    if not mp3_path or not os.path.exists(mp3_path):
        return mp3_path

    optimized_path = f"{os.path.splitext(mp3_path)[0]}_optimized.mp3"
    original_size = os.path.getsize(mp3_path)
    audio_clip = None

    try:
        audio_clip = AudioFileClip(mp3_path)
        audio_clip.write_audiofile(
            optimized_path,
            codec="mp3",
            bitrate=TARGET_MP3_BITRATE,
            fps=44100,
            logger=None,
        )
        optimized_size = os.path.getsize(optimized_path)

        if optimized_size < original_size:
            os.replace(optimized_path, mp3_path)
            print(
                f"[INFO] MP3 optimized: {original_size} -> {optimized_size} bytes "
                f"at {TARGET_MP3_BITRATE}."
            )
        else:
            os.remove(optimized_path)
            print(
                f"[INFO] MP3 optimization skipped; optimized file was not smaller "
                f"({optimized_size} >= {original_size})."
            )
    except Exception as exc:
        if os.path.exists(optimized_path):
            try:
                os.remove(optimized_path)
            except OSError:
                pass
        print(f"[WARNING] MP3 optimization skipped: {exc}")
    finally:
        if audio_clip is not None:
            audio_clip.close()

    return mp3_path


async def _save_edge_tts_mp3(text, mp3_path, singer_gender="female"):
    import edge_tts

    last_error = None
    preferred_voices = [
        KHMER_MALE_VOICE if _normalize_singer_gender(singer_gender) == "male" else KHMER_FEMALE_VOICE,
        KHMER_FEMALE_VOICE if _normalize_singer_gender(singer_gender) == "male" else KHMER_MALE_VOICE,
    ]

    for voice in preferred_voices:
        try:
            communicate = edge_tts.Communicate(text, voice=voice)
            await communicate.save(mp3_path)
            return
        except Exception as exc:
            last_error = exc

    raise Exception(f"Khmer TTS failed: {last_error}")


def _generate_khmer_mp3(lyrics, singer_gender="female"):
    cleaned_lyrics = _clean_lyrics_for_tts(lyrics)
    if not cleaned_lyrics:
        raise Exception("Khmer lyrics are empty after cleanup")

    os.makedirs("media/generated/songs", exist_ok=True)
    mp3_path = f"media/generated/songs/{uuid.uuid4()}.mp3"
    asyncio.run(_save_edge_tts_mp3(cleaned_lyrics, mp3_path, singer_gender=singer_gender))
    print("[SUCCESS] Khmer MP3 saved:", mp3_path)
    return mp3_path


def _extract_audio_url(output):
    audio_url = output.get("audio_url")

    if not audio_url:
        clips = output.get("clips")
        if clips and len(clips) > 0:
            audio_url = clips[0].get("audio_url")

    if not audio_url:
        songs = output.get("songs")
        if songs and len(songs) > 0:
            audio_url = songs[0].get("song_path")

    if not audio_url:
        raise Exception(f"No audio URL found in output: {output}")

    return audio_url


def _download_audio_file(audio_url, file_stem, progress_callback=None):
    os.makedirs("media/generated/songs", exist_ok=True)
    mp3_path = f"media/generated/songs/{file_stem}.mp3"

    print("\n========== DOWNLOAD MP3 ==========")
    if progress_callback:
        progress_callback("⏳ Generating MP3...\nDownloading audio file...")

    try:
        audio_data = requests.get(audio_url, timeout=120)
        print("[DEBUG] MP3 HTTP Status:", audio_data.status_code)
        audio_data.raise_for_status()
    except Exception as e:
        raise Exception(f"Failed to download MP3: {e}")

    with open(mp3_path, "wb") as f:
        f.write(audio_data.content)

    print("[SUCCESS] MP3 saved:", mp3_path)
    return mp3_path


def _run_piapi_music_task(payload, progress_callback=None):
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

    print("\n========== CREATE TASK ==========")
    print("[DEBUG] API URL:", API_URL)
    print("[DEBUG] Payload:", payload)
    if progress_callback:
        progress_callback("⏳ Generating MP3...\nSubmitting request to music server...")

    response = None
    for attempt in range(1, PIAPI_CREATE_RETRIES + 1):
        try:
            response = requests.post(
                API_URL,
                json=payload,
                headers=headers,
                timeout=60
            )
        except Exception as e:
            if attempt == PIAPI_CREATE_RETRIES:
                raise Exception(f"Failed to connect to music API: {e}")

            print(f"[WARNING] Music API connection attempt {attempt} failed: {e}")
            time.sleep(PIAPI_RETRY_DELAY_SECONDS)
            continue

        if _is_retryable_status(response.status_code) and attempt < PIAPI_CREATE_RETRIES:
            if progress_callback:
                progress_callback(
                    f"⏳ Generating MP3...\nMusic server is busy. Retrying create request ({attempt}/{PIAPI_CREATE_RETRIES})..."
                )
            print(
                f"[WARNING] Music API returned {response.status_code} on create attempt "
                f"{attempt}. Retrying..."
            )
            time.sleep(PIAPI_RETRY_DELAY_SECONDS)
            continue

        break

    if response is None:
        raise Exception("Music API did not return a response.")

    print("[DEBUG] HTTP Status:", response.status_code)
    print("[DEBUG] Response Text:", response.text)

    if response.status_code >= 500:
        _raise_api_error("Music API server error", response.text, response.status_code)

    if response.status_code >= 400:
        _raise_api_error("Music API request error", response.text, response.status_code)

    try:
        data = response.json()
    except Exception:
        raise Exception(f"Invalid JSON response: {response.text}")

    print("[DEBUG] Parsed JSON:", data)

    if "data" not in data:
        raise Exception(f"Response missing 'data': {data}")

    task_id = data["data"].get("task_id")

    if not task_id:
        raise Exception(f"No task_id returned: {data}")

    print(f"[INFO] Task created successfully: {task_id}")
    if progress_callback:
        progress_callback("⏳ Generating MP3...\nQueued on music server...")

    max_attempts = 60
    attempt = 0

    while attempt < max_attempts:
        print(f"\n========== POLL {attempt + 1} ==========")

        check_response = None
        for poll_attempt in range(1, PIAPI_POLL_RETRIES + 1):
            try:
                check_response = requests.get(
                    f"{API_URL}/{task_id}",
                    headers=headers,
                    timeout=60
                )
            except Exception as e:
                if poll_attempt == PIAPI_POLL_RETRIES:
                    raise Exception(f"Polling request failed: {e}")

                print(f"[WARNING] Poll request attempt {poll_attempt} failed: {e}")
                time.sleep(PIAPI_RETRY_DELAY_SECONDS)
                continue

            if _is_retryable_status(check_response.status_code) and poll_attempt < PIAPI_POLL_RETRIES:
                print(
                    f"[WARNING] Music API returned {check_response.status_code} on poll attempt "
                    f"{poll_attempt}. Retrying..."
                )
                time.sleep(PIAPI_RETRY_DELAY_SECONDS)
                continue

            break

        if check_response is None:
            raise Exception("Polling request did not return a response.")

        print("[DEBUG] Poll HTTP Status:", check_response.status_code)
        print("[DEBUG] Poll Response:", check_response.text)

        if check_response.status_code >= 500:
            _raise_api_error("Music API polling error", check_response.text, check_response.status_code)

        if check_response.status_code >= 400:
            _raise_api_error("Music API polling request error", check_response.text, check_response.status_code)

        try:
            result = check_response.json()
        except Exception:
            raise Exception(f"Invalid polling JSON: {check_response.text}")

        print("[DEBUG] Parsed Poll JSON:", result)

        if "data" not in result:
            raise Exception(f"Polling response missing data: {result}")

        status = result["data"].get("status")
        print(f"[POLL] Status = {status}")

        if progress_callback:
            status_text = str(status or "queued").replace("_", " ").title()
            progress_callback(
                f"⏳ Generating MP3...\nMusic server status: {status_text} (check {attempt + 1}/{max_attempts})"
            )

        if status == "completed":
            output = result["data"].get("output")
            if not output:
                raise Exception("Generation completed but no output found")

            print("[DEBUG] Output:", output)
            audio_url = _extract_audio_url(output)
            print("[SUCCESS] Audio URL:", audio_url)
            return _download_audio_file(audio_url, task_id, progress_callback=progress_callback)

        if status == "failed":
            error_obj = result["data"].get("error")
            if isinstance(error_obj, dict):
                error_msg = error_obj.get("message", "Unknown error")
            else:
                error_msg = str(error_obj)

            raise Exception(f"Music generation failed: {error_msg}")

        if status not in ["pending", "processing", "running", "queued"]:
            print(f"[WARNING] Unknown status: {status}")
        else:
            print("[INFO] Still generating...")

        attempt += 1
        time.sleep(10)

    raise Exception("Music generation timed out")


def _generate_khmer_instrumental(style, mood, progress_callback=None):
    payload = {
        "model": "Qubico/ace-step",
        "task_type": "txt2audio",
        "input": {
            "style_prompt": (
                f"instrumental only, no vocals, cambodian pop inspired, "
                f"{style.lower()}, {mood.lower()}"
            ),
            "negative_style_prompt": "vocals, singing, speech, lyrics, voice",
            "lyrics": "[inst]",
            "duration": 180,
        },
        "config": {
            "webhook_config": {
                "endpoint": "",
                "secret": ""
            }
        }
    }

    return _run_piapi_music_task(payload, progress_callback=progress_callback)


def _mix_voice_with_music(voice_path, music_path):
    mixed_path = f"media/generated/songs/{uuid.uuid4()}.mp3"
    voice_clip = AudioFileClip(voice_path)
    music_clip = AudioFileClip(music_path)

    try:
        background_clip = (
            music_clip
            .subclipped(0, voice_clip.duration)
            .with_duration(voice_clip.duration)
            .with_volume_scaled(0.18)
        )
        mixed_clip = CompositeAudioClip([background_clip, voice_clip])
        mixed_clip.write_audiofile(mixed_path, fps=44100, codec="mp3")
    finally:
        voice_clip.close()
        music_clip.close()
        try:
            mixed_clip.close()
        except Exception:
            pass

    print("[SUCCESS] Khmer music MP3 saved:", mixed_path)
    return mixed_path


def _generate_khmer_song(style, mood, lyrics, singer_gender="female"):
    try:
        voice_path = _generate_khmer_mp3(lyrics, singer_gender=singer_gender)
        instrumental_path = _generate_khmer_instrumental(style, mood)
        return _mix_voice_with_music(voice_path, instrumental_path)
    except Exception as exc:
        print(f"[WARNING] Khmer TTS/mix path failed, falling back to standard music API: {exc}")
        return None


def _build_standard_music_payload(style, mood, lyrics, language="", singer_gender="female"):
    lang_lower = language.lower() if language else ""
    normalized_gender = _normalize_singer_gender(singer_gender)
    vocal_prompt = f"{normalized_gender} singer, {normalized_gender} vocals"

    if "khmer" in lang_lower or "cambodian" in lang_lower:
        style_prompt = (
            f"Khmer language, Cambodian pop, {vocal_prompt}, khmer vocals, sing in Khmer, "
            f"{style.lower()}, {mood.lower()}"
        )
        negative_style_prompt = "english vocals, english lyrics, english language"
    elif language:
        style_prompt = (
            f"{language} language, {vocal_prompt}, {lang_lower} vocals, sing in {language}, "
            f"{style.lower()}, {mood.lower()}"
        )
        negative_style_prompt = "english vocals, english lyrics"
    else:
        style_prompt = f"{vocal_prompt}, {style.lower()}, {mood.lower()}"
        negative_style_prompt = ""

    ace_lyrics = re.sub(
        r"\[([^\]]+)\]",
        lambda m: "[" + m.group(1).lower().split()[0] + "]",
        lyrics
    )

    return {
        "model": "Qubico/ace-step",
        "task_type": "txt2audio",
        "input": {
            "style_prompt": style_prompt,
            "negative_style_prompt": negative_style_prompt,
            "lyrics": ace_lyrics,
            "duration": 180,
        },
        "config": {
            "webhook_config": {
                "endpoint": "",
                "secret": ""
            }
        }
    }


def generate_music(style, topic, mood, lyrics, language="", singer_gender="female", progress_callback=None):
    lang_lower = language.lower() if language else ""

    if "khmer" in lang_lower or "cambodian" in lang_lower or _contains_khmer(lyrics):
        print("[INFO] Khmer request detected, using Khmer voice + music fallback")
        if progress_callback:
            progress_callback("⏳ Generating MP3...\nPreparing Khmer vocals...")
        khmer_result = _generate_khmer_song(style, mood, lyrics, singer_gender=singer_gender)
        if khmer_result:
            return _optimize_mp3_file(khmer_result)

    payload = _build_standard_music_payload(style, mood, lyrics, language, singer_gender=singer_gender)
    return _optimize_mp3_file(_run_piapi_music_task(payload, progress_callback=progress_callback))
import io
import os
import re
import uuid

import httpx
from openai import OpenAI
from PIL import Image

from app.config.settings import GENERATED_COVERS_DIR, OPENAI_API_KEY

# -----------------------------------
# OPENAI CLIENT
# Build with longer timeout and optional proxy from env
# -----------------------------------
_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
_http_client = httpx.Client(
    proxy=_proxy,
    timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=10.0),
)
client = OpenAI(api_key=OPENAI_API_KEY, http_client=_http_client)

_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds between retries
_MAX_COVER_SIZE = (768, 768)
_COVER_QUALITY = 86
_MAX_LYRIC_LINES = 4
_MAX_LINE_LENGTH = 140


def _clean_prompt_value(value, max_length=240):
    cleaned = re.sub(r"\s+", " ", (value or "")).strip()
    return cleaned[:max_length]


def _save_optimized_cover(image_data):
    os.makedirs(GENERATED_COVERS_DIR, exist_ok=True)
    filename = f"{uuid.uuid4()}.jpg"
    filepath = os.path.join(GENERATED_COVERS_DIR, filename)

    with Image.open(io.BytesIO(image_data)) as image:
        optimized = image.convert("RGB")
        optimized.thumbnail(_MAX_COVER_SIZE)
        optimized.save(
            filepath,
            format="JPEG",
            quality=_COVER_QUALITY,
            optimize=True,
            progressive=True,
        )

    return filepath


def _build_lyric_excerpt(lyrics):
    cleaned_lines = []

    for raw_line in (lyrics or "").splitlines():
        line = (raw_line or "").strip()
        if not line:
            continue
        if re.fullmatch(r"\[[^\]]+\]", line):
            continue

        compact_line = re.sub(r"\s+", " ", line)
        cleaned_lines.append(compact_line[:_MAX_LINE_LENGTH])
        if len(cleaned_lines) >= _MAX_LYRIC_LINES:
            break

    if not cleaned_lines:
        return ""

    return "\n".join(f"- {line}" for line in cleaned_lines)


def _build_cover_prompt(topic, mood, style, description, lyrics, language):
    lyric_excerpt = _build_lyric_excerpt(lyrics)
    description = _clean_prompt_value(description, max_length=320)
    language = _clean_prompt_value(language, max_length=80)
    mood = _clean_prompt_value(mood, max_length=120)
    topic = _clean_prompt_value(topic, max_length=180)
    style = _clean_prompt_value(style, max_length=120)

    contextual_sections = [
        f"Song Topic:\n{topic}",
        f"Mood:\n{mood or 'Not specified'}",
        f"Music Style:\n{style}",
    ]

    if language:
        contextual_sections.append(f"Song Language:\n{language}")

    if description:
        contextual_sections.append(f"Extra Song Context:\n{description}")

    if lyric_excerpt:
        contextual_sections.append(f"Lyric Excerpt:\n{lyric_excerpt}")

    return f"""
    Create a beautiful professional music cover image that fits this specific song, not a generic music poster.

    {chr(10).join(contextual_sections)}

    Requirements:
    - reflect the exact emotional tone, setting, and symbolism implied by the song details
    - if lyrics suggest a scene, relationship, memory, place, or time of day, show that visually
    - prefer one strong visual concept over a random collage
    - cinematic
    - emotional
    - high quality
    - modern music cover style
    - suitable for Spotify
    - attractive composition
    - detailed lighting
    - avoid unrelated microphones, instruments, or performers unless strongly implied by the song
    - no text
    - no logos
    - no watermark
    """


def _build_safe_cover_prompt(topic, mood, style, language):
    topic = _clean_prompt_value(topic, max_length=120)
    mood = _clean_prompt_value(mood, max_length=80)
    style = _clean_prompt_value(style, max_length=80)
    language = _clean_prompt_value(language, max_length=60)

    contextual_sections = [
        f"Song Topic:\n{topic or 'Original song'}",
        f"Mood:\n{mood or 'Emotional'}",
        f"Music Style:\n{style or 'Contemporary'}",
    ]

    if language:
        contextual_sections.append(f"Song Language:\n{language}")

    return f"""
    Create a professional, symbolic album cover inspired only by the high-level song mood and genre.

    {chr(10).join(contextual_sections)}

    Safety Requirements:
    - keep the image non-graphic, non-explicit, and suitable for a general audience
    - use metaphor, atmosphere, lighting, color, scenery, or abstract symbolism instead of depicting sensitive details literally
    - avoid violence, injuries, nudity, sexual content, drugs, self-harm, hate symbols, or illegal activity

    Creative Requirements:
    - cinematic
    - emotional
    - modern music cover style
    - one clear visual concept
    - no text
    - no logos
    - no watermark
    """


def _is_moderation_blocked_error(error):
    message = str(error).lower()
    if "moderation_blocked" in message or "safety system" in message:
        return True

    code = getattr(error, "code", None)
    if code == "moderation_blocked":
        return True

    error_body = getattr(error, "body", None)
    if isinstance(error_body, dict):
        nested_error = error_body.get("error") or {}
        nested_code = nested_error.get("code")
        nested_message = str(nested_error.get("message", "")).lower()
        if nested_code == "moderation_blocked" or "safety system" in nested_message:
            return True

    return False


# -----------------------------------
# GENERATE COVER IMAGE
# -----------------------------------
def generate_cover_image(
    topic,
    mood,
    style,
    description="",
    lyrics="",
    language="",
    progress_callback=None,
):

    prompt = _build_cover_prompt(topic, mood, style, description, lyrics, language)
    fallback_prompt = _build_safe_cover_prompt(topic, mood, style, language)

    import time
    start_time = time.time()

    last_error = None
    used_fallback_prompt = False
    if progress_callback:
        progress_callback("⏳ Generating cover image...\nPreparing image prompt...")

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if progress_callback:
                if attempt == 1:
                    progress_callback("⏳ Generating cover image...\nSending request to image engine...")
                else:
                    progress_callback(
                        f"⏳ Generating cover image...\nRetrying image request ({attempt}/{_MAX_RETRIES})..."
                    )
            print(f"[INFO] Starting OpenAI image generation (attempt {attempt}/{_MAX_RETRIES})...")
            response = client.images.generate(
                model="gpt-image-1",
                prompt=fallback_prompt if used_fallback_prompt else prompt,
                size="1024x1024"
            )
            print(f"[INFO] OpenAI image API call completed in {time.time() - start_time:.2f}s.")

            # Only handle base64 image (b64_json)
            if hasattr(response, "data") and response.data and hasattr(response.data[0], "b64_json") and response.data[0].b64_json:
                import base64
                if progress_callback:
                    progress_callback("⏳ Generating cover image...\nOptimizing generated image...")
                print("[INFO] Decoding base64 image data...")
                image_data = base64.b64decode(response.data[0].b64_json)
                filepath = _save_optimized_cover(image_data)
                print(f"[INFO] Writing image to {filepath}...")
                print(f"[INFO] Total image generation time: {time.time() - start_time:.2f}s.")
                if progress_callback:
                    progress_callback("✅ Cover image generated 100%")
                return filepath

            print("[ERROR] OpenAI image generation failed. Response:", response)
            raise ValueError("OpenAI did not return a valid base64 image.")

        except httpx.ConnectError as e:
            last_error = e
            print(f"[WARN] Connection failed (attempt {attempt}/{_MAX_RETRIES}): {e}")
            if attempt < _MAX_RETRIES:
                if progress_callback:
                    progress_callback(
                        f"⏳ Generating cover image...\nConnection issue. Retrying in {_RETRY_DELAY}s..."
                    )
                print(f"[INFO] Retrying in {_RETRY_DELAY}s...")
                time.sleep(_RETRY_DELAY)
        except Exception as e:
            if not used_fallback_prompt and _is_moderation_blocked_error(e):
                used_fallback_prompt = True
                if progress_callback:
                    progress_callback(
                        "⏳ Generating cover image...\nOriginal prompt was blocked. Retrying with safer cover prompt..."
                    )
                print("[WARN] Image prompt blocked by safety system. Retrying with safer fallback prompt...")
                continue
            import traceback
            print(f"[ERROR] Exception in generate_cover_image: {e}")
            traceback.print_exc()
            raise

    import traceback
    print(f"[ERROR] All {_MAX_RETRIES} connection attempts failed: {last_error}")
    raise httpx.ConnectError(
        f"Failed to connect to OpenAI after {_MAX_RETRIES} attempts. "
        f"Check your internet connection or set HTTPS_PROXY in .env.\n{last_error}"
    )

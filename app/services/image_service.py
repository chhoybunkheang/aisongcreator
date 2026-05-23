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

    lyric_excerpt = _build_lyric_excerpt(lyrics)
    description = (description or "").strip()
    language = (language or "").strip()
    mood = (mood or "").strip()

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

    prompt = f"""
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

    import time
    start_time = time.time()

    last_error = None
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
                prompt=prompt,
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

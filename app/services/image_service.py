import io
import os
import uuid

import httpx
from openai import OpenAI
from PIL import Image

from app.config.settings import OPENAI_API_KEY

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


def _save_optimized_cover(image_data):
    os.makedirs("media/generated/covers", exist_ok=True)
    filename = f"{uuid.uuid4()}.jpg"
    filepath = os.path.join("media/generated/covers", filename)

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


# -----------------------------------
# GENERATE COVER IMAGE
# -----------------------------------
def generate_cover_image(
    topic,
    mood,
    style
):

    prompt = f"""
    Create a beautiful professional music cover image.

    Song Topic:
    {topic}

    Mood:
    {mood}

    Music Style:
    {style}

    Requirements:
    - cinematic
    - emotional
    - high quality
    - modern music cover style
    - suitable for Spotify
    - attractive composition
    - detailed lighting
    - no text
    """

    import time
    start_time = time.time()

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
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
                print("[INFO] Decoding base64 image data...")
                image_data = base64.b64decode(response.data[0].b64_json)
                filepath = _save_optimized_cover(image_data)
                print(f"[INFO] Writing image to {filepath}...")
                print(f"[INFO] Total image generation time: {time.time() - start_time:.2f}s.")
                return filepath

            print("[ERROR] OpenAI image generation failed. Response:", response)
            raise ValueError("OpenAI did not return a valid base64 image.")

        except httpx.ConnectError as e:
            last_error = e
            print(f"[WARN] Connection failed (attempt {attempt}/{_MAX_RETRIES}): {e}")
            if attempt < _MAX_RETRIES:
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

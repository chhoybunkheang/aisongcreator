import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.getenv("APP_BASE_DIR", os.getcwd()))
DATA_ROOT = os.path.abspath(os.getenv("DATA_ROOT", os.path.join(BASE_DIR, "data")))
MEDIA_ROOT = os.path.abspath(os.getenv("MEDIA_ROOT", os.path.join(BASE_DIR, "media")))
GENERATED_MEDIA_ROOT = os.path.join(MEDIA_ROOT, "generated")
GENERATED_COVERS_DIR = os.path.join(GENERATED_MEDIA_ROOT, "covers")
GENERATED_SONGS_DIR = os.path.join(GENERATED_MEDIA_ROOT, "songs")
GENERATED_VIDEOS_DIR = os.path.join(GENERATED_MEDIA_ROOT, "videos")
SQLITE_DB_PATH = os.path.join(DATA_ROOT, "users.db")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
	DATABASE_URL = f"sqlite:///{SQLITE_DB_PATH.replace(os.sep, '/')}"
elif DATABASE_URL.startswith("postgres://"):
	DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split("://", 1)[0]:
	DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PAYMENT_QR_IMAGE = os.getenv("PAYMENT_QR_IMAGE", "")
PAYMENT_ACCOUNT_NUMBER = os.getenv("PAYMENT_ACCOUNT_NUMBER", "012345678")
PAYMENT_ACCOUNT_NAME = os.getenv("PAYMENT_ACCOUNT_NAME", "YOUR NAME")
PAYMENT_SCREENSHOT_AI_ENABLED = os.getenv("PAYMENT_SCREENSHOT_AI_ENABLED", "true").strip().lower() in {
	"1",
	"true",
	"yes",
	"on",
}

if PAYMENT_QR_IMAGE and not os.path.isabs(PAYMENT_QR_IMAGE) and not PAYMENT_QR_IMAGE.startswith(("http://", "https://")):
	PAYMENT_QR_IMAGE = os.path.join(BASE_DIR, PAYMENT_QR_IMAGE)

BOT_USERNAME_LABEL = f"@{BOT_USERNAME}" if BOT_USERNAME else "AI Song Bot"
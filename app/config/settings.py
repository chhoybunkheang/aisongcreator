import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
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
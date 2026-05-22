import importlib
import logging
import os
import sys
import warnings

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOGGER = logging.getLogger(__name__)

# Suppress the per_message PTBUserWarning — we intentionally use per_message=False
# with CallbackQueryHandler inside ConversationHandler (conversation is tracked per-user).
warnings.filterwarnings(
    "ignore",
    message=".*per_message=False.*CallbackQueryHandler.*",
    category=UserWarning,
)

REQUIRED_MODULES = [
    ("telegram", "python-telegram-bot"),
    ("openai", "openai"),
    ("sqlalchemy", "SQLAlchemy"),
    ("dotenv", "python-dotenv"),
    ("requests", "requests"),
    ("moviepy", "moviepy"),
    ("PIL", "pillow"),
    ("imageio_ffmpeg", "imageio-ffmpeg"),
]


def verify_runtime_environment():
    print(f"[INFO] Python executable: {sys.executable}")

    expected_venv = os.path.join(
        ".venv",
        "Scripts" if os.name == "nt" else "bin",
        "python.exe" if os.name == "nt" else "python",
    )
    normalized_executable = os.path.abspath(sys.executable).lower().replace("\\", "/")
    normalized_expected = os.path.abspath(expected_venv).lower().replace("\\", "/")

    if os.path.exists(expected_venv) and normalized_expected not in normalized_executable:
        print("[WARN] You are not using the project virtual environment.")
        print("[WARN] Recommended command:")
        print(f'[WARN] "{expected_venv}" "run.py"')

    missing = []
    for module_name, package_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(package_name)

    if missing:
        unique_missing = sorted(set(missing), key=str.lower)
        print("[ERROR] Missing required packages:", ", ".join(unique_missing))
        print("[ERROR] Install them with:")
        print(f'[ERROR] "{expected_venv}" -m pip install -r "requirements.txt"')
        return False

    return True


def main():
    if not verify_runtime_environment():
        return

    from telegram import BotCommand
    from telegram import error as tg_error
    from telegram.ext import ApplicationBuilder, MessageHandler, filters

    from app.config.settings import BOT_TOKEN
    from app.database.db import engine, migrate_db
    from app.database.models import Base
    from app.handlers.admin import (
        approve_callback_handler,
        approve_handler,
        reject_callback_handler,
    )
    from app.handlers.buycredits import (
        buycredits_handler,
        payment_handler,
        receive_payment,
    )
    from app.handlers.help import settings_action_handler, settings_handler
    from app.handlers.mysongs import (
        add_subtitle_handler,
        lyrics_detail_handler,
        mp3_actions_handler,
        mp3_to_lyrics_handler,
        mp3_video_prompt_handler,
        ms_cov_handler,
        ms_cov_upload_handler,
        ms_mp3_handler,
        ms_receive_uploaded_cover,
        ms_skip_handler,
        ms_vid_animation_handler,
        ms_vid_choice_handler,
        ms_vid_handler,
        mylyrics_handler,
        mymp3_handler,
        mymp4_handler,
        mysongs_handler,
        play_mp3_handler,
        song_detail_handler,
        watch_video_handler,
    )
    from app.handlers.song import song_handler
    from app.handlers.start import start_handler

    async def photo_router(update, context):
        user_data = context.user_data or {}

        if user_data.get("payment_qr_package") or user_data.get("buy_credits"):
            await receive_payment(update, context)
            return

        if user_data.get("ms_cover_song_id"):
            await ms_receive_uploaded_cover(update, context)
            return

    async def error_handler(update, context):
        error = context.error

        if isinstance(error, (tg_error.NetworkError, tg_error.TimedOut)):
            LOGGER.warning("Telegram network error: %s", error)
            return

        LOGGER.error(
            "Unhandled bot error",
            exc_info=(type(error), error, error.__traceback__),
        )

    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("start", "Refresh bot menu"),
        ])

    if not BOT_TOKEN:
        print("[ERROR] BOT_TOKEN is missing. Set it in your .env file.")
        return

    # Create database tables
    Base.metadata.create_all(bind=engine)
    migrate_db()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .pool_timeout(300)
        .post_init(post_init)
        .build()
    )

    app.add_handler(start_handler)
    app.add_handler(song_handler)
    app.add_handler(mysongs_handler)
    app.add_handler(mylyrics_handler)
    app.add_handler(mymp3_handler)
    app.add_handler(mymp4_handler)
    app.add_handler(settings_handler)
    app.add_handler(settings_action_handler)
    app.add_handler(song_detail_handler)
    app.add_handler(lyrics_detail_handler)
    app.add_handler(ms_mp3_handler)
    app.add_handler(ms_cov_handler)
    app.add_handler(ms_cov_upload_handler)
    app.add_handler(ms_vid_handler)
    app.add_handler(ms_vid_animation_handler)
    app.add_handler(ms_vid_choice_handler)
    app.add_handler(ms_skip_handler)
    app.add_handler(mp3_actions_handler)
    app.add_handler(mp3_to_lyrics_handler)
    app.add_handler(mp3_video_prompt_handler)
    app.add_handler(play_mp3_handler)
    app.add_handler(watch_video_handler)
    app.add_handler(add_subtitle_handler)
    app.add_handler(buycredits_handler)
    from app.handlers.help import settings_text_handler
    app.add_handler(settings_text_handler)
    app.add_handler(payment_handler)
    app.add_handler(MessageHandler(filters.PHOTO, photo_router))
    app.add_handler(approve_callback_handler)
    app.add_handler(reject_callback_handler)
    app.add_handler(approve_handler)
    app.add_error_handler(error_handler)

    print("✅ Bot is running...")

    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("[INFO] Bot stopped.")


if __name__ == "__main__":
    main()
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.database.queries import (
    create_user,
    get_user,
)
from app.keyboards.main_menu import get_main_menu


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    telegram_id = update.effective_user.id

    create_user(
        telegram_id=telegram_id,
        name=update.effective_user.first_name
    )

    user = get_user(telegram_id)

    await update.message.reply_text(
        f"🎵 Welcome to AI Song Bot!\n\n"
        f"💎 Credits: {user.credits}\n\n"
        f"Choose an option below:",
        reply_markup=get_main_menu()
    )

start_handler = CommandHandler("start", start)
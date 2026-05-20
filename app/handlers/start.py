from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.database.queries import (
    create_user,
    get_user,
)
from app.keyboards.main_menu import get_main_menu
from app.utils.helpers import replace_flow_message, safe_delete_message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    telegram_id = update.effective_user.id

    create_user(
        telegram_id=telegram_id,
        name=update.effective_user.first_name
    )

    user = get_user(telegram_id)

    await replace_flow_message(
        context,
        update.message.reply_text,
        f"🎵 Welcome to AI Song Bot!\n\n"
        f"Start here with {user.credits} full song credit.\n\n"
        f"💎 Full song credits: {user.credits}\n\n"
        f"Choose an option below:",
        reply_markup=get_main_menu(),
        state_key="start_flow_message_id",
    )

    await safe_delete_message(update.message)

start_handler = CommandHandler("start", start)
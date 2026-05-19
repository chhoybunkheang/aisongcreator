from datetime import datetime

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from app.config.settings import ADMIN_ID
from app.database.queries import (
    add_credits,
)


async def _approve_payment_request(message, bot, user_id, credits):

    success = add_credits(user_id, credits)

    if not success:
        await message.reply_text("❌ User not found.")
        return

    await bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ Payment approved!\n\n"
            f"💎 Added Credits: {credits}"
        )
    )


def _payment_status_text(status_text, actor_name):

    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"{status_text}\n"
        f"👤 Processed by: {actor_name}\n"
        f"🕒 Processed at: {processed_at}"
    )


async def _mark_payment_request(message, status_text, actor_name):

    caption = getattr(message, "caption", "") or ""
    final_status_text = _payment_status_text(status_text, actor_name)
    updated_caption = f"{caption}\n\n{final_status_text}" if caption else final_status_text
    await message.edit_caption(caption=updated_caption, reply_markup=None)


async def _reject_payment_request(message, bot, user_id):

    await bot.send_message(
        chat_id=user_id,
        text=(
            "❌ Payment rejected.\n\n"
            "Please check your payment screenshot and contact admin if needed."
        )
    )
# -----------------------------------
# APPROVE PAYMENT
# -----------------------------------
async def approve_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Admin protection
    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Access denied."
        )

        return

    try:

        # Command format:
        # /approve USER_ID CREDITS

        user_id = context.args[0]
        credits = int(context.args[1])
        await _approve_payment_request(update.message, context.bot, user_id, credits)
        await update.message.reply_text(
            f"✅ Added {credits} credits to {user_id}"
        )

    except Exception:

        await update.message.reply_text(
            "❌ Usage:\n/approve USER_ID CREDITS"
        )


async def approve_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if update.effective_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _prefix, user_id, credits_text = query.data.split("_", 2)
        credits = int(credits_text)
    except (AttributeError, TypeError, ValueError):
        await query.answer("Invalid approval data.", show_alert=True)
        return

    await query.answer("Approving payment...")
    await _approve_payment_request(query.message, context.bot, user_id, credits)
    await _mark_payment_request(
        query.message,
        "✅ Payment approved",
        query.from_user.first_name,
    )


async def reject_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if update.effective_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _prefix, user_id = query.data.split("_", 1)
    except (AttributeError, TypeError, ValueError):
        await query.answer("Invalid rejection data.", show_alert=True)
        return

    await query.answer("Rejecting payment...")
    await _reject_payment_request(query.message, context.bot, user_id)
    await _mark_payment_request(
        query.message,
        "❌ Payment rejected",
        query.from_user.first_name,
    )


# -----------------------------------
# HANDLER
# -----------------------------------
approve_handler = CommandHandler(
    "approve",
    approve_payment
)

approve_callback_handler = CallbackQueryHandler(
    approve_payment_callback,
    pattern=r"^approve_\d+_\d+$"
)

reject_callback_handler = CallbackQueryHandler(
    reject_payment_callback,
    pattern=r"^reject_\d+$"
)
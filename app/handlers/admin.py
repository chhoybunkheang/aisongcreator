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
    process_payment_request,
)


async def _approve_payment_request(message, bot, payment_request_id):

    outcome, payload = process_payment_request(payment_request_id, "approved")

    if outcome == "not_found":
        await message.reply_text("❌ Payment request not found.")
        return outcome

    if outcome == "already_processed":
        await message.reply_text("ℹ️ This payment request was already processed.")
        return outcome

    if outcome == "user_not_found":
        await message.reply_text("❌ User not found.")
        return outcome

    if payload is None:
        await message.reply_text("❌ Could not approve this payment request.")
        return outcome

    user_id = payload["telegram_id"]
    credits = payload["credits"]

    await bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ Payment approved!\n\n"
            f"💎 Added Credits: {credits}"
        )
    )
    return outcome


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


async def _reject_payment_request(message, bot, payment_request_id):

    outcome, payload = process_payment_request(payment_request_id, "rejected")

    if outcome == "not_found":
        await message.reply_text("❌ Payment request not found.")
        return outcome

    if outcome == "already_processed":
        await message.reply_text("ℹ️ This payment request was already processed.")
        return outcome

    if payload is None:
        await message.reply_text("❌ Could not reject this payment request.")
        return outcome

    await bot.send_message(
        chat_id=payload["telegram_id"],
        text=(
            "❌ Payment rejected.\n\n"
            "Please check your payment screenshot and contact admin if needed."
        )
    )
    return outcome
# -----------------------------------
# APPROVE PAYMENT
# -----------------------------------
async def approve_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message
    effective_user = update.effective_user

    if message is None or effective_user is None:
        return

    # Admin protection
    if effective_user.id != ADMIN_ID:

        await message.reply_text(
            "❌ Access denied."
        )

        return

    try:

        # Command format:
        # /approve USER_ID CREDITS

        args = context.args or []
        if len(args) != 2:
            raise ValueError("invalid approve arguments")

        user_id = args[0]
        credits = int(args[1])
        if credits <= 0:
            raise ValueError("credits must be positive")

        if not add_credits(user_id, credits):
            await message.reply_text("❌ User not found.")
            return

        await message.reply_text(
            f"✅ Added {credits} credits to {user_id}"
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ Payment approved!\n\n"
                    f"💎 Added Credits: {credits}"
                )
            )
        except Exception:
            pass

    except Exception:

        await message.reply_text(
            "❌ Usage:\n/approve USER_ID CREDITS"
        )


async def approve_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    effective_user = update.effective_user

    if query is None or effective_user is None or query.message is None:
        return

    if effective_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _prefix, payment_request_id = (query.data or "").split("_", 1)
    except (AttributeError, TypeError, ValueError):
        await query.answer("Invalid approval data.", show_alert=True)
        return

    await query.answer("Approving payment...")
    outcome = await _approve_payment_request(query.message, context.bot, payment_request_id)
    if outcome != "processed":
        return
    await _mark_payment_request(
        query.message,
        "✅ Payment approved",
        getattr(query.from_user, "first_name", "Admin"),
    )


async def reject_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    effective_user = update.effective_user

    if query is None or effective_user is None or query.message is None:
        return

    if effective_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _prefix, payment_request_id = (query.data or "").split("_", 1)
    except (AttributeError, TypeError, ValueError):
        await query.answer("Invalid rejection data.", show_alert=True)
        return

    await query.answer("Rejecting payment...")
    outcome = await _reject_payment_request(query.message, context.bot, payment_request_id)
    if outcome != "processed":
        return
    await _mark_payment_request(
        query.message,
        "❌ Payment rejected",
        getattr(query.from_user, "first_name", "Admin"),
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
    pattern=r"^approve_\d+$"
)

reject_callback_handler = CallbackQueryHandler(
    reject_payment_callback,
    pattern=r"^reject_\d+$"
)
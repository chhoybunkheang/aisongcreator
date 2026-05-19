from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
)

from app.config.settings import ADMIN_ID
from app.database.queries import (
    add_credits,
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

        success = add_credits(user_id, credits)

        if not success:

            await update.message.reply_text(
                "❌ User not found."
            )

            return

        # Notify user
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Payment approved!\n\n"
                f"💎 Added Credits: {credits}"
            )
        )

        # Notify admin
        await update.message.reply_text(
            f"✅ Added {credits} credits to {user_id}"
        )

    except:

        await update.message.reply_text(
            "❌ Usage:\n/approve USER_ID CREDITS"
        )


# -----------------------------------
# HANDLER
# -----------------------------------
approve_handler = CommandHandler(
    "approve",
    approve_payment
)
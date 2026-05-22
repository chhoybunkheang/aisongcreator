from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.database.queries import (
    create_user,
    get_user,
    register_referral_start,
)
from app.keyboards.main_menu import get_main_menu
from app.utils.helpers import replace_flow_message, safe_delete_message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    effective_user = update.effective_user
    message = update.message
    if effective_user is None or message is None:
        return

    telegram_id = effective_user.id

    existing_user = get_user(telegram_id)

    user = create_user(
        telegram_id=telegram_id,
        name=effective_user.first_name
    )

    if user is None:
        await message.reply_text(
            "❌ Unable to start right now. Please try again in a moment."
        )
        return

    referral_result = None
    referred_user_message = ""
    if existing_user is None:
        referral_code = context.args[0].strip() if context.args else ""
        if referral_code.startswith("ref_"):
            inviter_telegram_id = referral_code.replace("ref_", "", 1).strip()
            referral_result = register_referral_start(inviter_telegram_id, telegram_id)
            referred_user_message = (
                "You joined through a referral invite from another user. "
                "You can explore the bot normally. No payment is required to start.\n\n"
            )
            if referral_result.get("status") == "recorded":
                try:
                    await context.bot.send_message(
                        chat_id=int(inviter_telegram_id),
                        text=(
                            "🎁 Referral updated!\n\n"
                            f"Invites: {referral_result['invite_count']}\n"
                            f"Reward: +{referral_result['granted_credits']} credits"
                            if referral_result.get("granted_credits") else
                            "🎁 Referral updated!\n\n"
                            f"Invites: {referral_result['invite_count']}/{referral_result['invites_per_reward']}\n"
                            "Keep inviting new users to earn free credits."
                        ),
                    )
                except Exception:
                    pass

    referral_message = ""
    if referral_result and referral_result.get("granted_credits"):
        referral_message = (
            f"\n🎁 Referral reward unlocked: +{referral_result['granted_credits']} credits to your inviter.\n"
        )

    await replace_flow_message(
        context,
        message.reply_text,
        f"🎵 Welcome to AI Song Bot!\n\n"
        f"{referred_user_message}"
        f"Start here with {user.credits} full song credit.\n\n"
        f"{referral_message}"
        f"💎 Full song credits: {user.credits}\n\n"
        f"Choose an option below:",
        reply_markup=get_main_menu(),
        state_key="start_flow_message_id",
    )

    await safe_delete_message(message)

start_handler = CommandHandler("start", start)
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config.settings import ADMIN_ID


# -----------------------------------
# BUY CREDITS MENU
# -----------------------------------
async def buy_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [

        [
            InlineKeyboardButton(
                "10 Credits - $1",
                callback_data="buy_10"
            )
        ],

        [
            InlineKeyboardButton(
                "50 Credits - $3",
                callback_data="buy_50"
            )
        ],

        [
            InlineKeyboardButton(
                "100 Credits - $5",
                callback_data="buy_100"
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "💎 Buy Credits\n\n"
        "Choose a package:",
        reply_markup=reply_markup
    )


# -----------------------------------
# PAYMENT INSTRUCTIONS
# -----------------------------------
async def payment_info(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    package = query.data

    if package == "buy_10":
        credits = 10
        price = "$1"

    elif package == "buy_50":
        credits = 50
        price = "$3"

    else:
        credits = 100
        price = "$5"

    context.user_data["buy_credits"] = credits

    await query.message.reply_text(
        f"💳 Payment Instructions\n\n"
        f"Package: {credits} Credits\n"
        f"Price: {price}\n\n"
        f"Send payment to:\n"
        f"ABA: 012345678\n"
        f"Name: YOUR NAME\n\n"
        f"Then upload payment screenshot here."
    )
# -----------------------------------
# RECEIVE PAYMENT SCREENSHOT
# -----------------------------------
async def receive_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # Check if photo exists
    if not update.message.photo:

        await update.message.reply_text(
            "❌ Please upload a payment screenshot."
        )

        return

    telegram_id = update.effective_user.id
    username = update.effective_user.first_name

    credits = context.user_data.get("buy_credits", 0)

    # Get largest image
    photo = update.message.photo[-1]

    file_id = photo.file_id

    caption = (
        f"💳 New Payment Request\n\n"
        f"👤 User: {username}\n"
        f"🆔 Telegram ID: {telegram_id}\n"
        f"💎 Requested Credits: {credits}\n\n"
        f"Approve using:\n"
        f"/approve {telegram_id} {credits}"
    )

    # Send to admin
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=caption
    )

    await update.message.reply_text(
        "✅ Payment screenshot submitted.\n\n"
        "Please wait for admin approval."
    )

# -----------------------------------
# HANDLERS
# -----------------------------------
buycredits_handler = MessageHandler(
    filters.TEXT & filters.Regex(r"^💎 Add Credit$"),
    buy_credits
)

payment_handler = CallbackQueryHandler(
    payment_info,
    pattern="^buy_"
)
receipt_handler = MessageHandler(
    filters.PHOTO,
    receive_payment
)
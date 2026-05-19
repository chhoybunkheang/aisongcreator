from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram import error as tg_error
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config.settings import (
    ADMIN_ID,
    PAYMENT_ACCOUNT_NAME,
    PAYMENT_ACCOUNT_NUMBER,
    PAYMENT_QR_IMAGE,
)
from app.database.queries import (
    get_payment_qr_file_id,
    update_payment_qr_file_id,
)
from app.utils.helpers import replace_flow_message


def _package_details(package_code):

    package_map = {
        "buy_10": (10, "$1"),
        "buy_50": (50, "$3"),
        "buy_100": (100, "$5"),
    }

    return package_map.get(package_code, (100, "$5"))


async def _safe_delete_message(message):

    try:
        await message.delete()
    except tg_error.BadRequest:
        pass


async def _send_payment_qr(message, credits, price):

    caption = (
        f"💳 Scan To Pay\n\n"
        f"Package: {credits} Credits\n"
        f"Price: {price}\n\n"
        f"After payment, please take a screenshot and send it here. "
        f"The admin will review and approve your credits."
    )

    package_qr_file_id = get_payment_qr_file_id(credits)
    if package_qr_file_id:
        await message.reply_photo(
            photo=package_qr_file_id,
            caption=caption,
        )
        return True

    qr_image = PAYMENT_QR_IMAGE.strip()
    if not qr_image:
        return False

    if qr_image.startswith(("http://", "https://")):
        await message.reply_photo(
            photo=qr_image,
            caption=caption,
        )
        return True

    qr_path = Path(qr_image)
    if not qr_path.is_absolute():
        qr_path = Path.cwd() / qr_path

    if not qr_path.exists():
        return False

    with qr_path.open("rb") as qr_file:
        await message.reply_photo(
            photo=qr_file,
            caption=caption,
        )

    return True


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

    await replace_flow_message(
        context,
        update.message.reply_text,
        "💎 Buy Credits\n\n"
        "Choose a package:",
        reply_markup=reply_markup,
        state_key="buycredits_flow_message_id",
    )


# -----------------------------------
# PAYMENT INSTRUCTIONS
# -----------------------------------
async def payment_info(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    package = query.data
    credits, price = _package_details(package)
    source_message = query.message

    context.user_data["buy_credits"] = credits

    qr_sent = await _send_payment_qr(source_message, credits, price)

    await _safe_delete_message(source_message)

    if qr_sent:
        return

    payment_text = (
        f"💳 Payment Instructions\n\n"
        f"Package: {credits} Credits\n"
        f"Price: {price}\n\n"
        f"Account: {PAYMENT_ACCOUNT_NUMBER}\n"
        f"Name: {PAYMENT_ACCOUNT_NAME}\n\n"
    )

    if qr_sent:
        payment_text += (
            "Scan the QR code above, complete the payment, and upload your payment "
            "screenshot here so the admin can approve your credits."
        )
    else:
        payment_text += (
            "Scan the payment QR code and upload your payment screenshot here so the "
            "admin can approve your credits.\n\n"
            "Note: no QR image is configured yet. Set PAYMENT_QR_IMAGE in .env to show it here."
        )

    await context.bot.send_message(
        chat_id=source_message.chat_id,
        text=payment_text,
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

    pending_qr_package = context.user_data.get("payment_qr_package")
    if update.effective_user.id == ADMIN_ID and pending_qr_package:
        qr_photo = update.message.photo[-1]
        update_payment_qr_file_id(pending_qr_package, qr_photo.file_id)
        context.user_data.pop("payment_qr_package", None)

        await update.message.reply_text(
            f"✅ QR image saved for {pending_qr_package} credits.\n\n"
            "Users who choose this package will now see this QR image.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Back To Payment Setup", callback_data="settings_payment")],
            ])
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
        f"💎 Requested Credits: {credits}"
    )

    # Send to admin
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=caption,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve_{telegram_id}_{credits}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_{telegram_id}"
                )
            ]
        ])
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
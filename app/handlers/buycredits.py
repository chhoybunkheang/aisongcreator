import asyncio
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
    PAYMENT_SCREENSHOT_AI_ENABLED,
)
from app.database.queries import (
    create_payment_request,
    get_payment_qr_file_id,
    update_payment_qr_file_id,
)
from app.services.openai_service import analyze_payment_screenshot
from app.utils.helpers import replace_flow_message


def _package_details(package_code):

    package_map = {
        "buy_10": (10, "$1"),
        "buy_50": (50, "$3"),
        "buy_100": (100, "$5"),
    }

    return package_map.get(package_code, (100, "$5"))


def _payment_method_details(method_code):

    method_map = {
        "qr": "Scan QR",
        "bank": "Bank App",
    }

    return method_map.get(method_code, "Scan QR")


def _package_price(credits):

    price_map = {
        10: "$1",
        50: "$3",
        100: "$5",
    }

    return price_map.get(credits, "$5")


def _format_ai_review(review):
    status_map = {
        "approve": "Approve",
        "review": "Manual Review",
        "reject": "Reject",
        "unavailable": "Unavailable",
    }

    lines = [
        "🤖 AI Receipt Check",
        f"Recommendation: {status_map.get(review.get('status'), 'Manual Review')}",
        f"Confidence: {review.get('confidence', 0)}%",
        f"Summary: {review.get('summary', 'No summary provided.')}",
    ]

    amount_found = review.get("amount_found")
    if amount_found:
        lines.append(f"Amount found: {amount_found}")

    reference = review.get("reference")
    if reference:
        lines.append(f"Reference: {reference}")

    reasons = review.get("reasons") or []
    if reasons:
        lines.append("Checks: " + " | ".join(reasons))

    return "\n".join(lines)


def _payment_method_markup(package_code):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📷 Scan QR",
                callback_data=f"payment_qr_{package_code}",
            ),
            InlineKeyboardButton(
                "🏦 Bank App",
                callback_data=f"payment_bank_{package_code}",
            ),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="buycredits_menu")],
    ])


async def _analyze_receipt_photo(photo, credits, payment_method):
    if not PAYMENT_SCREENSHOT_AI_ENABLED:
        return {
            "status": "unavailable",
            "confidence": 0,
            "summary": "AI receipt check is disabled.",
            "amount_found": "",
            "reference": "",
            "reasons": [],
        }

    telegram_file = await photo.get_file()
    image_bytes = await telegram_file.download_as_bytearray()

    return await asyncio.to_thread(
        analyze_payment_screenshot,
        bytes(image_bytes),
        credits,
        _package_price(credits),
        payment_method,
    )


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

    message = update.message
    if message is None:
        return

    user_data = context.user_data
    if user_data is not None:
        user_data.pop("buy_credits", None)
        user_data.pop("buy_credits_method", None)

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
        message.reply_text,
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
    if query is None:
        return

    source_message = query.message
    if source_message is None:
        return

    query_data = query.data or ""

    user_data = context.user_data
    if user_data is None:
        return

    if query_data == "buycredits_menu":
        await query.answer()
        await query.edit_message_text(
            "💎 Buy Credits\n\nChoose a package:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("10 Credits - $1", callback_data="buy_10")],
                [InlineKeyboardButton("50 Credits - $3", callback_data="buy_50")],
                [InlineKeyboardButton("100 Credits - $5", callback_data="buy_100")],
            ]),
        )
        return

    if query_data.startswith("buy_"):
        await query.answer()
        credits, price = _package_details(query_data)
        await query.edit_message_text(
            f"💳 Payment Options\n\n"
            f"Package: {credits} Credits\n"
            f"Price: {price}\n\n"
            "Choose how you want to pay:",
            reply_markup=_payment_method_markup(query_data),
        )
        return

    if not query_data.startswith("payment_"):
        return

    _, payment_method, package_code = query_data.split("_", 2)
    credits, price = _package_details(package_code)

    if payment_method == "bank":
        await query.answer("Coming Soon", show_alert=True)
        return

    await query.answer()

    user_data["buy_credits"] = credits
    user_data["buy_credits_method"] = "qr"

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

    source_chat = getattr(source_message, "chat", None)
    if source_chat is None:
        return

    await context.bot.send_message(chat_id=source_chat.id, text=payment_text)
# -----------------------------------
# RECEIVE PAYMENT SCREENSHOT
# -----------------------------------
async def receive_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message
    if message is None:
        return

    user_data = context.user_data
    if user_data is None:
        return

    # Check if photo exists
    if not message.photo:

        await message.reply_text(
            "❌ Please upload a payment screenshot."
        )

        return

    effective_user = update.effective_user
    if effective_user is None:
        return

    pending_qr_package = user_data.get("payment_qr_package")
    if effective_user.id == ADMIN_ID and pending_qr_package:
        qr_photo = message.photo[-1]
        update_payment_qr_file_id(pending_qr_package, qr_photo.file_id)
        user_data.pop("payment_qr_package", None)

        await message.reply_text(
            f"✅ QR image saved for {pending_qr_package} credits.\n\n"
            "Users who choose this package will now see this QR image.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 Back To QR Payment Setup", callback_data="settings_payment")],
            ])
        )
        return

    telegram_id = effective_user.id
    username = effective_user.first_name

    credits = user_data.get("buy_credits", 0)
    payment_method = _payment_method_details(user_data.get("buy_credits_method", "qr"))

    # Get largest image
    photo = message.photo[-1]

    file_id = photo.file_id
    file_unique_id = photo.file_unique_id

    payment_request = create_payment_request(
        telegram_id=telegram_id,
        credits=credits,
        payment_method=payment_method,
        receipt_file_id=file_id,
        receipt_file_unique_id=file_unique_id,
    )

    try:
        ai_review = await _analyze_receipt_photo(photo, credits, payment_method)
    except Exception:
        ai_review = {
            "status": "unavailable",
            "confidence": 0,
            "summary": "AI receipt check failed.",
            "amount_found": "",
            "reference": "",
            "reasons": [],
        }

    caption = (
        f"💳 New Payment Request\n\n"
        f"👤 User: {username}\n"
        f"🆔 Telegram ID: {telegram_id}\n"
        f"💰 Payment Method: {payment_method}\n"
        f"💎 Requested Credits: {credits}\n\n"
        f"{_format_ai_review(ai_review)}"
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
                    callback_data=f"approve_{payment_request.id}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_{payment_request.id}"
                )
            ]
        ])
    )

    user_data.pop("buy_credits", None)
    user_data.pop("buy_credits_method", None)

    await message.reply_text(
        "✅ Payment screenshot submitted.\n\n"
        "The receipt was sent for AI pre-check and admin approval."
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
    pattern=r"^(buy_|payment_|buycredits_menu$)"
)
receipt_handler = MessageHandler(
    filters.PHOTO,
    receive_payment
)
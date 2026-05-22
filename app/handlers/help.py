
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Place this after all imports and function/class definitions
# ...existing code...
# ...existing code...
# Place this after all function/class definitions
from app.config.settings import ADMIN_ID
from app.database.queries import (
    DEFAULT_SONG_LANGUAGES,
    delete_payment_qr_file_id,
    delete_song_lyrics,
    delete_song_mp3,
    delete_song_mp4,
    get_enabled_song_languages,
    get_payment_qr_file_ids,
    get_user,
    get_user_songs,
    reset_user_song_data,
    set_credits,
    update_enabled_song_languages,
)
from app.utils.helpers import replace_flow_message


def _settings_menu_keyboard():
    return _settings_menu_keyboard_for_user(False)


def _settings_menu_keyboard_for_user(is_admin):
    rows = [
        [InlineKeyboardButton("ℹ️ Info", callback_data="settings_info")],
        [InlineKeyboardButton("🗑 Delete", callback_data="settings_delete")],
        [InlineKeyboardButton("♻️ Reset", callback_data="settings_reset")],
    ]

    if is_admin:
        rows.append([
            InlineKeyboardButton("💎 Credit Status", callback_data="settings_credit_status")
        ])
        rows.append([InlineKeyboardButton("🌍 Languages", callback_data="settings_languages")])
        rows.append([InlineKeyboardButton("📷 QR Payment", callback_data="settings_payment")])

    return InlineKeyboardMarkup(rows)


def _settings_delete_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Lyrics", callback_data="settings_delete_lyrics")],
        [InlineKeyboardButton("🎵 MP3", callback_data="settings_delete_mp3")],
        [InlineKeyboardButton("🎬 MP4", callback_data="settings_delete_mp4")],
        [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
    ])


def _settings_delete_list_keyboard(items, item_type):
    keyboard = [
        [
            InlineKeyboardButton(
                f"{item.topic} {_language_flag(item.language)}",
                callback_data=f"settings_delete_item_{item_type}_{item.id}"
            )
        ]
        for item in items
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="settings_delete")])
    return InlineKeyboardMarkup(keyboard)


def _settings_reset_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Reset", callback_data="settings_reset_confirm")],
        [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
    ])


def _settings_info_text(user_obj, tg_user):
    credits = user_obj.credits if user_obj else 0
    return (
        "⚙️ Settings\n\n"
        f"Name: {tg_user.first_name}\n"
        f"Telegram ID: {tg_user.id}\n"
        f"💎 Credits: {credits}"
    )


def _language_flag(language):
    normalized = (language or "").strip().lower()
    if normalized in {"khmer", "cambodian", "km", "kh"}:
        return "🇰🇭"
    if normalized in {"english", "en"}:
        return "🇺🇸"
    if normalized in {"vietnamese", "vietnam", "vi", "vn"}:
        return "🇻🇳"
    if normalized in {"chinese", "zh", "cn", "mandarin"}:
        return "🇨🇳"
    if normalized in {"japanese", "ja", "jp"}:
        return "🇯🇵"
    return "🌐"


def _settings_language_keyboard(enabled_languages):
    rows = []
    enabled_set = set(enabled_languages)

    for language in DEFAULT_SONG_LANGUAGES:
        prefix = "✅" if language in enabled_set else "⬜"
        rows.append([
            InlineKeyboardButton(
                f"{prefix} {_language_flag(language)} {language}",
                callback_data=f"settings_lang_toggle_{language}"
            )
        ])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="settings_back")])
    return InlineKeyboardMarkup(rows)


def _settings_payment_keyboard(qr_file_ids):
    rows = []

    for package_credits, price in ((10, "$1"), (50, "$3"), (100, "$5")):
        configured = "✅" if qr_file_ids.get(str(package_credits)) else "⬜"
        rows.append([
            InlineKeyboardButton(
                f"{configured} {package_credits} Credits - {price}",
                callback_data=f"settings_payment_pkg_{package_credits}"
            )
        ])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="settings_back")])
    return InlineKeyboardMarkup(rows)


def _settings_payment_package_keyboard(package_credits, has_qr):
    rows = [
        [InlineKeyboardButton("🖼 Upload / Replace QR", callback_data=f"settings_payment_upload_{package_credits}")],
    ]

    if has_qr:
        rows.insert(0, [InlineKeyboardButton("👁 View Current QR", callback_data=f"settings_payment_view_{package_credits}")])
        rows.append([InlineKeyboardButton("🗑 Remove QR", callback_data=f"settings_payment_remove_{package_credits}")])

    rows.append([InlineKeyboardButton("⬅️ Back To QR Payment", callback_data="settings_payment")])
    return InlineKeyboardMarkup(rows)


def _deletable_items(telegram_id, item_type):
    songs = get_user_songs(telegram_id)
    print(f"[DEBUG] _deletable_items: item_type={item_type}, total_songs={len(songs)}")
    for song in songs:
        print(f"[DEBUG] Song ID: {getattr(song, 'id', None)}, video_path: {getattr(song, 'video_path', None)}, mp3_path: {getattr(song, 'mp3_path', None)}, lyrics: {bool(getattr(song, 'lyrics', None))}")

    if item_type == "lyrics":
        items = [song for song in songs if getattr(song, "lyrics", None)]
    elif item_type == "mp3":
        items = [song for song in songs if getattr(song, "mp3_path", None)]
    elif item_type == "mp4":
        items = [song for song in songs if getattr(song, "video_path", None)]
    else:
        items = []
    print(f"[DEBUG] _deletable_items: found {len(items)} items for type {item_type}")
    return items


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_user = update.effective_user
    message = update.message
    if effective_user is None or message is None:
        return

    is_admin = effective_user.id == ADMIN_ID
    await replace_flow_message(
        context,
        message.reply_text,
        "⚙️ Settings\n\nChoose an option:",
        reply_markup=_settings_menu_keyboard_for_user(is_admin),
        state_key="settings_flow_message_id",
    )


async def settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE):

    print("[DEBUG] settings_action handler triggered")
    if update.callback_query:
        print(f"[DEBUG] callback_data: {update.callback_query.data}")
    query = update.callback_query
    user_data = context.user_data
    if query is None:
        message = update.message
        if (
            message and
            update.effective_user and
            user_data is not None and
            update.effective_user.id == ADMIN_ID and
            user_data.get("settings_waiting_for_credit_amount")
        ):
            print(f"[DEBUG] Received credit input: '{message.text}' user_data: {user_data}")
            text = (message.text or "").strip()
            if text.isdigit():
                amount = int(text)
                set_credits(ADMIN_ID, amount)
                await message.reply_text(f"✅ Admin credits set to {amount}.")
                user_data.pop("settings_waiting_for_credit_amount", None)
            else:
                await message.reply_text("❌ Please enter a valid number (digits only). Try again:")
        return

    if user_data is None or query.from_user is None:
        return

    await query.answer()
    chat = update.effective_chat
    telegram_id = query.from_user.id
    is_admin = telegram_id == ADMIN_ID
    query_data = query.data or ""

    # Admin credit status and set prompt
    if is_admin and query_data == "settings_credit_status":
        admin_user = get_user(ADMIN_ID)
        current_credits = admin_user.credits if admin_user else 0
        await query.edit_message_text(
            f"💎 Admin Credit Status\n\nCurrent credits: {current_credits}\n\nEnter a new credit amount to set:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")]
            ])
        )
        user_data["settings_waiting_for_credit_amount"] = True
        return

    if query_data == "settings_back":
        user_data.pop("payment_qr_package", None)
        await query.edit_message_text(
            "⚙️ Settings\n\nChoose an option:",
            reply_markup=_settings_menu_keyboard_for_user(is_admin)
        )
        return

    if query_data == "settings_info":
        user = get_user(telegram_id)
        try:
            await query.edit_message_text(
                _settings_info_text(user, query.from_user),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
                ])
            )
        except Exception as e:
            print(f"[settings_info] edit_message_text failed: {e}")
            if chat is None:
                return
            await context.bot.send_message(
                chat_id=chat.id,
                text=_settings_info_text(user, query.from_user),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
                ])
            )
        return

    if query_data == "settings_delete":
        await query.edit_message_text(
            "🗑 Delete\n\nChoose what you want to delete:",
            reply_markup=_settings_delete_keyboard()
        )
        return

    if query_data == "settings_reset":
        await query.edit_message_text(
            "♻️ Reset\n\nThis will delete all your lyrics, MP3, and MP4 data.",
            reply_markup=_settings_reset_keyboard()
        )
        return

    if query_data == "settings_languages":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        enabled_languages = get_enabled_song_languages()
        await query.edit_message_text(
            "🌍 Visible Song Languages\n\nChoose which languages users can see in Create Song:",
            reply_markup=_settings_language_keyboard(enabled_languages)
        )
        return

    if query_data == "settings_payment":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        user_data.pop("payment_qr_package", None)
        qr_file_ids = get_payment_qr_file_ids()
        await query.edit_message_text(
            "📷 QR Payment Setup\n\nChoose a package to upload or replace its QR image:",
            reply_markup=_settings_payment_keyboard(qr_file_ids)
        )
        return

    if query_data.startswith("settings_payment_pkg_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        package_credits = int(query_data.rsplit("_", 1)[1])
        user_data["payment_qr_package"] = package_credits
        qr_file_ids = get_payment_qr_file_ids()
        has_qr = bool(qr_file_ids.get(str(package_credits)))
        await query.edit_message_text(
            f"💳 QR Setup For {package_credits} Credits\n\n"
            "You can send a QR image now, or use the buttons below.",
            reply_markup=_settings_payment_package_keyboard(package_credits, has_qr)
        )
        return

    if query_data.startswith("settings_payment_upload_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        package_credits = int(query_data.rsplit("_", 1)[1])
        user_data["payment_qr_package"] = package_credits
        await query.edit_message_text(
            f"💳 Upload QR For {package_credits} Credits\n\n"
            "Send one QR image now. The uploaded image will be shown to users who choose this package.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back To Package", callback_data=f"settings_payment_pkg_{package_credits}")],
            ])
        )
        return

    if query_data.startswith("settings_payment_view_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        package_credits = int(query_data.rsplit("_", 1)[1])
        qr_file_id = get_payment_qr_file_ids().get(str(package_credits), "")
        if not qr_file_id:
            await query.answer("No QR image saved for this package.", show_alert=True)
            return

        if chat is None:
            return

        await context.bot.send_photo(
            chat_id=chat.id,
            photo=qr_file_id,
            caption=f"Current QR for {package_credits} credits.",
        )
        await query.edit_message_text(
            f"💳 QR Setup For {package_credits} Credits\n\n"
            "Current QR shown above.",
            reply_markup=_settings_payment_package_keyboard(package_credits, True)
        )
        return

    if query_data.startswith("settings_payment_remove_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        package_credits = int(query_data.rsplit("_", 1)[1])
        deleted = delete_payment_qr_file_id(package_credits)
        await query.edit_message_text(
            (
                f"✅ Removed QR for {package_credits} credits."
                if deleted else f"No QR was saved for {package_credits} credits."
            ),
            reply_markup=_settings_payment_package_keyboard(package_credits, False)
        )
        return

    if query_data.startswith("settings_lang_toggle_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        selected_language = query_data.replace("settings_lang_toggle_", "", 1)
        enabled_languages = get_enabled_song_languages()

        if selected_language in enabled_languages:
            if len(enabled_languages) == 1:
                await query.answer("At least one language must stay enabled.", show_alert=True)
                return
            enabled_languages = [language for language in enabled_languages if language != selected_language]
        else:
            enabled_languages.append(selected_language)

        ordered_languages = [
            language for language in DEFAULT_SONG_LANGUAGES
            if language in enabled_languages
        ]
        update_enabled_song_languages(ordered_languages)
        await query.edit_message_text(
            "🌍 Visible Song Languages\n\nChoose which languages users can see in Create Song:",
            reply_markup=_settings_language_keyboard(ordered_languages)
        )
        return

    if query_data in {"settings_delete_lyrics", "settings_delete_mp3", "settings_delete_mp4"}:
        try:
            item_type = query_data.replace("settings_delete_", "")
            items = _deletable_items(telegram_id, item_type)
            print(f"[DEBUG] settings_action: delete {item_type}, items={items}")

            if not items:
                await query.edit_message_text(
                    f"No {item_type.upper()} items found.",
                    reply_markup=_settings_delete_keyboard()
                )
                return

            await query.edit_message_text(
                f"Select one {item_type.upper()} item to delete:",
                reply_markup=_settings_delete_list_keyboard(items, item_type)
            )
            return
        except Exception as e:
            import traceback
            print(f"[ERROR] Exception in delete item logic: {e}\n{traceback.format_exc()}")
            await query.edit_message_text(
                f"❌ An error occurred while loading your {query_data.replace('settings_delete_', '').upper()} items. Please contact support.",
                reply_markup=_settings_delete_keyboard()
            )
            return

    if query_data.startswith("settings_delete_item_"):
        _, _, _, item_type, song_id = query_data.split("_", 4)
        song_id = int(song_id)

        if item_type == "lyrics":
            deleted = delete_song_lyrics(song_id, telegram_id)
        elif item_type == "mp3":
            deleted = delete_song_mp3(song_id, telegram_id)
        else:
            deleted = delete_song_mp4(song_id, telegram_id)

        items = _deletable_items(telegram_id, item_type)
        if not items:
            await query.edit_message_text(
                f"✅ Deleted selected {item_type.upper()} item." if deleted else f"Selected {item_type.upper()} item was not found.",
                reply_markup=_settings_delete_keyboard()
            )
            return

        await query.edit_message_text(
            f"✅ Deleted selected {item_type.upper()} item." if deleted else f"Selected {item_type.upper()} item was not found.",
            reply_markup=_settings_delete_list_keyboard(items, item_type)
        )
        return

    if query.data == "settings_reset_confirm":
        deleted_count = reset_user_song_data(telegram_id)
        await query.edit_message_text(
            f"✅ Reset complete. Deleted {deleted_count} song record(s) and related media.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back To Settings", callback_data="settings_back")],
            ])
        )
        return


settings_handler = MessageHandler(
    filters.TEXT & filters.Regex(r"^⚙️ Settings$"),
    settings
)



settings_action_handler = CallbackQueryHandler(settings_action, pattern=r"^settings_")

# Place this after all function/class definitions
settings_text_handler = MessageHandler(
    filters.TEXT & ~filters.COMMAND,
    settings_action
)

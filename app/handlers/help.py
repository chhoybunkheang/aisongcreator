
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Place this after all imports and function/class definitions
# ...existing code...
# ...existing code...
# Place this after all function/class definitions
from app.config.settings import ADMIN_ID
from app.database.queries import (
    DEFAULT_SONG_LANGUAGES,
    add_credits,
    delete_payment_qr_file_id,
    delete_song_lyrics,
    delete_song_mp3,
    delete_song_mp4,
    get_all_user_summaries,
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
              InlineKeyboardButton("💎 Credits", callback_data="settings_credits")
        ])
        rows.append([InlineKeyboardButton("👥 Users", callback_data="settings_users")])
        rows.append([InlineKeyboardButton("🌍 Languages", callback_data="settings_languages")])
        rows.append([InlineKeyboardButton("📷 QR Payment", callback_data="settings_payment")])

    return InlineKeyboardMarkup(rows)


def _settings_credits_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Admin's Credit", callback_data="settings_credit_admin")],
        [InlineKeyboardButton("👤 User's Credit", callback_data="settings_credit_users")],
        [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
    ])


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


def _settings_users_chunks(user_summaries):
    header = f"👥 Bot Users\n\nTotal users: {len(user_summaries)}"
    if not user_summaries:
        return [header + "\n\nNo users found yet."]

    chunks = []
    current_chunk = header

    for index, user_summary in enumerate(user_summaries, start=1):
        created_at = user_summary["created_at"]
        joined_text = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "Unknown"
        entry = (
            f"\n\n{index}. {user_summary['name']}\n"
            f"Telegram ID: {user_summary['telegram_id']}\n"
            f"Credits: {user_summary['credits']}\n"
            f"Songs: {user_summary['song_count']}\n"
            f"Joined: {joined_text}"
        )

        if len(current_chunk) + len(entry) > 3500:
            chunks.append(current_chunk)
            current_chunk = "👥 Bot Users (continued)" + entry
        else:
            current_chunk += entry

    chunks.append(current_chunk)
    return chunks


def _settings_credit_target_keyboard(user_summaries):
    rows = [[InlineKeyboardButton("🌐 All Users", callback_data="settings_credit_target_all")]]

    for user_summary in user_summaries:
        label = f"{user_summary['name']} ({user_summary['telegram_id']})"
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=f"settings_credit_target_user_{user_summary['telegram_id']}"
            )
        ])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="settings_credits")])
    return InlineKeyboardMarkup(rows)


def _settings_credit_action_keyboard(target_type, target_label):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Add/Deduct Credits", callback_data=f"settings_credit_action_{target_type}")],
        [InlineKeyboardButton(f"⬅️ Back To {target_label}", callback_data="settings_credit_users")],
    ])


def _settings_user_credit_target_text(target_name, target_id, current_credits=None):
    credits_line = f"Current credits: {current_credits}\n\n" if current_credits is not None else ""
    return (
        "👤 User Credit Management\n\n"
        f"Selected user: {target_name}\n"
        f"Telegram ID: {target_id}\n\n"
        f"{credits_line}"
        "Tap the button below to update credits."
    )


def _settings_all_users_credit_text(user_count):
    return (
        "🌐 All Users Credit Management\n\n"
        f"Selected target: All users ({user_count})\n\n"
        "Tap the button below to update credits."
    )


def _apply_credit_change(target_scope, amount, user_data):
    if target_scope == "admin":
        return 1 if set_credits(ADMIN_ID, amount) else 0

    if target_scope == "all":
        changed_count = 0
        for user_summary in get_all_user_summaries():
            telegram_id = user_summary["telegram_id"]
            if user_data.get("settings_credit_operation") == "add":
                if add_credits(telegram_id, amount):
                    changed_count += 1
                continue

            current_credits = int(user_summary.get("credits", 0) or 0)
            new_credits = max(current_credits - amount, 0)
            if set_credits(telegram_id, new_credits):
                changed_count += 1
        return changed_count

    target_user_id = user_data.get("settings_credit_target_id")
    target_user = get_user(target_user_id)
    if not target_user:
        return 0

    if user_data.get("settings_credit_operation") == "add":
        return 1 if add_credits(target_user_id, amount) else 0

    current_credits = int(getattr(target_user, "credits", 0) or 0)
    new_credits = max(current_credits - amount, 0)
    return 1 if set_credits(target_user_id, new_credits) else 0


def _clear_credit_settings_state(user_data):
    user_data.pop("settings_waiting_for_credit_amount", None)
    user_data.pop("settings_credit_scope", None)
    user_data.pop("settings_credit_target_id", None)
    user_data.pop("settings_credit_target_name", None)
    user_data.pop("settings_credit_operation", None)


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
            try:
                amount = int(text)
            except ValueError:
                await message.reply_text("❌ Please enter a valid number. Use -number to deduct. Try again:")
                return

            credit_scope = user_data.get("settings_credit_scope")

            if credit_scope == "admin" and amount < 0:
                await message.reply_text("❌ Admin credit must be 0 or higher. Try again:")
                return

            if credit_scope in {"user", "all"} and amount < 0:
                user_data["settings_credit_operation"] = "deduct"
                amount = abs(amount)

            if credit_scope == "admin":
                changed_count = _apply_credit_change("admin", amount, user_data)
                if changed_count:
                    await message.reply_text(f"✅ Admin credits set to {amount}.")
                else:
                    await message.reply_text("❌ Could not update admin credits.")
                _clear_credit_settings_state(user_data)
                return

            if credit_scope in {"user", "all"}:
                changed_count = _apply_credit_change(credit_scope, amount, user_data)
                operation = user_data.get("settings_credit_operation")
                if not changed_count:
                    await message.reply_text("❌ Could not update credits for the selected target.")
                    _clear_credit_settings_state(user_data)
                    return

                if credit_scope == "all":
                    action_text = "added to" if operation == "add" else "deducted from"
                    await message.reply_text(
                        f"✅ {amount} credits {action_text} {changed_count} user(s)."
                    )
                else:
                    target_name = user_data.get("settings_credit_target_name", "Selected user")
                    action_text = "added to" if operation == "add" else "deducted from"
                    await message.reply_text(
                        f"✅ {amount} credits {action_text} {target_name}."
                    )
                _clear_credit_settings_state(user_data)
                return

            await message.reply_text("❌ Credit action is not selected. Please open Settings again.")
            _clear_credit_settings_state(user_data)
            return
        return

    if user_data is None or query.from_user is None:
        return

    chat = update.effective_chat
    telegram_id = query.from_user.id
    is_admin = telegram_id == ADMIN_ID
    query_data = query.data or ""

    if query_data == "settings_credits":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        _clear_credit_settings_state(user_data)
        await query.edit_message_text(
            "💎 Credits\n\nChoose what you want to manage:",
            reply_markup=_settings_credits_keyboard()
        )
        return

    if is_admin and query_data == "settings_credit_admin":
        await query.answer()
        _clear_credit_settings_state(user_data)
        admin_user = get_user(ADMIN_ID)
        current_credits = admin_user.credits if admin_user else 0
        await query.edit_message_text(
            f"💎 Admin Credit\n\nCurrent credits: {current_credits}\n\nEnter a new credit amount to set:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="settings_credits")]
            ])
        )
        user_data["settings_credit_scope"] = "admin"
        user_data["settings_waiting_for_credit_amount"] = True
        return

    if query_data == "settings_credit_users":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        _clear_credit_settings_state(user_data)
        user_summaries = get_all_user_summaries()
        await query.edit_message_text(
            "👤 User Credits\n\nChoose one user or All Users:",
            reply_markup=_settings_credit_target_keyboard(user_summaries)
        )
        return

    if query_data == "settings_credit_target_all":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        _clear_credit_settings_state(user_data)
        user_data["settings_credit_scope"] = "all"
        user_count = len(get_all_user_summaries())
        await query.edit_message_text(
            _settings_all_users_credit_text(user_count),
            reply_markup=_settings_credit_action_keyboard("all", "User Credits")
        )
        return

    if query_data.startswith("settings_credit_target_user_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        _clear_credit_settings_state(user_data)
        target_user_id = query_data.rsplit("_", 1)[1]
        target_user = get_user(target_user_id)
        if not target_user:
            await query.edit_message_text(
                "❌ User not found.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="settings_credit_users")],
                ])
            )
            return

        user_data["settings_credit_scope"] = "user"
        user_data["settings_credit_target_id"] = str(target_user_id)
        user_data["settings_credit_target_name"] = target_user.name or "Unknown"
        await query.edit_message_text(
            _settings_user_credit_target_text(
                user_data["settings_credit_target_name"],
                target_user_id,
                current_credits=target_user.credits or 0,
            ),
            reply_markup=_settings_credit_action_keyboard("user", "User Credits")
        )
        return

    if query_data.startswith("settings_credit_action_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        target_scope = query_data.rsplit("_", 1)[1]
        if target_scope not in {"user", "all"}:
            await query.edit_message_text(
                "❌ Invalid credit action.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="settings_credits")],
                ])
            )
            return

        if target_scope == "user" and not user_data.get("settings_credit_target_id"):
            await query.edit_message_text(
                "❌ No user selected.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="settings_credit_users")],
                ])
            )
            return

        user_data["settings_credit_scope"] = target_scope
        user_data["settings_credit_operation"] = "add"
        user_data["settings_waiting_for_credit_amount"] = True
        target_text = (
            f"all users ({len(get_all_user_summaries())})"
            if target_scope == "all"
            else f"{user_data.get('settings_credit_target_name', 'selected user')} ({user_data.get('settings_credit_target_id', '')})"
        )
        await query.edit_message_text(
            f"💎 User Credits\n\n"
            f"Selected target: {target_text}.\n\n"
            "Enter the credit amount. Positive adds credits, negative deducts credits:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="settings_credit_users")],
            ])
        )
        return

    if query_data == "settings_users":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
        _clear_credit_settings_state(user_data)
        user_chunks = _settings_users_chunks(get_all_user_summaries())
        await query.edit_message_text(
            user_chunks[0],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
            ])
        )

        if chat is None:
            return

        for extra_chunk in user_chunks[1:]:
            await context.bot.send_message(chat_id=chat.id, text=extra_chunk)
        return

    if query_data == "settings_back":
        await query.answer()
        user_data.pop("payment_qr_package", None)
        _clear_credit_settings_state(user_data)
        await query.edit_message_text(
            "⚙️ Settings\n\nChoose an option:",
            reply_markup=_settings_menu_keyboard_for_user(is_admin)
        )
        return

    if query_data == "settings_info":
        await query.answer()
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
        await query.answer()
        await query.edit_message_text(
            "🗑 Delete\n\nChoose what you want to delete:",
            reply_markup=_settings_delete_keyboard()
        )
        return

    if query_data == "settings_reset":
        await query.answer()
        await query.edit_message_text(
            "♻️ Reset\n\nThis will delete all your lyrics, MP3, and MP4 data.",
            reply_markup=_settings_reset_keyboard()
        )
        return

    if query_data == "settings_languages":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        await query.answer()
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

        await query.answer()
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

        await query.answer()
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

        await query.answer()
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

        await query.answer()
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

        await query.answer()
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

        await query.answer()
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
            await query.answer()
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
        await query.answer()
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
        await query.answer()
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
    filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_ID) & filters.Regex(r"^-?\d+$"),
    settings_action
)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.config.settings import ADMIN_ID
from app.database.queries import (
    DEFAULT_SONG_LANGUAGES,
    delete_song_lyrics,
    delete_song_mp3,
    delete_song_mp4,
    delete_user_lyrics,
    delete_user_mp3,
    delete_user_mp4,
    get_enabled_song_languages,
    get_user,
    get_user_songs,
    reset_user_song_data,
    update_enabled_song_languages,
)


def _language_flag(language):
    normalized = (language or "").strip().lower()
    if normalized in {"khmer", "cambodian", "km", "kh"}:
        return "🇰🇭"
    if normalized in {"english", "en"}:
        return "🇺🇸"
    return "🌐"


def _settings_menu_keyboard():
    return _settings_menu_keyboard_for_user(False)


def _settings_menu_keyboard_for_user(is_admin):
    rows = [
        [InlineKeyboardButton("ℹ️ Info", callback_data="settings_info")],
        [InlineKeyboardButton("🗑 Delete", callback_data="settings_delete")],
        [InlineKeyboardButton("♻️ Reset", callback_data="settings_reset")],
    ]

    if is_admin:
        rows.append([InlineKeyboardButton("🌍 Languages", callback_data="settings_languages")])

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


def _deletable_items(telegram_id, item_type):
    songs = get_user_songs(telegram_id)

    if item_type == "lyrics":
        return [song for song in songs if song.lyrics]
    if item_type == "mp3":
        return [song for song in songs if song.mp3_path]
    if item_type == "mp4":
        return [song for song in songs if song.video_path]

    return []


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_admin = update.effective_user.id == ADMIN_ID
    await update.message.reply_text(
        "⚙️ Settings\n\nChoose an option:",
        reply_markup=_settings_menu_keyboard_for_user(is_admin)
    )


async def settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    telegram_id = query.from_user.id
    is_admin = telegram_id == ADMIN_ID

    if query.data == "settings_back":
        await query.edit_message_text(
            "⚙️ Settings\n\nChoose an option:",
            reply_markup=_settings_menu_keyboard_for_user(is_admin)
        )
        return

    if query.data == "settings_info":
        user = get_user(telegram_id)
        await query.edit_message_text(
            _settings_info_text(user, query.from_user),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="settings_back")],
            ])
        )
        return

    if query.data == "settings_delete":
        await query.edit_message_text(
            "🗑 Delete\n\nChoose what you want to delete:",
            reply_markup=_settings_delete_keyboard()
        )
        return

    if query.data == "settings_reset":
        await query.edit_message_text(
            "♻️ Reset\n\nThis will delete all your lyrics, MP3, and MP4 data.",
            reply_markup=_settings_reset_keyboard()
        )
        return

    if query.data == "settings_languages":
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        enabled_languages = get_enabled_song_languages()
        await query.edit_message_text(
            "🌍 Visible Song Languages\n\nChoose which languages users can see in Create Song:",
            reply_markup=_settings_language_keyboard(enabled_languages)
        )
        return

    if query.data.startswith("settings_lang_toggle_"):
        if not is_admin:
            await query.answer("Admin only.", show_alert=True)
            return

        selected_language = query.data.replace("settings_lang_toggle_", "", 1)
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

    if query.data in {"settings_delete_lyrics", "settings_delete_mp3", "settings_delete_mp4"}:
        item_type = query.data.replace("settings_delete_", "")
        items = _deletable_items(telegram_id, item_type)

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

    if query.data.startswith("settings_delete_item_"):
        _, _, _, item_type, song_id = query.data.split("_", 4)
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

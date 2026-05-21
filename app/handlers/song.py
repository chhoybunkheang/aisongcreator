import asyncio
import json
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram import error as tg_error
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config.settings import (
    BOT_USERNAME_LABEL,
    GENERATED_COVERS_DIR,
    GENERATED_VIDEOS_DIR,
)
from app.database.queries import (
    create_user,
    deduct_credit,
    get_enabled_song_languages,
    get_song_by_id,
    get_user,
    get_user_songs,
    save_song,
    update_song_cover,
    update_song_lyrics,
    update_song_mp3,
    update_song_subtitle_timing,
    update_song_video,
)
from app.services.image_service import generate_cover_image
from app.services.music_service import (
    generate_music,
)
from app.services.openai_service import generate_lyrics, generate_subtitle_timing
from app.services.video_service import create_music_video
from app.states.song_states import (
    CHOOSE_COVER,
    CHOOSE_TYPE,
    CONFIRM_COVER,
    CONFIRM_MP3,
    CONFIRM_VIDEO,
    CUSTOM_SONG_TYPE,
    DESCRIPTION,
    EDIT_LYRICS,
    LANGUAGE,
    LYRICS_ACTION,
    MOOD,
    MUSIC_STYLE,
    PASTE_LYRICS,
    SINGER,
    SONG_TYPE,
    TOPIC,
    UPLOAD_COVER,
)
from app.utils.helpers import (
    clear_flow_message_tracking,
    make_progress_notifier,
    replace_flow_message,
    send_audio_with_status,
    send_photo_with_status,
    send_video_with_status,
    start_progress_message,
    start_timed_progress_message,
    stop_progress_message,
)
from app.utils.validators import (
    validate_description,
    validate_lyrics,
    validate_mood,
    validate_style,
    validate_topic,
)

MP3_QUEUE_SECONDS = 50
COVER_QUEUE_SECONDS = 40
VIDEO_QUEUE_SECONDS = 120
VIDEO_WITH_SUBTITLES_QUEUE_SECONDS = 120


def _yes_no_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data="yes"),
            InlineKeyboardButton("❌ No", callback_data="no"),
        ]
    ])


def _animation_style_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌊 Pan Motion", callback_data="anim_pan")],
        [InlineKeyboardButton("💓 Beat Pulse", callback_data="anim_pulse")],
        [InlineKeyboardButton("✨ Pan + Pulse", callback_data="anim_pan_pulse")],
    ])


def _mp3_caption(title):
    return f"🎵 Title: {title}\nCreated by: {BOT_USERNAME_LABEL}"


def _video_caption(title, subtitles_enabled=False):
    suffix = " (with subtitles)" if subtitles_enabled else ""
    return f"🎬 Title: {title}{suffix}\nCreated by: {BOT_USERNAME_LABEL}"


def _mp3_delivery_keyboard():
    rows = [[InlineKeyboardButton("🎵 Full MP3", callback_data="mp3_full")]]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="mp3_cancel")])
    return InlineKeyboardMarkup(rows)


def _unlock_full_song_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Buy Credits", callback_data="buycredits_menu")],
    ])


def _language_keyboard():
    enabled_languages = get_enabled_song_languages()
    language_buttons = []
    language_map = {
        "English": "🇺🇸 English",
        "Khmer": "🇰🇭 Khmer",
        "Vietnamese": "🇻🇳 Vietnam",
        "Chinese": "🇨🇳 Chinese",
        "Japanese": "🇯🇵 Japanese",
    }

    for language in enabled_languages:
        if language in language_map:
            language_buttons.append(
                InlineKeyboardButton(language_map[language], callback_data=f"lang_{language}")
            )

    if not language_buttons:
        language_buttons.append(InlineKeyboardButton("🇺🇸 English", callback_data="lang_English"))

    rows = []
    for index in range(0, len(language_buttons), 2):
        rows.append(language_buttons[index:index + 2])

    return InlineKeyboardMarkup(rows)


def _cover_source_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Upload Image", callback_data="cover_upload"),
            InlineKeyboardButton("🎨 Generate Image", callback_data="cover_generate"),
        ]
    ])


def _singer_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎤 Male", callback_data="singer_male"),
            InlineKeyboardButton("🎙 Female", callback_data="singer_female"),
        ]
    ])


def _description_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="desc_skip")],
    ])


def _song_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Love Song", callback_data="stype_love")],
        [InlineKeyboardButton("🎂 Birthday Song", callback_data="stype_birthday")],
        [InlineKeyboardButton("💍 Wedding Song", callback_data="stype_wedding")],
        [InlineKeyboardButton("🥺 Sorry Song", callback_data="stype_sorry")],
        [InlineKeyboardButton("👨‍👩‍👧 Parents Tribute", callback_data="stype_parents")],
        [InlineKeyboardButton("🤝 Friendship Song", callback_data="stype_friendship")],
        [InlineKeyboardButton("🎧 TikTok Remix", callback_data="stype_tiktok")],
        [InlineKeyboardButton("💔 Sad Breakup", callback_data="stype_breakup")],
        [InlineKeyboardButton("✍️ Custom", callback_data="stype_custom")],
    ])


def _music_style_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 Pop", callback_data="mstyle_Pop"),
            InlineKeyboardButton("💕 Romantic", callback_data="mstyle_Romantic"),
        ],
        [
            InlineKeyboardButton("💔 Sad Ballad", callback_data="mstyle_Sad Ballad"),
            InlineKeyboardButton("🎸 Acoustic", callback_data="mstyle_Acoustic"),
        ],
        [
            InlineKeyboardButton("🎤 Rap", callback_data="mstyle_Rap"),
            InlineKeyboardButton("⚡ EDM", callback_data="mstyle_EDM"),
        ],
        [
            InlineKeyboardButton("📱 TikTok Remix", callback_data="mstyle_TikTok Remix"),
            InlineKeyboardButton("🎧 Lo-fi", callback_data="mstyle_Lo-fi"),
        ],
        [
            InlineKeyboardButton("🤘 Rock", callback_data="mstyle_Rock"),
            InlineKeyboardButton("🇰🇭 Khmer Remix", callback_data="mstyle_Khmer Remix"),
        ],
        [InlineKeyboardButton("✍️ Type My Own", callback_data="mstyle_custom")],
    ])


def _suggested_moods(song_type_code):
    mood_map = {
        "love": [
            ("❤️ Romantic", "Romantic"),
            ("🥺 Emotional", "Emotional"),
            ("🌈 Hopeful", "Hopeful"),
            ("🌙 Chill", "Chill"),
        ],
        "birthday": [
            ("😊 Happy", "Happy"),
            ("⚡ Energetic", "Energetic"),
            ("🎉 Fun", "Fun"),
            ("🌈 Joyful", "Joyful"),
        ],
        "wedding": [
            ("❤️ Romantic", "Romantic"),
            ("🥺 Emotional", "Emotional"),
            ("🌈 Joyful", "Joyful"),
            ("🙏 Loving", "Loving"),
        ],
        "sorry": [
            ("🥺 Emotional", "Emotional"),
            ("😢 Sad", "Sad"),
            ("🌈 Hopeful", "Hopeful"),
            ("🙏 Sincere", "Sincere"),
        ],
        "parents": [
            ("🥺 Emotional", "Emotional"),
            ("🙏 Grateful", "Grateful"),
            ("❤️ Loving", "Loving"),
            ("🌈 Hopeful", "Hopeful"),
        ],
        "friendship": [
            ("😊 Happy", "Happy"),
            ("🌙 Chill", "Chill"),
            ("🎉 Fun", "Fun"),
            ("🌈 Hopeful", "Hopeful"),
        ],
        "tiktok": [
            ("⚡ Energetic", "Energetic"),
            ("🎉 Party", "Party"),
            ("😎 Cool", "Cool"),
            ("🔥 Hype", "Hype"),
        ],
        "breakup": [
            ("💔 Heartbroken", "Heartbroken"),
            ("😢 Sad", "Sad"),
            ("🥺 Emotional", "Emotional"),
            ("🌙 Lonely", "Lonely"),
        ],
        "custom": [
            ("😊 Happy", "Happy"),
            ("❤️ Romantic", "Romantic"),
            ("😢 Sad", "Sad"),
            ("🥺 Emotional", "Emotional"),
            ("⚡ Energetic", "Energetic"),
            ("🌙 Chill", "Chill"),
            ("💔 Heartbroken", "Heartbroken"),
            ("🌈 Hopeful", "Hopeful"),
        ],
    }
    return mood_map.get(song_type_code or "custom", mood_map["custom"])


def _mood_keyboard(song_type_code):
    suggested_moods = _suggested_moods(song_type_code)
    rows = []

    for index in range(0, len(suggested_moods), 2):
        pair = suggested_moods[index:index + 2]
        rows.append([
            InlineKeyboardButton(label, callback_data=f"mood_{value}")
            for label, value in pair
        ])

    rows.append([InlineKeyboardButton("✍️ Type My Own", callback_data="mood_custom")])
    return InlineKeyboardMarkup(rows)


def _mood_examples_text(song_type_code):
    examples = [value for _, value in _suggested_moods(song_type_code)[:4]]
    return "\n".join(f"- {example}" for example in examples)


def _song_type_label(song_type_code):
    labels = {
        "love": "Love Song",
        "birthday": "Birthday Song",
        "wedding": "Wedding Song",
        "sorry": "Sorry Song",
        "parents": "Parents Tribute",
        "friendship": "Friendship Song",
        "tiktok": "TikTok Remix",
        "breakup": "Sad Breakup",
        "custom": "Custom",
    }
    return labels.get(song_type_code, song_type_code or "Custom")


def _lyrics_action_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Edit Lyrics", callback_data="lyrics_edit")],
        [InlineKeyboardButton("🎼 Continue", callback_data="lyrics_continue")],
        [InlineKeyboardButton("🔄 Regenerate", callback_data="lyrics_regenerate")],
    ])


def _lyrics_preview_message(lyrics, generated=False):
    prefix = "🎵 Your AI Lyrics" if generated else "📝 Your Lyrics"
    lyrics_msg = f"{prefix}\n\n{lyrics}"
    if len(lyrics_msg) > 4096:
        lyrics_msg = lyrics_msg[:4090] + "..."
    return lyrics_msg


async def _show_lyrics_actions(context, chat_id):
    await replace_flow_message(
        context,
        context.bot.send_message,
        chat_id=chat_id,
        text="What do you want to do with these lyrics?",
        reply_markup=_lyrics_action_keyboard(),
        state_key="song_flow_message_id",
    )
    return LYRICS_ACTION


async def _send_lyrics_preview_and_actions(context, telegram_id, chat_id, lyrics, generated=False):
    context.user_data["lyrics"] = lyrics

    await context.bot.send_message(
        chat_id=chat_id,
        text=_lyrics_preview_message(lyrics, generated=generated),
    )
    return await _show_lyrics_actions(context, chat_id)


def _persist_current_lyrics(context, telegram_id):
    song_id = _save_lyrics_draft(context, telegram_id)
    if song_id is not None:
        update_song_lyrics(song_id, context.user_data["lyrics"])

    return song_id


async def _regenerate_lyrics_for_current_context(query, context):
    style = context.user_data["style"]
    topic = context.user_data["topic"]
    mood = context.user_data["mood"]
    language = context.user_data["language"]
    description = context.user_data.get("description", "")

    await query.edit_message_text(
        f"🌍 Language selected: {language}\n🎤 Singer: {context.user_data.get('singer_gender', 'female').title()}"
    )
    progress_message = await replace_flow_message(
        context,
        context.bot.send_message,
        chat_id=query.message.chat_id,
        text="⏳ Generating lyrics...\nPreparing request...",
        state_key="song_flow_message_id",
    )
    progress_task, progress_stop = await start_progress_message(
        progress_message,
        "⏳ Generating lyrics...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), progress_message)

    try:
        lyrics = await asyncio.to_thread(
            generate_lyrics,
            style=style,
            topic=topic,
            mood=mood,
            language=language,
            description=description,
            progress_callback=progress_callback,
        )
        await stop_progress_message(
            progress_task,
            progress_stop,
            progress_message,
            "✅ Lyrics generated 100%"
        )
        return await _send_lyrics_preview_and_actions(
            context,
            query.from_user.id,
            query.message.chat_id,
            lyrics,
            generated=True,
        )
    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            progress_message,
            "❌ Lyrics generation failed"
        )
        error_msg = f"❌ Error generating lyrics:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)
        return ConversationHandler.END

def _friendly_mp3_error_message(error):
    error_text = str(error or "").strip()
    lowered = error_text.lower()

    if any(token in lowered for token in ("502", "503", "504", "bad gateway", "music api server error", "polling error")):
        return (
            "❌ Could not generate the MP3 right now.\n\n"
            "The music server is temporarily busy or unavailable. Please try again in a few minutes."
        )

    if "timed out" in lowered or "timeout" in lowered:
        return (
            "❌ MP3 generation timed out.\n\n"
            "The music server took too long to respond. Please try again shortly."
        )

    return f"❌ Error generating MP3:\n{error_text}"


async def _safe_answer(query):
    """Answer a callback query, ignoring expired/invalid query errors."""
    try:
        await query.answer()
    except tg_error.BadRequest:
        pass  # query expired (>30s old) — safe to ignore


def _entry_type_keyboard():
    rows = [
        [InlineKeyboardButton("🎵 New", callback_data="type_new")],
        [InlineKeyboardButton("📝 Library", callback_data="type_mylyrics")],
        [InlineKeyboardButton("📋 Paste Lyric", callback_data="type_paste")],
    ]
    return InlineKeyboardMarkup(rows)


def _save_lyrics_draft(context, telegram_id):
    if context.user_data.get("song_id"):
        return context.user_data["song_id"]

    song = save_song(
        telegram_id=telegram_id,
        style=context.user_data["style"],
        topic=context.user_data["topic"],
        mood=context.user_data["mood"],
        language=context.user_data["language"],
        lyrics=context.user_data["lyrics"],
    )

    if song:
        context.user_data["song_id"] = song.id
        return song.id

    return None


# -----------------------------
# START SONG FLOW
# -----------------------------
async def create_song(update: Update, context: ContextTypes.DEFAULT_TYPE):

    effective_user = update.effective_user
    message = update.message
    if effective_user is None or message is None:
        return ConversationHandler.END

    telegram_id = effective_user.id
    user = get_user(telegram_id)
    if not user:
        create_user(
            telegram_id=telegram_id,
            name=effective_user.first_name,
        )
        user = get_user(telegram_id)

    if not user:
        await message.reply_text(
            "❌ Unable to start song creation right now. Please send /start and try again."
        )
        return ConversationHandler.END

    if user.credits <= 0:
        from app.handlers.buycredits import _buy_credits_menu_markup
        await message.reply_text(
            "❌ You don't have enough credits.\n\n"
            "Please add credits to continue.\n\n"
            "Choose a package below:",
            reply_markup=_buy_credits_menu_markup(telegram_id)
        )
        return ConversationHandler.END

    keyboard = _entry_type_keyboard()
    await replace_flow_message(
        context,
        message.reply_text,
        f"💎 Full song credits: {user.credits}\n\nChoose how you want to create your song:",
        reply_markup=keyboard,
        state_key="song_flow_message_id",
    )
    return CHOOSE_TYPE


# -----------------------------
# CHOOSE TYPE
# -----------------------------
async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    if query.data == "type_new":
        context.user_data.clear()
        context.user_data["description"] = ""
        context.user_data["song_type"] = "custom"
        context.chat_data["song_flow_message_id"] = query.message.message_id
        await query.edit_message_text(
            "🎵 Choose Song Type",
            reply_markup=_song_type_keyboard(),
        )
        return SONG_TYPE

    if query.data == "type_paste":
        context.user_data.clear()
        context.user_data["description"] = ""
        context.chat_data["song_flow_message_id"] = query.message.message_id
        await query.edit_message_text("📋 Please paste your lyrics:")
        return PASTE_LYRICS

    # type_mylyrics — show saved lyrics list that has not been converted yet
    songs = get_user_songs(query.from_user.id)
    songs_with_lyrics = [s for s in songs if s.lyrics and not s.mp3_path]
    if not songs_with_lyrics:
        await query.edit_message_text(
            "You don't have any saved lyrics waiting for MP3 conversion yet. "
            "Use New or Paste Lyric first."
        )
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(s.topic, callback_data=f"lyr_pick_{s.id}")] for s in songs_with_lyrics]
    await query.edit_message_text(
        "📜 Select lyrics to convert to MP3:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.chat_data["song_flow_message_id"] = query.message.message_id
    return CHOOSE_TYPE


# -----------------------------
# PICK SAVED LYRICS
# -----------------------------
async def pick_saved_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[2])
    song = get_song_by_id(song_id)

    if not song:
        await query.edit_message_text("Song not found.")
        return ConversationHandler.END

    context.user_data["song_id"] = song.id
    context.user_data["style"] = song.style
    context.user_data["topic"] = song.topic
    context.user_data["mood"] = song.mood
    context.user_data["language"] = song.language
    context.user_data["lyrics"] = song.lyrics
    context.user_data["description"] = ""

    await query.edit_message_text(f"📜 Topic: {song.topic}")
    context.chat_data["song_flow_message_id"] = query.message.message_id
    return await _send_lyrics_preview_and_actions(
        context,
        query.from_user.id,
        query.message.chat_id,
        song.lyrics,
        generated=False,
    )


# -----------------------------
# GET PASTED LYRICS
# -----------------------------
async def get_pasted_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    lyrics, error_message = validate_lyrics(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return PASTE_LYRICS

    context.user_data["lyrics"] = lyrics
    context.user_data["description"] = ""
    await replace_flow_message(
        context,
        update.message.reply_text,
        "🎼 Choose a music style or type your own.\n\n"
        "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song",
        reply_markup=_music_style_keyboard(),
        state_key="song_flow_message_id",
    )
    return MUSIC_STYLE


# -----------------------------
# CHOOSE SONG TYPE
# -----------------------------
async def choose_song_type(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    song_type = (query.data or "").replace("stype_", "", 1)
    context.user_data["song_type"] = song_type or "custom"

    if context.user_data["song_type"] == "custom":
        await query.edit_message_text(
            "✍️ Custom Song\n\n"
            "Type your song type or theme.\n\n"
            "Examples:\n- Graduation Song\n- Motivation Song\n- Welcome Home Song\n- Prayer Song",
        )
        return CUSTOM_SONG_TYPE

    song_type_label = _song_type_label(context.user_data["song_type"])
    await query.edit_message_text(
        f"🎵 Song Type: {song_type_label}\n\n"
        "🎼 Choose a music style or type your own.\n\n"
        "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song",
        reply_markup=_music_style_keyboard(),
    )
    return MUSIC_STYLE


async def get_custom_song_type(update: Update, context: ContextTypes.DEFAULT_TYPE):

    song_type, error_message = validate_topic(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return CUSTOM_SONG_TYPE

    context.user_data["song_type"] = song_type
    await replace_flow_message(
        context,
        update.message.reply_text,
        "🎼 Choose a music style or type your own.\n\n"
        "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song",
        reply_markup=_music_style_keyboard(),
        state_key="song_flow_message_id",
    )
    return MUSIC_STYLE


# -----------------------------
# GET MUSIC STYLE
# -----------------------------
async def get_music_style(update: Update, context: ContextTypes.DEFAULT_TYPE):

    style, error_message = validate_style(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return MUSIC_STYLE

    context.user_data["style"] = style
    await replace_flow_message(
        context,
        update.message.reply_text,
        "📝 What is the song topic?",
        state_key="song_flow_message_id",
    )
    return TOPIC


async def choose_music_style(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    callback_value = (query.data or "").replace("mstyle_", "", 1)
    if callback_value == "custom":
        await query.edit_message_text(
            "✍️ Type your music style.\n\n"
            "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song"
        )
        return MUSIC_STYLE

    context.user_data["style"] = callback_value
    await query.edit_message_text("📝 What is the song topic?")
    return TOPIC


# -----------------------------
# GET TOPIC
# -----------------------------
async def get_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):

    topic, error_message = validate_topic(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return TOPIC

    context.user_data["topic"] = topic
    song_type_code = context.user_data.get("song_type", "custom")
    song_type_label = _song_type_label(song_type_code)
    await replace_flow_message(
        context,
        update.message.reply_text,
        f"😊 Choose a mood for the {song_type_label.lower()} or type your own.\n\n"
        f"Examples:\n{_mood_examples_text(song_type_code)}",
        reply_markup=_mood_keyboard(song_type_code),
        state_key="song_flow_message_id",
    )
    return MOOD


# -----------------------------
# GET MOOD
# -----------------------------
async def get_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):

    mood, error_message = validate_mood(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return MOOD

    context.user_data["mood"] = mood
    await replace_flow_message(
        context,
        update.message.reply_text,
        "✍️ Tell me more about the feeling or story you want in the lyrics.\n\n"
        "Example:\n"
        "- a breakup at midnight\n"
        "- soft romantic words\n"
        "- from a girl to a boy\n"
        "- mention rain, memories, and pain\n\n"
        "You can type extra details or tap Skip.",
        reply_markup=_description_keyboard(),
        state_key="song_flow_message_id",
    )
    return DESCRIPTION


async def choose_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    callback_value = (query.data or "").replace("mood_", "", 1)
    if callback_value == "custom":
        await query.edit_message_text(
            "✍️ Type your mood.\n\n"
            "Examples:\n- Happy\n- Emotional\n- Sad\n- Energetic"
        )
        return MOOD

    context.user_data["mood"] = callback_value
    await query.edit_message_text(
        "✍️ Tell me more about the feeling or story you want in the lyrics.\n\n"
        "Example:\n"
        "- a breakup at midnight\n"
        "- soft romantic words\n"
        "- from a girl to a boy\n"
        "- mention rain, memories, and pain\n\n"
        "You can type extra details or tap Skip.",
        reply_markup=_description_keyboard(),
    )
    return DESCRIPTION


# -----------------------------
# GET DESCRIPTION
# -----------------------------
async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):

    description_text = (update.message.text or "").strip()
    if description_text.lower() == "skip":
        context.user_data["description"] = ""
    else:
        description, error_message = validate_description(description_text)
        if error_message:
            await update.message.reply_text(error_message)
            return DESCRIPTION
        context.user_data["description"] = description
    await replace_flow_message(
        context,
        update.message.reply_text,
        "🌍 Choose a language:",
        reply_markup=_language_keyboard(),
        state_key="song_flow_message_id",
    )
    return LANGUAGE


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    if query is None:
        return DESCRIPTION

    context.user_data["description"] = ""
    await query.edit_message_text(
        "🌍 Choose a language:",
        reply_markup=_language_keyboard(),
    )
    return LANGUAGE


# -----------------------------
# GET LANGUAGE - Generate Lyrics or Ask for Lyrics
# -----------------------------
async def get_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    selected_language = query.data.replace("lang_", "", 1)
    enabled_languages = get_enabled_song_languages()

    if selected_language not in enabled_languages:
        await query.edit_message_text(
            "❌ This language is not available right now. Please choose another language.",
            reply_markup=_language_keyboard()
        )
        return LANGUAGE

    context.user_data["language"] = selected_language
    language = context.user_data["language"]

    await query.edit_message_text(
        f"🌍 Language selected: {language}\n\nChoose a singer voice:",
        reply_markup=_singer_keyboard()
    )
    return SINGER


async def get_singer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    singer_gender = query.data.replace("singer_", "", 1)
    context.user_data["singer_gender"] = singer_gender
    language = context.user_data["language"]

    if context.user_data.get("lyrics"):
        # Paste Lyrics path — lyrics already collected before style/topic/mood/language
        lyrics = context.user_data["lyrics"]
        await query.edit_message_text(
            f"🌍 Language selected: {language}\n🎤 Singer: {singer_gender.title()}"
        )
        context.chat_data["song_flow_message_id"] = query.message.message_id
        return await _send_lyrics_preview_and_actions(
            context,
            query.from_user.id,
            query.message.chat_id,
            lyrics,
            generated=False,
        )

    return await _regenerate_lyrics_for_current_context(query, context)


async def lyrics_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    if query.data == "lyrics_continue":
        _persist_current_lyrics(context, query.from_user.id)
        await query.edit_message_text(
            "🎧 Do you want to convert this to MP3?",
            reply_markup=_mp3_delivery_keyboard(),
        )
        return CONFIRM_MP3

    if query.data == "lyrics_edit":
        await query.edit_message_text(
            "✍️ Send your updated lyrics text."
        )
        return EDIT_LYRICS

    return await _regenerate_lyrics_for_current_context(query, context)


async def edit_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lyrics, error_message = validate_lyrics(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return EDIT_LYRICS

    context.user_data["lyrics"] = lyrics

    return await _send_lyrics_preview_and_actions(
        context,
        update.effective_user.id,
        update.effective_chat.id,
        lyrics,
        generated=False,
    )


# -----------------------------
# CONFIRM MP3
# -----------------------------
async def confirm_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    action = query.data or ""

    if action in {"no", "mp3_cancel"}:
        await query.edit_message_text("👍 Okay! Song creation stopped.")
        return ConversationHandler.END

    if action not in {"yes", "mp3_full", ""}:
        await query.edit_message_text("❌ Invalid option.")
        return ConversationHandler.END

    user = get_user(query.from_user.id)
    if not user or user.credits <= 0:
        await query.edit_message_text(
            "❌ You do not have enough credits for the full MP3.\n\n"
            "💎 Buy credits to unlock the full song.",
            reply_markup=_unlock_full_song_keyboard(),
        )
        return CONFIRM_MP3

    style = context.user_data["style"]
    topic = context.user_data["topic"]
    mood = context.user_data["mood"]
    lyrics = context.user_data["lyrics"]

    initial_progress_text = "⏳ Generating MP3...\nPreparing request..."
    await query.edit_message_text(initial_progress_text)
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        initial_progress_text,
        start_percent=1,
        max_percent=95,
        total_seconds=MP3_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        mp3_file = await asyncio.to_thread(
            generate_music,
            style=style, topic=topic, mood=mood, lyrics=lyrics,
            language=context.user_data.get("language", ""),
            singer_gender=context.user_data.get("singer_gender", "female"),
            progress_callback=progress_callback,
        )

        context.user_data["mp3_file"] = mp3_file

        subtitle_timing = []
        try:
            subtitle_timing = await asyncio.to_thread(
                generate_subtitle_timing,
                mp3_file,
                lyrics,
                context.user_data.get("language", "")
            )
        except Exception:
            subtitle_timing = []
        context.user_data["subtitle_timing"] = subtitle_timing

        deduct_credit(query.from_user.id)
        song_id = _save_lyrics_draft(context, query.from_user.id)
        if song_id:
            update_song_mp3(song_id, mp3_file)
            update_song_subtitle_timing(song_id, json.dumps(subtitle_timing, ensure_ascii=False))

        with open(mp3_file, "rb") as audio:
            await send_audio_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                audio=audio,
                title=topic,
                caption=_mp3_caption(topic),
                status_message=query.message,
                upload_text="⏫ MP3 ready. Uploading to Telegram...",
                complete_text="✅ MP3 uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "✅ MP3 uploaded 100%"
        )

        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=query.message.chat_id,
            text="🎨 Do you want to generate a cover image?",
            reply_markup=_yes_no_keyboard(),
            state_key="song_flow_message_id",
        )
        return CONFIRM_COVER

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "❌ MP3 generation failed"
        )
        error_msg = _friendly_mp3_error_message(e)
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)
        return ConversationHandler.END


# -----------------------------
# CONFIRM COVER
# -----------------------------
async def confirm_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    if query.data == "no":
        user = get_user(query.from_user.id)
        await query.edit_message_text(
            f"✅ All done!\n\n"
            f"💎 Remaining Credits: {user.credits}"
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "🎨 Choose cover image type:",
        reply_markup=_cover_source_keyboard()
    )
    return CHOOSE_COVER


# -----------------------------
# CHOOSE COVER SOURCE
# -----------------------------
async def choose_cover_source(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    if query.data == "cover_upload":
        await query.edit_message_text(
            "🖼 Please upload one image to use as your cover."
        )
        return UPLOAD_COVER

    topic = context.user_data["topic"]
    mood = context.user_data["mood"]
    style = context.user_data["style"]

    await query.edit_message_text("⏳ Generating cover image...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "⏳ Generating cover image...\nPreparing request...",
        start_percent=1,
        max_percent=95,
        total_seconds=COVER_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        cover_image = await asyncio.to_thread(
            generate_cover_image,
            topic=topic, mood=mood, style=style,
            progress_callback=progress_callback,
        )
        context.user_data["cover_image"] = cover_image
        if context.user_data.get("song_id"):
            update_song_cover(context.user_data["song_id"], cover_image)

        with open(cover_image, "rb") as photo:
            await send_photo_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                photo=photo,
                caption="🎨 AI Generated Cover",
                status_message=query.message,
                upload_text="⏫ Cover ready. Uploading to Telegram...",
                complete_text="✅ Cover uploaded",
            )

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "✅ Cover uploaded 100%"
        )

        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=query.message.chat_id,
            text="🎬 Do you want to create a music video?",
            reply_markup=_yes_no_keyboard(),
            state_key="song_flow_message_id",
        )
        return CONFIRM_VIDEO

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "❌ Cover generation failed"
        )
        error_msg = f"❌ Error generating cover:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)
        return ConversationHandler.END


# -----------------------------
# RECEIVE UPLOADED COVER
# -----------------------------
async def receive_uploaded_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return UPLOAD_COVER

    photo = update.message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)

    os.makedirs(GENERATED_COVERS_DIR, exist_ok=True)
    cover_path = os.path.join(
        GENERATED_COVERS_DIR,
        f"upload_{update.effective_user.id}_{uuid.uuid4().hex}.jpg"
    )
    await telegram_file.download_to_drive(cover_path)

    context.user_data["cover_image"] = cover_path
    if context.user_data.get("song_id"):
        update_song_cover(context.user_data["song_id"], cover_path)

    with open(cover_path, "rb") as photo_file:
        await send_photo_with_status(
            context.bot,
            chat_id=update.effective_chat.id,
            photo=photo_file,
            caption="🖼 Uploaded Cover Image",
        )

    await replace_flow_message(
        context,
        context.bot.send_message,
        chat_id=update.effective_chat.id,
        text="🎬 Do you want to create a music video?",
        reply_markup=_yes_no_keyboard(),
        state_key="song_flow_message_id",
    )
    return CONFIRM_VIDEO


# -----------------------------
# CONFIRM VIDEO
# -----------------------------
async def confirm_video(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    user = get_user(query.from_user.id)

    if not context.user_data.get("video_animation_prompt_pending") and not context.user_data.get("video_animation_style_prompt_pending") and not context.user_data.get("video_subtitle_prompt_pending"):
        if query.data == "no":
            await query.edit_message_text(
                f"✅ All done!\n\n"
                f"💎 Remaining Credits: {user.credits}"
            )
            return ConversationHandler.END

        context.user_data["video_animation_prompt_pending"] = True
        await query.edit_message_text(
            "✨ Do you want to add animation to the video?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_VIDEO

    if context.user_data.get("video_animation_prompt_pending"):
        context.user_data.pop("video_animation_prompt_pending", None)

        if query.data == "yes":
            context.user_data["video_animation_style_prompt_pending"] = True
            await query.edit_message_text(
                "🎞 Choose the animation style for your video:",
                reply_markup=_animation_style_keyboard()
            )
            return CONFIRM_VIDEO

        context.user_data["video_animation_style"] = "none"
        context.user_data["video_subtitle_prompt_pending"] = True
        await query.edit_message_text(
            "📝 Do you want to add subtitles to the video?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_VIDEO

    if context.user_data.get("video_animation_style_prompt_pending"):
        animation_map = {
            "anim_pan": "pan",
            "anim_pulse": "pulse",
            "anim_pan_pulse": "pan_pulse",
        }
        animation_style = animation_map.get(query.data)
        if not animation_style:
            await query.edit_message_text(
                "🎞 Choose the animation style for your video:",
                reply_markup=_animation_style_keyboard()
            )
            return CONFIRM_VIDEO

        context.user_data.pop("video_animation_style_prompt_pending", None)
        context.user_data["video_animation_style"] = animation_style
        context.user_data["video_subtitle_prompt_pending"] = True
        await query.edit_message_text(
            "📝 Do you want to add subtitles to the video?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_VIDEO

    subtitles_enabled = query.data == "yes"
    if subtitles_enabled and (not user or user.credits <= 10):
        await query.edit_message_text(
            "❌ You need more than 10 credits to create a video with subtitles.\n\n"
            "💎 Please add credit or choose video without subtitles.",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_VIDEO

    context.user_data.pop("video_subtitle_prompt_pending", None)

    mp3_file = context.user_data["mp3_file"]
    cover_image = context.user_data["cover_image"]
    topic = context.user_data["topic"]
    subtitle_timing = None

    await query.edit_message_text(
        "⏳ Generating subtitles...\nPreparing request..."
        if subtitles_enabled and context.user_data.get("lyrics")
        else "⏳ Creating music video...\nPreparing render..."
    )
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "⏳ Generating subtitles...\nPreparing request..."
        if subtitles_enabled and context.user_data.get("lyrics")
        else "⏳ Creating music video...\nPreparing render...",
        start_percent=1,
        max_percent=95,
        total_seconds=VIDEO_WITH_SUBTITLES_QUEUE_SECONDS if subtitles_enabled else VIDEO_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    if subtitles_enabled and context.user_data.get("lyrics"):
        try:
            subtitle_timing = await asyncio.to_thread(
                generate_subtitle_timing,
                mp3_file,
                context.user_data["lyrics"],
                context.user_data.get("language", ""),
                progress_callback=progress_callback,
            )
        except Exception:
            subtitle_timing = []
        context.user_data["subtitle_timing"] = subtitle_timing
        if subtitle_timing and context.user_data.get("song_id"):
            update_song_subtitle_timing(
                context.user_data["song_id"],
                json.dumps(subtitle_timing, ensure_ascii=False)
            )

    try:
        safe_topic = "_".join(topic.split())
        os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)
        video_path = os.path.join(GENERATED_VIDEOS_DIR, f"{query.from_user.id}_{safe_topic}.mp4")

        await asyncio.to_thread(
            create_music_video,
            audio_path=mp3_file,
            image_path=cover_image,
            output_path=video_path,
            animation_style=context.user_data.get("video_animation_style", "none"),
            lyrics=context.user_data.get("lyrics"),
            subtitle_timing=subtitle_timing,
            subtitles_enabled=subtitles_enabled,
            progress_callback=progress_callback,
        )
        if context.user_data.get("song_id"):
            update_song_video(context.user_data["song_id"], video_path)

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption=_video_caption(topic, subtitles_enabled=subtitles_enabled),
                status_message=query.message,
                upload_text="⏫ Video ready. Uploading to Telegram...",
                complete_text="✅ Video uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "✅ Video uploaded 100%"
        )

        if subtitles_enabled:
            deduct_credit(query.from_user.id)
            user = get_user(query.from_user.id)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ All done!\n\n💎 Remaining Credits: {user.credits}"
        )
        clear_flow_message_tracking(context, state_key="song_flow_message_id")

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "❌ Video creation failed"
        )
        error_msg = f"❌ Error creating video:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)

    return ConversationHandler.END


# -----------------------------
# CANCEL
# -----------------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    clear_flow_message_tracking(context, state_key="song_flow_message_id")
    await update.message.reply_text("❌ Song creation cancelled.")
    return ConversationHandler.END


# -----------------------------
# CONVERSATION HANDLER
# -----------------------------
song_handler = ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.TEXT & filters.Regex(r"^🎵 Create Song$"),
            create_song
        )
    ],
    states={
        CHOOSE_TYPE: [
            CallbackQueryHandler(choose_type, pattern=r"^type_"),
            CallbackQueryHandler(pick_saved_lyrics, pattern=r"^lyr_pick_\d+$"),
        ],
        SONG_TYPE: [
            CallbackQueryHandler(choose_song_type, pattern=r"^stype_")
        ],
        CUSTOM_SONG_TYPE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_song_type)
        ],
        PASTE_LYRICS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_pasted_lyrics)
        ],
        MUSIC_STYLE: [
            CallbackQueryHandler(choose_music_style, pattern=r"^mstyle_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_music_style)
        ],
        TOPIC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_topic)
        ],
        MOOD: [
            CallbackQueryHandler(choose_mood, pattern=r"^mood_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_mood)
        ],
        DESCRIPTION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_description),
            CallbackQueryHandler(skip_description, pattern=r"^desc_skip$")
        ],
        LANGUAGE: [
            CallbackQueryHandler(get_language, pattern=r"^lang_")
        ],
        SINGER: [
            CallbackQueryHandler(get_singer, pattern=r"^singer_")
        ],
        LYRICS_ACTION: [
            CallbackQueryHandler(lyrics_action, pattern=r"^lyrics_")
        ],
        EDIT_LYRICS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_lyrics)
        ],
        CONFIRM_MP3: [
            CallbackQueryHandler(confirm_mp3)
        ],
        CONFIRM_COVER: [
            CallbackQueryHandler(confirm_cover)
        ],
        CHOOSE_COVER: [
            CallbackQueryHandler(choose_cover_source, pattern=r"^cover_")
        ],
        UPLOAD_COVER: [
            MessageHandler(filters.PHOTO, receive_uploaded_cover)
        ],
        CONFIRM_VIDEO: [
            CallbackQueryHandler(confirm_video)
        ],
    },
    fallbacks=[]
)

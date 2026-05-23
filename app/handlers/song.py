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


# Cancel handler for any flow
async def cancel_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Song creation cancelled. You can start again anytime.")
    elif update.message:
        await update.message.reply_text("❌ Song creation cancelled. You can start again anytime.")
    # Optionally clear user_data or any state here
    return ConversationHandler.END

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
    refund_credit,
    save_song,
    update_song_cover,
    update_song_lyrics,
    update_song_mp3,
    update_song_source_video,
    update_song_subtitle_timing,
    update_song_video,
)
from app.handlers.buycredits import payment_info
from app.services.image_service import generate_cover_image
from app.services.music_service import (
    generate_music,
)
from app.services.openai_service import generate_lyrics, generate_subtitle_timing
from app.services.video_service import create_music_video
from app.states.song_states import (
    BUY_CREDITS,
    CHOOSE_COVER,
    CHOOSE_TYPE,
    CONFIRM_MP3,
    CONFIRM_VIDEO,
    CONFIRM_VIDEO_START,
    DESCRIPTION,
    EDIT_LYRICS,
    LANGUAGE,
    LYRICS_ACTION,
    MOOD,
    MUSIC_STYLE,
    PASTE_LYRICS,
    SINGER,
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


def _mp3_caption(title):
    return f"🎵 Title: {title}\nCreated by: {BOT_USERNAME_LABEL}"


def _video_caption(title, subtitles_enabled=False):
    suffix = " (with subtitles)" if subtitles_enabled else ""
    return (
        f"🎬 Title: {title}{suffix}\n"
        f"Created by: {BOT_USERNAME_LABEL}"
    )


def _mp3_delivery_keyboard():
    rows = [[InlineKeyboardButton("🎵 Full MP3", callback_data="mp3_full")]]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="mp3_cancel")])
    return InlineKeyboardMarkup(rows)


def _unlock_full_song_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Add Credits", callback_data="buycredits_menu")],
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
        [InlineKeyboardButton("🖼 Upload Image", callback_data="cover_upload")],
        [InlineKeyboardButton("🎞 Upload Video", callback_data="cover_upload_video")],
        [InlineKeyboardButton("🎨 Use Generated Image", callback_data="cover_use_generated")],
    ])


async def _resolve_generated_cover_image(context):
    generated_cover_image = context.user_data.get("generated_cover_image")
    if generated_cover_image and os.path.exists(generated_cover_image):
        return generated_cover_image

    generated_cover_task = context.user_data.get("generated_cover_task")
    if not generated_cover_task:
        return None

    generated_cover_image = await generated_cover_task
    context.user_data["generated_cover_image"] = generated_cover_image
    context.user_data.pop("generated_cover_task", None)
    return generated_cover_image


def _singer_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎤 Male", callback_data="singer_male"),
            InlineKeyboardButton("🎙 Female", callback_data="singer_female"),
        ]
    ])


def _detect_language_from_lyrics(lyrics):
    text = (lyrics or "").strip()
    enabled_languages = set(get_enabled_song_languages())

    if any("\u1780" <= char <= "\u17ff" for char in text) and "Khmer" in enabled_languages:
        return "Khmer"

    if any("\u3040" <= char <= "\u30ff" for char in text) and "Japanese" in enabled_languages:
        return "Japanese"

    if any("\u4e00" <= char <= "\u9fff" for char in text) and "Chinese" in enabled_languages:
        return "Chinese"

    vietnamese_markers = set("ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    if any(char.lower() in vietnamese_markers for char in text) and "Vietnamese" in enabled_languages:
        return "Vietnamese"

    if "English" in enabled_languages:
        return "English"

    return next(iter(enabled_languages), "English")


def _description_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="desc_skip")],
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
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_flow")],
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
        description=context.user_data.get("description", ""),
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
    query = update.callback_query
    chat = update.effective_chat
    if effective_user is None or chat is None:
        return ConversationHandler.END

    if query:
        await _safe_answer(query)

    telegram_id = effective_user.id
    user = get_user(telegram_id)
    if not user:
        user = create_user(
            telegram_id=telegram_id,
            name=effective_user.first_name,
        )

    if not user:
        await context.bot.send_message(
            chat_id=chat.id,
            text="❌ Unable to start song creation right now. Please send /start and try again."
        )
        return ConversationHandler.END

    user_credits = int(getattr(user, "credits", 0) or 0)

    if user_credits <= 0:
        from app.handlers.buycredits import _buy_credits_menu_markup
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "❌ You don't have enough credits.\n\n"
                "Please add credits to continue.\n\n"
                "Choose a package below:"
            ),
            reply_markup=_buy_credits_menu_markup(telegram_id),
        )
        return ConversationHandler.END

    keyboard = _entry_type_keyboard()
    prompt_text = f"💎 Full song credits: {user_credits}\n\nChoose how you want to create your song:"
    if query:
        await replace_flow_message(
            context,
            query.edit_message_text,
            prompt_text,
            reply_markup=keyboard,
            state_key="song_flow_message_id",
        )
    else:
        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=chat.id,
            text=prompt_text,
            reply_markup=keyboard,
            state_key="song_flow_message_id",
        )
    return CHOOSE_TYPE


# -----------------------------
# CHOOSE TYPE
# -----------------------------
async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    if query is None or query.message is None or query.from_user is None:
        return ConversationHandler.END

    user_data = context.user_data
    chat_data = context.chat_data
    if user_data is None or chat_data is None:
        return ConversationHandler.END

    await _safe_answer(query)

    if query.data == "type_new":
        user_data.clear()
        user_data["description"] = ""
        chat_data["song_flow_message_id"] = query.message.message_id
        await query.edit_message_text(
            "📝 What is the song title?",
        )
        return TOPIC

    if query.data == "type_paste":
        user_data.clear()
        user_data["pasted_lyrics_mode"] = True
        user_data["description"] = ""
        chat_data["song_flow_message_id"] = query.message.message_id
        await query.edit_message_text("📋 Please paste your lyrics:")
        return PASTE_LYRICS

    # type_mylyrics — show saved lyrics list that has not been converted yet
    songs = get_user_songs(query.from_user.id)
    songs_with_lyrics = [
        song for song in songs
        if getattr(song, "lyrics", None) and not getattr(song, "mp3_path", None)
    ]
    if not songs_with_lyrics:
        await query.edit_message_text(
            "You don't have any saved lyrics waiting for MP3 conversion yet. "
            "Use New or Paste Lyric first."
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(str(song.topic), callback_data=f"lyr_pick_{song.id}")]
        for song in songs_with_lyrics
    ]
    await query.edit_message_text(
        "📜 Select lyrics to convert to MP3:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    chat_data["song_flow_message_id"] = query.message.message_id
    return CHOOSE_TYPE


# -----------------------------
# PICK SAVED LYRICS
# -----------------------------
async def pick_saved_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.message is None or query.from_user is None or chat is None:
        return ConversationHandler.END

    user_data = context.user_data
    chat_data = context.chat_data
    if user_data is None or chat_data is None:
        return ConversationHandler.END

    await _safe_answer(query)

    callback_data = query.data or ""
    song_id = int(callback_data.split("_")[2])
    song = get_song_by_id(song_id)

    if not song:
        await query.edit_message_text("Song not found.")
        return ConversationHandler.END

    user_data["song_id"] = song.id
    user_data["style"] = song.style
    user_data["topic"] = song.topic
    user_data["mood"] = song.mood
    user_data["language"] = song.language
    user_data["lyrics"] = song.lyrics
    user_data["description"] = ""

    await query.edit_message_text(f"📜 Topic: {song.topic}")
    chat_data["song_flow_message_id"] = query.message.message_id
    return await _send_lyrics_preview_and_actions(
        context,
        query.from_user.id,
        chat.id,
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
    context.user_data["pasted_lyrics_mode"] = True
    context.user_data["description"] = ""
    context.user_data["language"] = _detect_language_from_lyrics(lyrics)
    await replace_flow_message(
        context,
        update.message.reply_text,
        "📝 What is the song title?",
        state_key="song_flow_message_id",
    )
    return TOPIC


# -----------------------------
# GET MUSIC STYLE
# -----------------------------
async def get_music_style(update: Update, context: ContextTypes.DEFAULT_TYPE):

    style, error_message = validate_style(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return MUSIC_STYLE

    context.user_data["style"] = style

    if context.user_data.get("pasted_lyrics_mode"):
        context.user_data["mood"] = ""
        await replace_flow_message(
            context,
            update.message.reply_text,
            f"🌍 Language detected: {context.user_data['language']}\n\nChoose a singer voice:",
            reply_markup=_singer_keyboard(),
            state_key="song_flow_message_id",
        )
        return SINGER

    await replace_flow_message(
        context,
        update.message.reply_text,
        "😊 Choose a mood or type your own.\n\n"
        f"Examples:\n{_mood_examples_text('custom')}",
        reply_markup=_mood_keyboard("custom"),
        state_key="song_flow_message_id",
    )
    return MOOD


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

    if context.user_data.get("pasted_lyrics_mode"):
        context.user_data["mood"] = ""
        await query.edit_message_text(
            f"🌍 Language detected: {context.user_data['language']}\n\nChoose a singer voice:",
            reply_markup=_singer_keyboard(),
        )
        return SINGER

    await query.edit_message_text(
        "😊 Choose a mood or type your own.\n\n"
        f"Examples:\n{_mood_examples_text('custom')}",
        reply_markup=_mood_keyboard("custom"),
    )
    return MOOD


# -----------------------------
# GET TOPIC
# -----------------------------
async def get_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):

    topic, error_message = validate_topic(update.message.text)
    if error_message:
        await update.message.reply_text(error_message)
        return TOPIC

    context.user_data["topic"] = topic

    if context.user_data.get("pasted_lyrics_mode"):
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

    await replace_flow_message(
        context,
        update.message.reply_text,
        "✍️ Tell me more about the song prompt or story.\n\n"
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
        "🌍 Choose a language:",
        reply_markup=_language_keyboard(),
        state_key="song_flow_message_id",
    )
    return LANGUAGE


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
        "🌍 Choose a language:",
        reply_markup=_language_keyboard(),
    )
    return LANGUAGE


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
        "🎼 Choose a music style or type your own.\n\n"
        "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song",
        reply_markup=_music_style_keyboard(),
        state_key="song_flow_message_id",
    )
    return MUSIC_STYLE


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    if query is None:
        return DESCRIPTION

    context.user_data["description"] = ""
    await query.edit_message_text(
        "🎼 Choose a music style or type your own.\n\n"
        "Examples:\n- Remix\n- Rap\n- Romantic\n- Sad Song",
        reply_markup=_music_style_keyboard(),
    )
    return MUSIC_STYLE


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
    if context.user_data.get("pasted_lyrics_mode"):
        context.user_data["language"] = _detect_language_from_lyrics(lyrics)

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

    credit_reserved = deduct_credit(query.from_user.id)
    if not credit_reserved:
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

    generated_cover_task = asyncio.create_task(
        asyncio.to_thread(
            generate_cover_image,
            topic=topic,
            mood=mood,
            style=style,
            description=context.user_data.get("description", ""),
            lyrics=context.user_data.get("lyrics", ""),
            language=context.user_data.get("language", ""),
        )
    )
    context.user_data["generated_cover_task"] = generated_cover_task
    context.user_data.pop("generated_cover_image", None)

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
            text="🎬 Do you want to create a video?\n\nIf yes, you can upload an image, upload a video, or use the generated image.",
            reply_markup=_yes_no_keyboard(),
            state_key="song_flow_message_id",
        )
        return CONFIRM_VIDEO_START

    except Exception as e:
        context.user_data.pop("generated_cover_task", None)
        context.user_data.pop("generated_cover_image", None)
        if credit_reserved:
            refund_credit(query.from_user.id)
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
# CONFIRM VIDEO START
# -----------------------------
async def confirm_video_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    if query.data == "no":
        context.user_data.pop("generated_cover_task", None)
        context.user_data.pop("generated_cover_image", None)
        user = get_user(query.from_user.id)
        await query.edit_message_text(
            f"✅ All done!\n\n"
            f"💎 Remaining Credits: {user.credits}"
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "🎬 Choose visual source type for your video:",
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
        context.user_data["visual_upload_mode"] = "image"
        await query.edit_message_text(
            "🖼 Please upload one image to use as your cover."
        )
        return UPLOAD_COVER

    if query.data == "cover_upload_video":
        context.user_data["visual_upload_mode"] = "video"
        await query.edit_message_text(
            "🎞 Please upload one video to use as your music video source."
        )
        return UPLOAD_COVER

    if query.data == "cover_use_generated":
        await query.edit_message_text("🎨 Preparing generated image...\nFinalizing cover...")

        try:
            cover_image = await _resolve_generated_cover_image(context)
        except Exception:
            context.user_data.pop("generated_cover_task", None)
            context.user_data.pop("generated_cover_image", None)
            await query.edit_message_text(
                "❌ The generated image is not ready right now. Please upload an image or upload a video instead.",
                reply_markup=_cover_source_keyboard(),
            )
            return CHOOSE_COVER

        if not cover_image:
            await query.edit_message_text(
                "❌ The generated image is not available right now. Please upload an image or upload a video instead.",
                reply_markup=_cover_source_keyboard(),
            )
            return CHOOSE_COVER

        context.user_data["cover_image"] = cover_image
        context.user_data.pop("source_video_path", None)
        if context.user_data.get("song_id"):
            update_song_cover(context.user_data["song_id"], cover_image)
            update_song_source_video(context.user_data["song_id"], None)

        context.user_data["video_animation_style"] = "none"
        context.user_data["video_subtitle_prompt_pending"] = True
        await query.edit_message_text(
            "Step 1 of 1: Do you want to add subtitles to the video?\n\nSubtitles use extra credits.",
            reply_markup=_yes_no_keyboard(),
        )
        return CONFIRM_VIDEO

    topic = context.user_data["topic"]
    mood = context.user_data["mood"]
    style = context.user_data["style"]

    await query.edit_message_text(
        "❌ Generated image is not available right now. Please upload an image or upload a video instead.",
        reply_markup=_cover_source_keyboard(),
    )
    return CHOOSE_COVER


# -----------------------------
# RECEIVE UPLOADED COVER
# -----------------------------
async def receive_uploaded_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upload_mode = context.user_data.pop("visual_upload_mode", None)

    if update.message.photo:
        if upload_mode == "video":
            await update.message.reply_text("❌ Please upload a video.")
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
        context.user_data.pop("source_video_path", None)
        if context.user_data.get("song_id"):
            update_song_cover(context.user_data["song_id"], cover_path)
            update_song_source_video(context.user_data["song_id"], None)

        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=(
                f"✅ Cover image uploaded for \"{context.user_data.get('topic', 'your song')}\".\n\n"
                "Next you can choose whether to add subtitles.\n\n"
                "Create video now?"
            ),
            reply_markup=_yes_no_keyboard(),
            state_key="song_flow_message_id",
        )
        return CONFIRM_VIDEO

    video_message = update.message.video
    document_message = update.message.document
    if video_message or document_message:
        if upload_mode == "image":
            await update.message.reply_text("❌ Please upload an image.")
            return UPLOAD_COVER

        file_id = video_message.file_id if video_message else document_message.file_id
        telegram_file = await context.bot.get_file(file_id)

        os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)
        source_video_path = os.path.join(
            GENERATED_VIDEOS_DIR,
            f"source_{update.effective_user.id}_{uuid.uuid4().hex}.mp4"
        )
        await telegram_file.download_to_drive(source_video_path)

        context.user_data["source_video_path"] = source_video_path
        context.user_data.pop("cover_image", None)
        if context.user_data.get("song_id"):
            update_song_source_video(context.user_data["song_id"], source_video_path)
            update_song_cover(context.user_data["song_id"], None)

        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=(
                f"✅ Source video uploaded for \"{context.user_data.get('topic', 'your song')}\".\n\n"
                "Next you can choose whether to add subtitles.\n\n"
                "Create video now?"
            ),
            reply_markup=_yes_no_keyboard(),
            state_key="song_flow_message_id",
        )
        return CONFIRM_VIDEO

    await update.message.reply_text("❌ Please upload an image or a video.")
    return UPLOAD_COVER


# -----------------------------
# CONFIRM VIDEO
# -----------------------------
async def confirm_video(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    user = get_user(query.from_user.id)
    source_video_path = context.user_data.get("source_video_path")

    if not context.user_data.get("video_subtitle_prompt_pending"):
        if query.data == "no":
            try:
                await query.delete_message()
            except Exception:
                pass
            return ConversationHandler.END

        context.user_data["video_animation_style"] = "none"
        context.user_data["video_subtitle_prompt_pending"] = True
        await query.edit_message_text(
            (
                "Step 1 of 1: Do you want to add subtitles to the uploaded video?\n\nSubtitles use extra credits."
                if source_video_path
                else "Step 1 of 1: Do you want to add subtitles to the video?\n\nSubtitles use extra credits."
            ),
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_VIDEO

    subtitles_enabled = query.data == "yes"
    if subtitles_enabled and (not user or user.credits <= 10):
        from app.handlers.buycredits import _buy_credits_menu_markup
        await query.edit_message_text(
            "❌ You need more than 10 credits to create a video with subtitles.\n\n"
            "💎 Please add credits or create the video without subtitles.",
            reply_markup=_buy_credits_menu_markup(query.from_user.id)
        )
        return BUY_CREDITS

    context.user_data.pop("video_subtitle_prompt_pending", None)

    subtitle_credit_reserved = False
    if subtitles_enabled:
        subtitle_credit_reserved = deduct_credit(query.from_user.id, minimum_credits=11)
        if not subtitle_credit_reserved:
            from app.handlers.buycredits import _buy_credits_menu_markup
            await query.edit_message_text(
                "❌ You need more than 10 credits to create a video with subtitles.\n\n"
                "💎 Please add credits or create the video without subtitles.",
                reply_markup=_buy_credits_menu_markup(query.from_user.id)
            )
            return BUY_CREDITS

    mp3_file = context.user_data["mp3_file"]
    cover_image = context.user_data.get("cover_image")
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
            source_video_path=source_video_path,
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
            user = get_user(query.from_user.id)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ All done!\n\n💎 Remaining Credits: {user.credits}"
        )
        clear_flow_message_tracking(context, state_key="song_flow_message_id")

    except Exception as e:
        if subtitle_credit_reserved:
            refund_credit(query.from_user.id)
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
        ),
        CallbackQueryHandler(create_song, pattern=r"^create_song$"),
    ],
    states={
        CHOOSE_TYPE: [
            CallbackQueryHandler(choose_type, pattern=r"^type_"),
            CallbackQueryHandler(pick_saved_lyrics, pattern=r"^lyr_pick_\d+$"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        LANGUAGE: [
            CallbackQueryHandler(get_language, pattern=r"^lang_"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        MUSIC_STYLE: [
            CallbackQueryHandler(choose_music_style, pattern=r"^mstyle_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_music_style),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        TOPIC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_topic),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        MOOD: [
            CallbackQueryHandler(choose_mood, pattern=r"^mood_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_mood),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        DESCRIPTION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_description),
            CallbackQueryHandler(skip_description, pattern=r"^desc_skip$"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        PASTE_LYRICS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_pasted_lyrics),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        SINGER: [
            CallbackQueryHandler(get_singer, pattern=r"^singer_"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        LYRICS_ACTION: [
            CallbackQueryHandler(lyrics_action, pattern=r"^lyrics_"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        EDIT_LYRICS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_lyrics),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        CONFIRM_MP3: [
            CallbackQueryHandler(confirm_mp3),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        CONFIRM_VIDEO_START: [
            CallbackQueryHandler(confirm_video_start),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        CHOOSE_COVER: [
            CallbackQueryHandler(choose_cover_source, pattern=r"^cover_"),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        UPLOAD_COVER: [
            MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.VIDEO, receive_uploaded_cover),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        CONFIRM_VIDEO: [
            CallbackQueryHandler(confirm_video),
            CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")
        ],
        BUY_CREDITS: [
            CallbackQueryHandler(confirm_video, pattern=r"^cancel_flow$|^no$"),
            CallbackQueryHandler(
                payment_info,
                pattern=r"^(buy_|payment_|buycredits_menu$|freecredits_info$)"
            ),
        ],
    },
    fallbacks=[CallbackQueryHandler(cancel_flow_handler, pattern=r"^cancel_flow$")],
    allow_reentry=True,
)

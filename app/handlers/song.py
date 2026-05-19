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

from app.database.queries import (
    deduct_credit,
    get_enabled_song_languages,
    get_song_by_id,
    get_user,
    get_user_songs,
    save_song,
    update_song_cover,
    update_song_mp3,
    update_song_subtitle_timing,
    update_song_video,
)
from app.services.image_service import generate_cover_image
from app.services.music_service import generate_music
from app.services.openai_service import generate_lyrics, generate_subtitle_timing
from app.services.video_service import create_music_video
from app.states.song_states import (
    CHOOSE_COVER,
    CHOOSE_TYPE,
    CONFIRM_COVER,
    CONFIRM_MP3,
    CONFIRM_VIDEO,
    LANGUAGE,
    MOOD,
    PASTE_LYRICS,
    SINGER,
    STYLE,
    TOPIC,
    UPLOAD_COVER,
)
from app.utils.helpers import (
    send_audio_with_status,
    send_photo_with_status,
    send_video_with_status,
    start_progress_message,
    stop_progress_message,
)


def _yes_no_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data="yes"),
            InlineKeyboardButton("❌ No", callback_data="no"),
        ]
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


async def _safe_answer(query):
    """Answer a callback query, ignoring expired/invalid query errors."""
    try:
        await query.answer()
    except tg_error.BadRequest:
        pass  # query expired (>30s old) — safe to ignore


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

    telegram_id = update.effective_user.id
    user = get_user(telegram_id)

    if user.credits <= 0:
        await update.message.reply_text(
            "❌ You don't have enough credits.\n\n"
            "💎 Please buy more credits."
        )
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 New Lyrics", callback_data="type_new")],
        [InlineKeyboardButton("📝 My Lyrics", callback_data="type_mylyrics")],
        [InlineKeyboardButton("📋 Past Lyric", callback_data="type_paste")],
    ])
    await update.message.reply_text(
        f"💎 Credits: {user.credits}\n\nChoose how you want to create your song:",
        reply_markup=keyboard
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
        await query.edit_message_text("🎼 What music style do you want?\n\nExample:\n- Remix\n- Rap\n- Romantic\n- Sad Song")
        return STYLE

    if query.data == "type_paste":
        context.user_data.clear()
        await query.edit_message_text("📋 Please paste your lyrics:")
        return PASTE_LYRICS

    # type_mylyrics — show saved lyrics list that has not been converted yet
    songs = get_user_songs(query.from_user.id)
    songs_with_lyrics = [s for s in songs if s.lyrics and not s.mp3_path]
    if not songs_with_lyrics:
        await query.edit_message_text(
            "You don't have any saved lyrics waiting for MP3 conversion yet. "
            "Use New Lyrics or Past Lyric first."
        )
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(s.topic, callback_data=f"lyr_pick_{s.id}")] for s in songs_with_lyrics]
    await query.edit_message_text(
        "📜 Select lyrics to convert to MP3:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
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

    lyrics_preview = str(song.lyrics)[:300] + "..." if song.lyrics and len(str(song.lyrics)) > 300 else song.lyrics
    await query.edit_message_text(
        f"📜 Topic: {song.topic}\n\n{lyrics_preview}\n\n🎧 Do you want to convert this to MP3?",
        reply_markup=_yes_no_keyboard()
    )
    return CONFIRM_MP3


# -----------------------------
# GET PASTED LYRICS
# -----------------------------
async def get_pasted_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["lyrics"] = update.message.text
    await update.message.reply_text("🎼 What music style do you want?\n\nExample:\n- Remix\n- Rap\n- Romantic\n- Sad Song")
    return STYLE


# -----------------------------
# GET STYLE
# -----------------------------
async def get_style(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["style"] = update.message.text
    await update.message.reply_text("📝 What is the song topic?")
    return TOPIC


# -----------------------------
# GET TOPIC
# -----------------------------
async def get_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["topic"] = update.message.text
    await update.message.reply_text(
        "😊 What mood should the song have?\n\n"
        "Example:\n"
        "- Happy\n"
        "- Emotional\n"
        "- Sad\n"
        "- Energetic"
    )
    return MOOD


# -----------------------------
# GET MOOD
# -----------------------------
async def get_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["mood"] = update.message.text
    await update.message.reply_text(
        "🌍 Choose a language:",
        reply_markup=_language_keyboard()
    )
    return LANGUAGE


# -----------------------------
# GET LANGUAGE - Generate Lyrics or Ask for Lyrics
# -----------------------------
async def get_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    selected_language = query.data.replace("lang_", "", 1)

    if selected_language == "Khmer":
        await query.edit_message_text(
            "🇰🇭 Khmer language is now in construction. Please choose another language.",
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
        _save_lyrics_draft(context, query.from_user.id)
        lyrics_msg = f"📝 Your Lyrics\n\n{lyrics}"
        if len(lyrics_msg) > 4096:
            lyrics_msg = lyrics_msg[:4090] + "..."
        await query.edit_message_text(
            f"🌍 Language selected: {language}\n🎤 Singer: {singer_gender.title()}"
        )
        await context.bot.send_message(chat_id=query.message.chat_id, text=lyrics_msg)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎧 Do you want to convert this to MP3?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_MP3

    style = context.user_data["style"]
    topic = context.user_data["topic"]
    mood = context.user_data["mood"]

    await query.edit_message_text(
        f"🌍 Language selected: {language}\n🎤 Singer: {singer_gender.title()}"
    )
    progress_message = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="⏳ Generating lyrics... 0%"
    )
    progress_task, progress_stop = await start_progress_message(
        progress_message,
        "⏳ Generating lyrics..."
    )

    try:
        lyrics = await asyncio.to_thread(
            generate_lyrics,
            style=style, topic=topic, mood=mood, language=language
        )
        await stop_progress_message(
            progress_task,
            progress_stop,
            progress_message,
            "✅ Lyrics generated 100%"
        )
        context.user_data["lyrics"] = lyrics
        _save_lyrics_draft(context, query.from_user.id)

        lyrics_msg = f"🎵 Your AI Lyrics\n\n{lyrics}"
        if len(lyrics_msg) > 4096:
            lyrics_msg = lyrics_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=lyrics_msg)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎧 Do you want to convert this to MP3?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_MP3

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


# -----------------------------
# CONFIRM MP3
# -----------------------------
async def confirm_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    if query.data == "no":
        await query.edit_message_text("👍 Okay! Song creation stopped.")
        return ConversationHandler.END

    style = context.user_data["style"]
    topic = context.user_data["topic"]
    mood = context.user_data["mood"]
    lyrics = context.user_data["lyrics"]

    await query.edit_message_text("⏳ Generating MP3... 0%")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "⏳ Generating MP3..."
    )

    try:
        mp3_file = await asyncio.to_thread(
            generate_music,
            style=style, topic=topic, mood=mood, lyrics=lyrics,
            language=context.user_data.get("language", ""),
            singer_gender=context.user_data.get("singer_gender", "female")
        )
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "✅ MP3 generated 100%"
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
                caption="🎵 Your AI Generated Song",
                status_message=query.message,
                upload_text="⏫ MP3 ready. Uploading to Telegram...",
                complete_text="✅ MP3 uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎨 Do you want to generate a cover image?",
            reply_markup=_yes_no_keyboard()
        )
        return CONFIRM_COVER

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "❌ MP3 generation failed"
        )
        error_msg = f"❌ Error generating MP3:\n{str(e)}"
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

    await query.edit_message_text("⏳ Generating cover image... 0%")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "⏳ Generating cover image..."
    )

    try:
        cover_image = await asyncio.to_thread(
            generate_cover_image,
            topic=topic, mood=mood, style=style
        )
        await stop_progress_message(progress_task, progress_stop)
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

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎬 Do you want to create a music video?",
            reply_markup=_yes_no_keyboard()
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

    os.makedirs("media/generated/covers", exist_ok=True)
    cover_path = os.path.join(
        "media",
        "generated",
        "covers",
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

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🎬 Do you want to create a music video?",
        reply_markup=_yes_no_keyboard()
    )
    return CONFIRM_VIDEO


# -----------------------------
# CONFIRM VIDEO
# -----------------------------
async def confirm_video(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await _safe_answer(query)

    user = get_user(query.from_user.id)

    if not context.user_data.get("video_subtitle_prompt_pending"):
        if query.data == "no":
            await query.edit_message_text(
                f"✅ All done!\n\n"
                f"💎 Remaining Credits: {user.credits}"
            )
            return ConversationHandler.END

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

    if subtitles_enabled and context.user_data.get("lyrics"):
        try:
            subtitle_timing = await asyncio.to_thread(
                generate_subtitle_timing,
                mp3_file,
                context.user_data["lyrics"],
                context.user_data.get("language", "")
            )
        except Exception:
            subtitle_timing = []
        context.user_data["subtitle_timing"] = subtitle_timing
        if subtitle_timing and context.user_data.get("song_id"):
            update_song_subtitle_timing(
                context.user_data["song_id"],
                json.dumps(subtitle_timing, ensure_ascii=False)
            )

    await query.edit_message_text("⏳ Creating music video... 0%")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "⏳ Creating music video..."
    )

    try:
        safe_topic = "_".join(topic.split())
        video_path = f"media/generated/videos/{query.from_user.id}_{safe_topic}.mp4"
        os.makedirs("media/generated/videos", exist_ok=True)

        await asyncio.to_thread(
            create_music_video,
            audio_path=mp3_file,
            image_path=cover_image,
            output_path=video_path,
            lyrics=context.user_data.get("lyrics"),
            subtitle_timing=subtitle_timing,
            subtitles_enabled=subtitles_enabled
        )
        await stop_progress_message(progress_task, progress_stop)
        if context.user_data.get("song_id"):
            update_song_video(context.user_data["song_id"], video_path)

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption="🎬 Your AI Music Video",
                status_message=query.message,
                upload_text="⏫ Video ready. Uploading to Telegram...",
                complete_text="✅ Video uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        if subtitles_enabled:
            deduct_credit(query.from_user.id)
            user = get_user(query.from_user.id)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ All done!\n\n💎 Remaining Credits: {user.credits}"
        )

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
        PASTE_LYRICS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_pasted_lyrics)
        ],
        STYLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_style)
        ],
        TOPIC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_topic)
        ],
        MOOD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_mood)
        ],
        LANGUAGE: [
            CallbackQueryHandler(get_language, pattern=r"^lang_")
        ],
        SINGER: [
            CallbackQueryHandler(get_singer, pattern=r"^singer_")
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

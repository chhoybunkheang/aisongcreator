import asyncio
import json
import os
import uuid

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram import (
    error as tg_error,
)
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.database.queries import (
    deduct_credit,
    get_song_by_id,
    get_user,
    get_user_songs,
    update_song_cover,
    update_song_lyrics,
    update_song_mp3,
    update_song_subtitle_timing,
    update_song_video,
)
from app.services.image_service import generate_cover_image
from app.services.music_service import generate_music
from app.services.openai_service import (
    generate_subtitle_timing,
    transcribe_lyrics_from_mp3,
)
from app.services.video_service import create_music_video
from app.utils.helpers import (
    clear_flow_message_tracking,
    make_progress_notifier,
    replace_flow_message,
    retry_telegram_call,
    send_audio_with_status,
    send_photo_with_status,
    send_video_with_status,
    start_progress_message,
    stop_progress_message,
)


async def _safe_answer(query):
    """Answer a callback query, ignoring expired/invalid query errors."""
    try:
        await query.answer()
    except tg_error.BadRequest:
        pass  # query expired (>30s old) - safe to ignore


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


def _lyrics_list_label(song):
    return f"{song.topic} {_language_flag(song.language)}"


def _mp3_list_label(song):
    parts = [song.topic, _language_flag(song.language)]

    if song.lyrics:
        parts.append("📝")

    if song.video_path and os.path.exists(song.video_path):
        parts.append("🎬")

    return " ".join(parts)


def _mp4_list_label(song):
    parts = [song.topic, _language_flag(song.language)]

    if song.subtitle_timing:
        parts.append("💬")

    return " ".join(parts)


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

    return f"Error generating MP3:\n{error_text}"


def _cover_source_keyboard(song_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Upload Image", callback_data=f"ms_cov_upload_{song_id}"),
            InlineKeyboardButton("🎨 Generate Image", callback_data=f"ms_cov_{song_id}"),
        ]
    ])


def _mp3_action_markup(song):
    rows = []
    has_video = bool(song.video_path and os.path.exists(song.video_path))
    if has_video:
        rows.append([
            InlineKeyboardButton("🎬 Watch", callback_data=f"watchvid_{song.id}"),
            InlineKeyboardButton("🎧 Listen", callback_data=f"play_{song.id}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🎬 Create Video", callback_data=f"mp3video_{song.id}"),
            InlineKeyboardButton("🎧 Listen", callback_data=f"play_{song.id}"),
        ])

    if not song.lyrics:
        rows.append([
            InlineKeyboardButton("📝 Lyrics From MP3", callback_data=f"mp3lyrics_{song.id}"),
        ])

    return InlineKeyboardMarkup(rows)


# -----------------------------------
# HELPER: build keyboard for next step
# -----------------------------------
def _next_step_markup(song):

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

        return f"Error generating MP3:\n{error_text}"
    sid = song.id
    if not song.mp3_path or not os.path.exists(song.mp3_path):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Generate MP3", callback_data=f"ms_mp3_{sid}"),
            InlineKeyboardButton("Skip", callback_data=f"ms_skip_{sid}"),
        ]])
    if not song.cover_path or not os.path.exists(song.cover_path):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Generate Cover", callback_data=f"ms_cov_{sid}"),
            InlineKeyboardButton("Skip", callback_data=f"ms_skip_{sid}"),
        ]])
    if not song.video_path or not os.path.exists(song.video_path):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Generate Video", callback_data=f"ms_vid_prompt_{sid}"),
            InlineKeyboardButton("Skip", callback_data=f"ms_skip_{sid}"),
        ]])
    return None


# -----------------------------------
# MY SONGS LIST
# -----------------------------------
async def my_songs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    songs = get_user_songs(telegram_id)
    visible_songs = [
        song for song in songs
        if song.lyrics or song.mp3_path or song.cover_path or song.video_path
    ]

    if not visible_songs:
        await replace_flow_message(
            context,
            update.message.reply_text,
            "You don't have any songs yet.",
            state_key="mysongs_flow_message_id",
        )
        return

    keyboard = []
    for song in visible_songs:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{song.topic}",
                callback_data=f"song_{song.id}"
            )
        ])

    await replace_flow_message(
        context,
        update.message.reply_text,
        "Your Songs:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        state_key="mysongs_flow_message_id",
    )


# -----------------------------------
# SONG DETAIL + offer next step
# -----------------------------------
async def song_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = query.data.split("_")[1]
    song = get_song_by_id(song_id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    message = (
        f"Topic: {song.topic}\n"
        f"Music Style: {song.style}\n"
        f"Mood: {song.mood}\n"
        f"Language: {song.language}\n\n"
        f"{song.lyrics}"
    )
    if len(message) > 4000:
        message = message[:4000] + "\n\n..."

    # Send existing MP3 if available
    if song.mp3_path and os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text=message)
        with open(song.mp3_path, "rb") as audio:
            await send_audio_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                audio=audio,
                title=song.topic,
                read_timeout=300,
                write_timeout=300,
            )
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text=message)

    markup = _next_step_markup(song)
    if markup:
        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=query.message.chat_id,
            text="What do you want to generate?",
            reply_markup=markup,
            state_key="mysongs_flow_message_id",
        )
    else:
        clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")


# -----------------------------------
# GENERATE MP3
# -----------------------------------
async def ms_gen_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[2])
    song = get_song_by_id(song_id)
    user = get_user(query.from_user.id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    if not user or user.credits <= 0:
        await query.edit_message_text(
            "❌ You don't have enough credits.\n\n"
            "💎 Please buy more credits."
        )
        return

    await query.edit_message_text("Generating MP3...\nPreparing request...")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "Generating MP3...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        mp3_file = await asyncio.to_thread(
            generate_music,
            style=song.style,
            topic=song.topic,
            mood=song.mood,
            lyrics=song.lyrics,
            language=song.language or "",
            progress_callback=progress_callback,
        )
        await stop_progress_message(progress_task, progress_stop)
        deduct_credit(query.from_user.id)
        update_song_mp3(song_id, mp3_file)
        subtitle_timing = []
        try:
            subtitle_timing = await asyncio.to_thread(
                generate_subtitle_timing,
                mp3_file,
                song.lyrics,
                song.language or ""
            )
        except Exception:
            subtitle_timing = []
        if subtitle_timing:
            update_song_subtitle_timing(song_id, json.dumps(subtitle_timing, ensure_ascii=False))

        with open(mp3_file, "rb") as audio:
            await send_audio_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                audio=audio,
                title=song.topic,
                caption="Your AI Generated Song",
                status_message=query.message,
                upload_text="MP3 ready. Uploading to Telegram...",
                complete_text="MP3 uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        song = get_song_by_id(song_id)
        markup = _next_step_markup(song)
        if markup:
            await replace_flow_message(
                context,
                context.bot.send_message,
                chat_id=query.message.chat_id,
                text="What do you want to generate next?",
                reply_markup=markup,
                state_key="mysongs_flow_message_id",
            )
        else:
            clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
            await context.bot.send_message(chat_id=query.message.chat_id, text="All done!")

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "MP3 generation failed"
        )
        error_msg = _friendly_mp3_error_message(e)
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


# -----------------------------------
# GENERATE COVER
# -----------------------------------
async def ms_gen_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[2])
    song = get_song_by_id(song_id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    await query.edit_message_text("Generating cover image...\nPreparing request...")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "Generating cover image...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        cover_image = await asyncio.to_thread(
            generate_cover_image,
            topic=song.topic,
            mood=song.mood,
            style=song.style,
            progress_callback=progress_callback,
        )
        await stop_progress_message(progress_task, progress_stop)
        update_song_cover(song_id, cover_image)

        with open(cover_image, "rb") as photo:
            await send_photo_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                photo=photo,
                caption="AI Generated Cover",
                status_message=query.message,
                upload_text="Cover ready. Uploading to Telegram...",
                complete_text="Cover uploaded",
            )

        song = get_song_by_id(song_id)
        markup = _next_step_markup(song)
        if markup:
            await replace_flow_message(
                context,
                context.bot.send_message,
                chat_id=query.message.chat_id,
                text="What do you want to generate next?",
                reply_markup=markup,
                state_key="mysongs_flow_message_id",
            )
        else:
            clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
            await context.bot.send_message(chat_id=query.message.chat_id, text="All done!")

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Cover generation failed"
        )
        error_msg = f"Error generating cover:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


# -----------------------------------
# GENERATE VIDEO
# -----------------------------------
async def ms_prompt_video_subtitles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[3])
    song = get_song_by_id(song_id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    if not song.mp3_path or not song.cover_path:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="MP3 and cover image are required before generating a video."
        )
        return

    await query.edit_message_text(
        "Do you want to add subtitles to the video?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_yes_{song.id}"),
            InlineKeyboardButton("❌ No", callback_data=f"ms_vid_no_{song.id}"),
        ]])
    )


async def ms_gen_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    choice = query.data.split("_")[2]
    song_id = int(query.data.split("_")[3])
    song = get_song_by_id(song_id)
    user = get_user(query.from_user.id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    if not song.mp3_path or not song.cover_path:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="MP3 and cover image are required before generating a video."
        )
        return

    if choice == "yes" and (not user or user.credits <= 10):
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ You need more than 10 credits to create a video with subtitles.\n\n"
                "💎 Please add credit or create the video without subtitles."
            )
        )
        return

    subtitles_enabled = choice == "yes"
    await query.edit_message_text(
        "Generating subtitles...\nPreparing request..."
        if subtitles_enabled and song.lyrics and song.mp3_path
        else "Creating music video...\nPreparing render..."
    )
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "Generating subtitles..." if subtitles_enabled and song.lyrics and song.mp3_path else "Creating music video...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        safe_topic = "_".join(song.topic.split())
        video_path = f"media/generated/videos/{song_id}_{safe_topic}.mp4"
        os.makedirs("media/generated/videos", exist_ok=True)
        subtitle_timing = None
        if subtitles_enabled and song.lyrics and song.mp3_path:
            try:
                generated_timing = await asyncio.to_thread(
                    generate_subtitle_timing,
                    song.mp3_path,
                    song.lyrics,
                    song.language or "",
                    progress_callback=progress_callback,
                )
            except Exception:
                generated_timing = []
            subtitle_timing = json.dumps(generated_timing, ensure_ascii=False) if generated_timing else None
            if generated_timing:
                update_song_subtitle_timing(song_id, subtitle_timing)

        await asyncio.to_thread(
            create_music_video,
            audio_path=song.mp3_path,
            image_path=song.cover_path,
            output_path=video_path,
            lyrics=song.lyrics,
            subtitle_timing=subtitle_timing,
            subtitles_enabled=subtitles_enabled,
            progress_callback=progress_callback,
        )
        await stop_progress_message(progress_task, progress_stop)
        update_song_video(song_id, video_path)

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption="Your AI Music Video",
                status_message=query.message,
                upload_text="Video ready. Uploading to Telegram...",
                complete_text="Video uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        if subtitles_enabled:
            deduct_credit(query.from_user.id)
            user = get_user(query.from_user.id)

        if subtitles_enabled and user:
            clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"All done!\n\n💎 Remaining Credits: {user.credits}"
            )
        else:
            clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
            await context.bot.send_message(chat_id=query.message.chat_id, text="All done!")

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Video creation failed"
        )
        error_msg = f"Error creating video:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


# -----------------------------------
# SKIP
# -----------------------------------
async def ms_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
    await query.edit_message_text("Okay! Skipped.")


# -----------------------------------
# MY LYRICS LIST
# -----------------------------------
async def my_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    songs = get_user_songs(telegram_id)
    songs_with_lyrics = [s for s in songs if s.lyrics]

    if not songs_with_lyrics:
        await replace_flow_message(
            context,
            update.message.reply_text,
            "You don't have any lyrics yet.",
            state_key="mysongs_flow_message_id",
        )
        return

    keyboard = [[
        InlineKeyboardButton(_lyrics_list_label(s), callback_data=f"lyr_{s.id}")
    ] for s in songs_with_lyrics]
    await replace_flow_message(
        context,
        update.message.reply_text,
        "Your Lyrics — tap to read:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        state_key="mysongs_flow_message_id",
    )


async def lyrics_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Lyrics not found.")
        return

    text = (
        f"Topic: {song.topic}\n"
        f"Music Style: {song.style}\n"
        f"Mood: {song.mood}\n"
        f"Language: {song.language}\n\n"
        f"{song.lyrics}"
    )
    if len(text) > 4096:
        text = text[:4090] + "\n\n..."

    await replace_flow_message(
        context,
        context.bot.send_message,
        chat_id=query.message.chat_id,
        text=text,
        state_key="mysongs_flow_message_id",
    )


# -----------------------------------
# HANDLERS
# -----------------------------------
mysongs_handler = MessageHandler(
    filters.TEXT & filters.Regex("My Songs"),
    my_songs
)


# -----------------------------------
# MY MP3 LIST
# -----------------------------------
async def my_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    songs = get_user_songs(telegram_id)
    songs_with_mp3 = [s for s in songs if s.mp3_path and os.path.exists(s.mp3_path)]

    if not songs_with_mp3:
        await replace_flow_message(
            context,
            update.message.reply_text,
            "You don't have any generated MP3s yet.",
            state_key="mysongs_flow_message_id",
        )
        return

    keyboard = [[
        InlineKeyboardButton(
            _mp3_list_label(s),
            callback_data=f"mp3_{s.id}"
        )
    ] for s in songs_with_mp3]
    await replace_flow_message(
        context,
        update.message.reply_text,
        "Your MP3s — tap a song to choose an action:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        state_key="mysongs_flow_message_id",
    )


async def mp3_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    if song.video_path and os.path.exists(song.video_path):
        await query.edit_message_text(
            f"Choose what you want to do with \"{song.topic}\":",
            reply_markup=_mp3_action_markup(song)
        )
        context.chat_data["mysongs_flow_message_id"] = query.message.message_id
        return

    cover_missing = not (song.cover_path and os.path.exists(song.cover_path))
    message = f"\"{song.topic}\" has not been converted to video yet."
    if cover_missing:
        message += " Choose Create Video to generate a cover first, then continue to video creation."
    else:
        message += " Choose Create Video to continue with video creation, or Listen to play the MP3."

    await query.edit_message_text(
        message,
        reply_markup=_mp3_action_markup(song)
    )
    context.chat_data["mysongs_flow_message_id"] = query.message.message_id


async def mp3_video_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    if not song.cover_path or not os.path.exists(song.cover_path):
        await query.edit_message_text(
            f"\"{song.topic}\" needs a cover image before video creation. Choose cover image type:",
            reply_markup=_cover_source_keyboard(song.id)
        )
        return

    await query.edit_message_text(
        f"The cover image for \"{song.topic}\" is ready. Create the music video now?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_prompt_{song.id}"),
            InlineKeyboardButton("❌ No", callback_data=f"ms_skip_{song.id}"),
        ]])
    )
    context.chat_data["mysongs_flow_message_id"] = query.message.message_id


async def play_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    with open(song.mp3_path, "rb") as audio:
        await send_audio_with_status(
            context.bot,
            chat_id=query.message.chat_id,
            audio=audio,
            title=song.topic,
            read_timeout=300,
            write_timeout=300,
        )


async def ms_upload_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[3])
    context.user_data["ms_cover_song_id"] = song_id

    await query.edit_message_text("🖼 Please upload one image to use as your cover.")


async def ms_receive_uploaded_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    song_id = context.user_data.get("ms_cover_song_id")

    if not song_id:
        return

    song = get_song_by_id(song_id)
    if not song:
        await retry_telegram_call(update.message.reply_text, "Song not found.")
        context.user_data.pop("ms_cover_song_id", None)
        return

    if not update.message.photo:
        await retry_telegram_call(update.message.reply_text, "❌ Please upload an image.")
        return

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

    update_song_cover(song_id, cover_path)
    context.user_data.pop("ms_cover_song_id", None)

    with open(cover_path, "rb") as photo_file:
        await send_photo_with_status(
            context.bot,
            chat_id=update.effective_chat.id,
            photo=photo_file,
            caption="🖼 Uploaded Cover Image",
        )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"The cover image for \"{song.topic}\" is ready. Create the music video now?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_prompt_{song.id}"),
            InlineKeyboardButton("❌ No", callback_data=f"ms_skip_{song.id}"),
        ]])
    )
    clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")


async def mp3_to_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    await query.edit_message_text("⏳ Recovering lyrics from MP3...\nPreparing request...")
    progress_task, progress_stop = await start_progress_message(
        query.message,
        "⏳ Recovering lyrics from MP3...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        lyrics = await asyncio.to_thread(
            transcribe_lyrics_from_mp3,
            song.mp3_path,
            song.language or "",
            progress_callback=progress_callback,
        )
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "✅ Lyrics recovered 100%"
        )

        update_song_lyrics(song_id, lyrics)

        lyrics_text = f"📝 Recovered Lyrics for {song.topic}\n\n{lyrics}"
        if len(lyrics_text) > 4096:
            lyrics_text = lyrics_text[:4090] + "..."

        await context.bot.send_message(chat_id=query.message.chat_id, text=lyrics_text)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"✅ Lyrics recovered for \"{song.topic}\".\n\n"
                "Please check it in 📝 My Lyrics."
            )
        )

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Lyrics recovery failed"
        )
        error_msg = f"Error recovering lyrics from MP3:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


# -----------------------------------
# MY MP4 LIST
# -----------------------------------
async def my_mp4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    songs = get_user_songs(telegram_id)
    songs_with_video = [s for s in songs if s.video_path and os.path.exists(s.video_path)]

    if not songs_with_video:
        await replace_flow_message(
            context,
            update.message.reply_text,
            "You don't have any generated videos yet.",
            state_key="mysongs_flow_message_id",
        )
        return

    keyboard = [[
        InlineKeyboardButton(_mp4_list_label(s), callback_data=f"watchvid_{s.id}")
    ] for s in songs_with_video]
    await replace_flow_message(
        context,
        update.message.reply_text,
        "Your Videos — tap to watch:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        state_key="mysongs_flow_message_id",
    )


async def watch_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.video_path or not os.path.exists(song.video_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="Video file not found.")
        return

    with open(song.video_path, "rb") as video:
        await send_video_with_status(
            context.bot,
            chat_id=query.message.chat_id,
            video=video,
            caption=song.topic,
            read_timeout=300,
            write_timeout=300,
        )

    if song.mp3_path and os.path.exists(song.mp3_path) and song.cover_path and os.path.exists(song.cover_path):
        button_text = "Update Subtitles" if song.subtitle_timing else "Add Subtitle"
        await replace_flow_message(
            context,
            context.bot.send_message,
            chat_id=query.message.chat_id,
            text="Video actions:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(button_text, callback_data=f"vidsub_{song.id}"),
            ]]),
            state_key="mysongs_flow_message_id",
        )
    else:
        clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")


async def add_subtitle_to_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    callback_parts = query.data.split("_")
    action = "yes"
    if len(callback_parts) == 2:
        song_id = int(callback_parts[1])
    elif len(callback_parts) == 3:
        action = callback_parts[1]
        song_id = int(callback_parts[2])
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Invalid subtitle action.")
        return

    song = get_song_by_id(song_id)
    user = get_user(query.from_user.id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    if action == "no":
        with open(song.video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption=song.topic,
                read_timeout=300,
                write_timeout=300,
            )
        return

    if not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    if not song.cover_path or not os.path.exists(song.cover_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="Cover image not found.")
        return

    if not user or user.credits <= 10:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ You need more than 10 credits to add or update subtitles.\n\n"
                "💎 Please add credit."
            )
        )
        return

    progress_message = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            "Updating subtitles...\nPreparing request..."
            if song.subtitle_timing else "Adding subtitles...\nPreparing request..."
        )
    )
    progress_task, progress_stop = await start_progress_message(
        progress_message,
        "Updating subtitles..." if song.subtitle_timing else "Adding subtitles...",
        auto_increment=False,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), progress_message)

    try:
        subtitle_timing = None
        if song.lyrics:
            try:
                generated_timing = await asyncio.to_thread(
                    generate_subtitle_timing,
                    song.mp3_path,
                    song.lyrics,
                    song.language or "",
                    progress_callback=progress_callback,
                )
            except Exception:
                generated_timing = []

            if generated_timing:
                subtitle_timing = json.dumps(generated_timing, ensure_ascii=False)
                update_song_subtitle_timing(song_id, subtitle_timing)

        safe_topic = "_".join(song.topic.split())
        video_path = f"media/generated/videos/{song_id}_{safe_topic}.mp4"
        os.makedirs("media/generated/videos", exist_ok=True)

        await asyncio.to_thread(
            create_music_video,
            audio_path=song.mp3_path,
            image_path=song.cover_path,
            output_path=video_path,
            lyrics=song.lyrics,
            subtitle_timing=subtitle_timing,
            progress_callback=progress_callback,
        )
        update_song_video(song_id, video_path)
        await stop_progress_message(progress_task, progress_stop)

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption=f"{song.topic} with subtitles",
                status_message=progress_message,
                upload_text="Subtitled video ready. Uploading to Telegram...",
                complete_text="Subtitled video uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        deduct_credit(query.from_user.id)
        user = get_user(query.from_user.id)
        if user:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Subtitles updated.\n\n💎 Remaining Credits: {user.credits}"
            )

    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            progress_message,
            "Adding subtitles failed"
        )
        error_msg = f"Error adding subtitles:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


song_detail_handler = CallbackQueryHandler(
    song_detail,
    pattern=r"^song_\d+$"
)

ms_mp3_handler = CallbackQueryHandler(ms_gen_mp3, pattern=r"^ms_mp3_\d+$")
ms_cov_handler = CallbackQueryHandler(ms_gen_cover, pattern=r"^ms_cov_\d+$")
ms_cov_upload_handler = CallbackQueryHandler(ms_upload_cover, pattern=r"^ms_cov_upload_\d+$")
ms_vid_handler = CallbackQueryHandler(ms_prompt_video_subtitles, pattern=r"^ms_vid_prompt_\d+$")
ms_vid_choice_handler = CallbackQueryHandler(ms_gen_video, pattern=r"^ms_vid_(yes|no)_\d+$")
ms_skip_handler = CallbackQueryHandler(ms_skip, pattern=r"^ms_skip_\d+$")
ms_uploaded_cover_handler = MessageHandler(filters.PHOTO, ms_receive_uploaded_cover)

mymp3_handler = MessageHandler(filters.TEXT & filters.Regex(r"^🎵 My MP3$"), my_mp3)
mymp4_handler = MessageHandler(filters.TEXT & filters.Regex(r"^🎬 My MP4$"), my_mp4)
mp3_actions_handler = CallbackQueryHandler(mp3_actions, pattern=r"^mp3_\d+$")
mp3_to_lyrics_handler = CallbackQueryHandler(mp3_to_lyrics, pattern=r"^mp3lyrics_\d+$")
mp3_video_prompt_handler = CallbackQueryHandler(mp3_video_prompt, pattern=r"^mp3video_\d+$")
play_mp3_handler = CallbackQueryHandler(play_mp3, pattern=r"^play_\d+$")
watch_video_handler = CallbackQueryHandler(watch_video, pattern=r"^watchvid_\d+$")
add_subtitle_handler = CallbackQueryHandler(add_subtitle_to_video, pattern=r"^vidsub(_(yes|no))?_\d+$")
mylyrics_handler = MessageHandler(filters.TEXT & filters.Regex(r"^📝 My Lyrics$"), my_lyrics)
lyrics_detail_handler = CallbackQueryHandler(lyrics_detail, pattern=r"^lyr_\d+$")
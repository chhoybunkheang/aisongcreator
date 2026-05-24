import asyncio
import json
import os
import re
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

from app.config.settings import (
    BOT_USERNAME_LABEL,
    GENERATED_COVERS_DIR,
    GENERATED_VIDEOS_DIR,
)
from app.database.queries import (
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
from app.services.image_service import generate_cover_image
from app.services.music_service import generate_music, generate_music_remix
from app.services.openai_service import (
    generate_subtitle_timing,
    transcribe_lyrics_from_mp3,
    translate_lyrics,
)
from app.services.video_service import create_music_video, extract_audio_from_video
from app.utils.helpers import (
    clear_flow_message_tracking,
    make_progress_notifier,
    replace_flow_message,
    retry_telegram_call,
    send_audio_with_status,
    send_photo_with_status,
    send_video_with_status,
    start_timed_progress_message,
    stop_progress_message,
)

MP3_QUEUE_SECONDS = 50
COVER_QUEUE_SECONDS = 40
VIDEO_QUEUE_SECONDS = 120
VIDEO_WITH_SUBTITLES_QUEUE_SECONDS = 120
MP3_TO_LYRICS_QUEUE_SECONDS = 60
REMIX_QUEUE_SECONDS = 120


async def _safe_answer(query):
    """Answer a callback query, ignoring expired/invalid query errors."""
    try:
        await query.answer()
    except tg_error.BadRequest:
        pass  # query expired (>30s old) - safe to ignore


def _mp3_caption(title):
    return f"🎵 Title: {title}\nCreated by: {BOT_USERNAME_LABEL}"


def _video_caption(title, subtitles_enabled=False):
    suffix = " (with subtitles)" if subtitles_enabled else ""
    return (
        f"🎬 Title: {title}{suffix}\n"
        f"Created by: {BOT_USERNAME_LABEL}"
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


def _detect_language_from_lyrics(lyrics):
    text = (lyrics or "").strip()

    if any("\u1780" <= char <= "\u17ff" for char in text):
        return "Khmer"

    if any("\u3040" <= char <= "\u30ff" for char in text):
        return "Japanese"

    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "Chinese"

    vietnamese_markers = set("ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    if any(char.lower() in vietnamese_markers for char in text):
        return "Vietnamese"

    return "English"


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

    if any(token in lowered for token in ("khmer singing validation failed", "khmer music is not available right now", "khmer vocals are unavailable")):
        return (
            "❌ Khmer music is not available right now.\n\n"
            "We don't deduct your credit. Please try again later."
        )

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


def _video_subtitle_keyboard(song_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_yes_{song_id}"),
        InlineKeyboardButton("❌ No", callback_data=f"ms_vid_no_{song_id}"),
    ]])


def _cover_source_keyboard(song_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Upload Image", callback_data=f"ms_cov_upload_{song_id}")],
        [InlineKeyboardButton("🎞 Upload Video", callback_data=f"ms_cov_upload_video_{song_id}")],
        [InlineKeyboardButton("🎨 Use Generated Image", callback_data=f"ms_cov_use_{song_id}")],
    ])


def _has_visual_source(song):
    return bool(
        (song.cover_path and os.path.exists(song.cover_path))
        or (song.source_video_path and os.path.exists(song.source_video_path))
    )


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

    if song.lyrics:
        rows.append([
            InlineKeyboardButton("🔄 Remix Language", callback_data=f"remixlang_{song.id}"),
        ])

    return InlineKeyboardMarkup(rows)


# -----------------------------------
# HELPER: build keyboard for next step
# -----------------------------------
def _next_step_markup(song):

    def _friendly_mp3_error_message(error):
        error_text = str(error or "").strip()
        lowered = error_text.lower()

        if any(token in lowered for token in ("khmer singing validation failed", "khmer music is not available right now", "khmer vocals are unavailable")):
            return (
                "❌ Khmer music is not available right now.\n\n"
                "We don't deduct your credit. Please try again later."
            )

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
    if not _has_visual_source(song):
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
        if song.lyrics or song.mp3_path or song.cover_path or song.source_video_path or song.video_path
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
    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    credit_reserved = deduct_credit(query.from_user.id)
    if not credit_reserved:
        await query.edit_message_text(
            "❌ You don't have enough credits.\n\n"
            "💎 Please buy more credits."
        )
        return

    await query.edit_message_text("Generating MP3...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "Generating MP3...\nPreparing request...",
        start_percent=1,
        max_percent=95,
        total_seconds=MP3_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        detected_language = _detect_language_from_lyrics(song.lyrics or "")
        mp3_file = await asyncio.to_thread(
            generate_music,
            style=song.style,
            topic=song.topic,
            mood=song.mood,
            lyrics=song.lyrics,
            language=detected_language or song.language or "",
            progress_callback=progress_callback,
        )
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
                caption=_mp3_caption(song.topic),
                status_message=query.message,
                upload_text="MP3 ready. Uploading to Telegram...",
                complete_text="MP3 uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "MP3 uploaded 100%"
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
        if credit_reserved:
            refund_credit(query.from_user.id)
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
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "Generating cover image...\nPreparing request...",
        start_percent=1,
        max_percent=95,
        total_seconds=COVER_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        cover_image = await asyncio.to_thread(
            generate_cover_image,
            topic=song.topic,
            mood=song.mood,
            style=song.style,
            description=song.description,
            lyrics=song.lyrics,
            language=song.language,
            progress_callback=progress_callback,
        )
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

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Cover uploaded 100%"
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

    if not song.mp3_path or not _has_visual_source(song):
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="MP3 and a cover image or source video are required before generating a video."
        )
        return

    context.user_data["ms_video_animation_style"] = "none"

    await query.edit_message_text(
        (
            "Step 1 of 1: Do you want to add subtitles to the uploaded video?\n\nSubtitles use extra credits."
            if song.source_video_path and os.path.exists(song.source_video_path)
            else "Step 1 of 1: Do you want to add subtitles to the video?\n\nSubtitles use extra credits."
        ),
        reply_markup=_video_subtitle_keyboard(song.id)
    )


async def _prompt_video_subtitles_for_song(query, context, song):
    context.user_data["ms_video_animation_style"] = "none"
    await replace_flow_message(
        context,
        context.bot.send_message,
        chat_id=query.message.chat_id,
        text=(
            "Step 1 of 1: Do you want to add subtitles to the uploaded video?\n\nSubtitles use extra credits."
            if song.source_video_path and os.path.exists(song.source_video_path)
            else "Step 1 of 1: Do you want to add subtitles to the video?\n\nSubtitles use extra credits."
        ),
        reply_markup=_video_subtitle_keyboard(song.id),
        state_key="mysongs_flow_message_id",
    )


async def ms_use_generated_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[3])
    song = get_song_by_id(song_id)

    if not song:
        await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
        return

    if song.cover_path and os.path.exists(song.cover_path):
        await _prompt_video_subtitles_for_song(query, context, song)
        return

    await query.edit_message_text("Generating image...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "Generating image...\nPreparing request...",
        start_percent=1,
        max_percent=95,
        total_seconds=COVER_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        cover_image = await asyncio.to_thread(
            generate_cover_image,
            topic=song.topic,
            mood=song.mood,
            style=song.style,
            description=song.description,
            lyrics=song.lyrics,
            language=song.language,
            progress_callback=progress_callback,
        )
        update_song_cover(song_id, cover_image)
        update_song_source_video(song_id, None)

        await stop_progress_message(progress_task, progress_stop)

        refreshed_song = get_song_by_id(song_id)
        if not refreshed_song:
            await context.bot.send_message(chat_id=query.message.chat_id, text="Song not found.")
            return

        await _prompt_video_subtitles_for_song(query, context, refreshed_song)
    except Exception as e:
        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Image generation failed"
        )
        error_msg = f"Error generating image:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


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

    if not song.mp3_path or not _has_visual_source(song):
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="MP3 and a cover image or source video are required before generating a video."
        )
        return

    if choice == "yes" and (not user or user.credits <= 10):
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ You need more than 10 credits to create a video with subtitles.\n\n"
                "💎 Please add credits or create the video without subtitles."
            )
        )
        return

    subtitles_enabled = choice == "yes"
    subtitle_credit_reserved = False
    if subtitles_enabled:
        subtitle_credit_reserved = deduct_credit(query.from_user.id, minimum_credits=11)
        if not subtitle_credit_reserved:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "❌ You need more than 10 credits to create a video with subtitles.\n\n"
                    "💎 Please add credits or create the video without subtitles."
                )
            )
            return

    animation_style = context.user_data.pop("ms_video_animation_style", "none")
    await query.edit_message_text(
        "Generating subtitles...\nPreparing request..."
        if subtitles_enabled and song.lyrics and song.mp3_path
        else "Creating music video...\nPreparing render..."
    )
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "Generating subtitles...\nPreparing request..."
        if subtitles_enabled and song.lyrics and song.mp3_path
        else "Creating music video...\nPreparing render...",
        start_percent=1,
        max_percent=95,
        total_seconds=VIDEO_WITH_SUBTITLES_QUEUE_SECONDS if subtitles_enabled else VIDEO_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), query.message)

    try:
        safe_topic = "_".join(song.topic.split())
        os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)
        video_path = os.path.join(GENERATED_VIDEOS_DIR, f"{song_id}_{safe_topic}.mp4")
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
            animation_style=animation_style,
            lyrics=song.lyrics,
            subtitle_timing=subtitle_timing,
            subtitles_enabled=subtitles_enabled,
            progress_callback=progress_callback,
            source_video_path=song.source_video_path,
        )
        update_song_video(song_id, video_path)

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption=_video_caption(song.topic, subtitles_enabled=subtitles_enabled),
                status_message=query.message,
                upload_text="Video ready. Uploading to Telegram...",
                complete_text="Video uploaded",
                read_timeout=300,
                write_timeout=300,
            )

        await stop_progress_message(
            progress_task,
            progress_stop,
            query.message,
            "Video uploaded 100%"
        )

        if subtitles_enabled:
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
        if subtitle_credit_reserved:
            refund_credit(query.from_user.id)
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

    cover_missing = not _has_visual_source(song)
    message = f"\"{song.topic}\" has not been converted to video yet."
    if cover_missing:
        message += " Choose Create Video to add a cover image or upload a source video first, then continue to video creation."
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

    if not _has_visual_source(song):
        await query.edit_message_text(
            f"\"{song.topic}\" needs a cover image or source video before video creation. Choose visual source type:",
            reply_markup=_cover_source_keyboard(song.id)
        )
        return

    await query.edit_message_text(
        f"The cover image for \"{song.topic}\" is ready.\n\n"
        "Next you can choose whether to add subtitles.\n\n"
        "Create the music video now?",
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
            caption=_mp3_caption(song.topic),
            read_timeout=300,
            write_timeout=300,
        )


async def ms_upload_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    parts = query.data.split("_")
    upload_mode = "video" if parts[3] == "video" else "image"
    song_id = int(parts[4] if upload_mode == "video" else parts[3])
    context.user_data["ms_cover_song_id"] = song_id
    context.user_data["ms_cover_upload_mode"] = upload_mode

    if upload_mode == "video":
        await query.edit_message_text("🎞 Please upload one video to use as your music video source.")
        return

    await query.edit_message_text("🖼 Please upload one image to use as your cover.")


async def ms_receive_uploaded_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    song_id = context.user_data.get("ms_cover_song_id")
    upload_mode = context.user_data.get("ms_cover_upload_mode")

    if not song_id:
        return

    song = get_song_by_id(song_id)
    if not song:
        await retry_telegram_call(update.message.reply_text, "Song not found.")
        context.user_data.pop("ms_cover_song_id", None)
        return

    if update.message.photo:
        if upload_mode == "video":
            await retry_telegram_call(update.message.reply_text, "❌ Please upload a video.")
            return

        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)

        os.makedirs(GENERATED_COVERS_DIR, exist_ok=True)
        cover_path = os.path.join(
            GENERATED_COVERS_DIR,
            f"upload_{update.effective_user.id}_{uuid.uuid4().hex}.jpg"
        )
        await telegram_file.download_to_drive(cover_path)

        update_song_cover(song_id, cover_path)
        update_song_source_video(song_id, None)
        context.user_data.pop("ms_cover_song_id", None)
        context.user_data.pop("ms_cover_upload_mode", None)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Cover image uploaded for \"{song.topic}\". Create the music video now?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_prompt_{song.id}"),
                InlineKeyboardButton("❌ No", callback_data=f"ms_skip_{song.id}"),
            ]])
        )
        clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
        return

    video_message = update.message.video
    document_message = update.message.document
    if video_message or document_message:
        if upload_mode == "image":
            await retry_telegram_call(update.message.reply_text, "❌ Please upload an image.")
            return

        file_id = video_message.file_id if video_message else document_message.file_id
        telegram_file = await context.bot.get_file(file_id)

        os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)
        source_video_path = os.path.join(
            GENERATED_VIDEOS_DIR,
            f"source_{update.effective_user.id}_{uuid.uuid4().hex}.mp4"
        )
        await telegram_file.download_to_drive(source_video_path)

        update_song_source_video(song_id, source_video_path)
        update_song_cover(song_id, None)
        context.user_data.pop("ms_cover_song_id", None)
        context.user_data.pop("ms_cover_upload_mode", None)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Source video uploaded for \"{song.topic}\". Create the music video now?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Yes", callback_data=f"ms_vid_prompt_{song.id}"),
                InlineKeyboardButton("❌ No", callback_data=f"ms_skip_{song.id}"),
            ]])
        )
        clear_flow_message_tracking(context, state_key="mysongs_flow_message_id")
        return

    await retry_telegram_call(update.message.reply_text, "❌ Please upload an image or a video.")


async def mp3_to_lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    song_id = int(query.data.split("_")[1])
    song = get_song_by_id(song_id)

    if not song or not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    await query.edit_message_text("⏳ Recovering lyrics from MP3...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "⏳ Recovering lyrics from MP3...\nPreparing request...",
        start_percent=1,
        max_percent=100,
        total_seconds=MP3_TO_LYRICS_QUEUE_SECONDS,
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
            caption=_video_caption(song.topic, subtitles_enabled=bool(song.subtitle_timing)),
            read_timeout=300,
            write_timeout=300,
        )

    has_lyrics = bool((song.lyrics or "").strip())
    if has_lyrics and song.mp3_path and os.path.exists(song.mp3_path) and _has_visual_source(song):
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
                caption=_video_caption(song.topic, subtitles_enabled=bool(song.subtitle_timing)),
                read_timeout=300,
                write_timeout=300,
            )
        return

    if not song.mp3_path or not os.path.exists(song.mp3_path):
        await context.bot.send_message(chat_id=query.message.chat_id, text="MP3 file not found.")
        return

    if not _has_visual_source(song):
        await context.bot.send_message(chat_id=query.message.chat_id, text="Cover image or source video not found.")
        return

    if not (song.lyrics or "").strip():
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="This video has no lyrics, so subtitles cannot be added."
        )
        return

    if not user or user.credits <= 10:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ You need more than 10 credits to add or update subtitles.\n\n"
                "💎 Please add credit."
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Add Credits", callback_data="buycredits_menu"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"watchvid_{song.id}"),
            ]]),
        )
        return

    progress_message = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            "Updating subtitles...\nPreparing request..."
            if song.subtitle_timing else "Adding subtitles...\nPreparing request..."
        )
    )
    progress_task, progress_stop = await start_timed_progress_message(
        progress_message,
        (
            "Updating subtitles...\nPreparing request..."
            if song.subtitle_timing else "Adding subtitles...\nPreparing request..."
        ),
        start_percent=1,
        max_percent=95,
        total_seconds=VIDEO_WITH_SUBTITLES_QUEUE_SECONDS,
    )
    progress_callback = make_progress_notifier(asyncio.get_running_loop(), progress_message)
    subtitle_credit_reserved = deduct_credit(query.from_user.id, minimum_credits=11)
    if not subtitle_credit_reserved:
        await stop_progress_message(progress_task, progress_stop, progress_message, "Adding subtitles failed")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ You need more than 10 credits to add or update subtitles.\n\n"
                "💎 Please add credit."
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Add Credits", callback_data="buycredits_menu"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"watchvid_{song.id}"),
            ]]),
        )
        return

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
        os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)
        video_path = os.path.join(GENERATED_VIDEOS_DIR, f"{song_id}_{safe_topic}.mp4")

        await asyncio.to_thread(
            create_music_video,
            audio_path=song.mp3_path,
            image_path=song.cover_path,
            output_path=video_path,
            animation_style="none",
            lyrics=song.lyrics,
            subtitle_timing=subtitle_timing,
            subtitles_enabled=True,
            progress_callback=progress_callback,
            source_video_path=song.source_video_path,
        )
        update_song_video(song_id, video_path)
        await stop_progress_message(
            progress_task,
            progress_stop,
            progress_message,
            "Subtitled video uploaded 100%"
        )

        with open(video_path, "rb") as video:
            await send_video_with_status(
                context.bot,
                chat_id=query.message.chat_id,
                video=video,
                caption=_video_caption(song.topic, subtitles_enabled=True),
                status_message=progress_message,
                upload_text="Subtitled video ready. Uploading to Telegram...",
                complete_text="Subtitled video uploaded",
                read_timeout=300,
                write_timeout=300,
            )
        user = get_user(query.from_user.id)
        if user:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Subtitles updated.\n\n💎 Remaining Credits: {user.credits}"
            )

    except Exception as e:
        if subtitle_credit_reserved:
            refund_credit(query.from_user.id)
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


# -----------------------------------
# REMIX LANGUAGE HANDLERS
# -----------------------------------

async def _show_remix_language_picker(bot, chat_id, song_id):
    """Show the target language selection keyboard."""
    languages = get_enabled_song_languages()
    buttons = []
    for i in range(0, len(languages), 2):
        row = [InlineKeyboardButton(languages[i], callback_data=f"remixgen_{song_id}_{languages[i]}")]
        if i + 1 < len(languages):
            row.append(InlineKeyboardButton(languages[i + 1], callback_data=f"remixgen_{song_id}_{languages[i + 1]}"))
        buttons.append(row)
    await bot.send_message(
        chat_id=chat_id,
        text="🌍 *Choose the target language for the remix:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def ms_remix_pick_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """First step: ask whether to use library or upload an MP3."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[1])

    song = get_song_by_id(song_id)
    if not song or song.user_id != query.from_user.id:
        await query.message.reply_text("Song not found.")
        return

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 From My Library", callback_data=f"remsrc_lib_{song_id}"),
        ],
        [
            InlineKeyboardButton("📤 Upload MP3/Video", callback_data=f"remsrc_up_{song_id}"),
            InlineKeyboardButton("🔗 YouTube Link", callback_data=f"remsrc_yt_{song_id}"),
        ],
    ])
    await query.message.reply_text(
        "🔄 *Remix in Another Language*\n\nChoose the style reference source:",
        parse_mode="Markdown",
        reply_markup=markup,
    )


async def ms_remix_src_lib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user's saved MP3 songs as reference choices."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[2])

    songs = get_user_songs(query.from_user.id)
    mp3_songs = [s for s in songs if s.mp3_path and os.path.exists(str(s.mp3_path))]

    if not mp3_songs:
        await query.message.reply_text("You have no saved MP3 songs to pick from.")
        return

    buttons = []
    for s in mp3_songs[:20]:
        label = (s.topic or f"Song #{s.id}")[:30]
        buttons.append([InlineKeyboardButton(f"🎵 {label}", callback_data=f"remsrc_sel_{s.id}_{song_id}")])

    await query.message.reply_text(
        "📚 *Pick a reference MP3:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def ms_remix_src_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a library song as reference — store its path and show language picker."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")  # remsrc_sel_{ref_id}_{song_id}
    ref_id = int(parts[2])
    song_id = int(parts[3])

    ref_song = get_song_by_id(ref_id)
    if not ref_song or not ref_song.mp3_path or not os.path.exists(str(ref_song.mp3_path)):
        await query.message.reply_text("Reference MP3 not found.")
        return

    context.user_data[f"remix_ref_{song_id}"] = str(ref_song.mp3_path)
    await _show_remix_language_picker(context.bot, query.message.chat_id, song_id)


def _is_youtube_url(text: str) -> bool:
    """Return True if text looks like a YouTube video URL."""
    text = (text or "").strip()
    return bool(re.match(
        r"^https?://(www\.)?(youtube\.com/watch|youtu\.be/|youtube\.com/shorts/)",
        text,
        re.IGNORECASE,
    ))


def _download_yt_audio(url: str, dest_base: str) -> str:
    """Download audio from a YouTube URL and save as MP3. Returns the mp3 path."""
    import yt_dlp  # lazy import — only needed for this feature
    from imageio_ffmpeg import get_ffmpeg_exe

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": dest_base + ".%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "ffmpeg_location": get_ffmpeg_exe(),
        "extractor_args": {"youtube": {"player_client": ["ios", "android"]}},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return dest_base + ".mp3"


async def ms_remix_src_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User chose to upload an MP3/video — set awaiting state and prompt."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[2])

    context.user_data["awaiting_remix_upload"] = song_id
    await query.message.reply_text(
        "📤 Please send your MP3 file or video now.\n\n"
        "_Send it as a file (Document), audio message, or video._",
        parse_mode="Markdown",
    )


async def ms_remix_src_yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User chose YouTube link — set awaiting state and prompt for URL."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[2])

    context.user_data["awaiting_remix_upload"] = song_id
    await query.message.reply_text(
        "🔗 Please paste a YouTube link:\n\n"
        "_e.g. https://youtu.be/abc123_",
        parse_mode="Markdown",
    )


async def _process_remix_ref_mp3(message, context, dest, song_id):
    """Shared logic after an MP3 (or extracted audio) is ready for remixing."""
    del context.user_data["awaiting_remix_upload"]

    # "new" mode — transcribe lyrics from the uploaded audio
    if song_id == "new":
        prog_msg = await message.reply_text("⏳ Transcribing lyrics from your audio...\nPreparing request...")
        progress_task, progress_stop = await start_timed_progress_message(
            prog_msg,
            "⏳ Transcribing lyrics from your audio...",
            start_percent=1,
            max_percent=95,
            total_seconds=60,
        )
        try:
            notifier = make_progress_notifier(asyncio.get_running_loop(), prog_msg)
            lyrics = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: transcribe_lyrics_from_mp3(dest, language="", progress_callback=notifier),
            )
            await stop_progress_message(progress_task, progress_stop, prog_msg, "✅ Lyrics ready!")
        except Exception as e:
            await stop_progress_message(progress_task, progress_stop, prog_msg, "❌ Transcription failed")
            await message.reply_text(f"❌ Could not transcribe lyrics: {e}")
            return

        context.user_data["remix_ext_ref"] = dest
        context.user_data["remix_ext_lyrics"] = lyrics
        await _show_remix_ext_language_picker(context.bot, message.chat_id)
        return

    # Normal mode — ref is tied to a specific song
    context.user_data[f"remix_ref_{song_id}"] = dest
    await _show_remix_language_picker(context.bot, message.chat_id, song_id)


async def ms_receive_remix_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a YouTube URL as remix reference audio."""
    song_id = context.user_data.get("awaiting_remix_upload")
    if not song_id:
        return

    text = (update.message.text or "").strip()
    if not _is_youtube_url(text):
        return  # let other handlers deal with it

    message = update.message
    status_msg = await message.reply_text("⬇️ Downloading audio from YouTube…")
    try:
        upload_dir = os.path.join("temp", "remix_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        dest_base = os.path.join(upload_dir, f"yt_{update.effective_user.id}_{song_id}")
        dest = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _download_yt_audio(text, dest_base)
        )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to download from YouTube: {e}")
        return

    await _process_remix_ref_mp3(message, context, dest, song_id)


async def ms_receive_remix_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive an uploaded MP3 and route to remix flow."""
    song_id = context.user_data.get("awaiting_remix_upload")
    if not song_id:
        return

    message = update.message
    file_obj = None
    if message.audio:
        file_obj = message.audio
    elif message.document and message.document.mime_type in (
        "audio/mpeg", "audio/mp3", "audio/x-mp3"
    ):
        file_obj = message.document

    if not file_obj:
        await message.reply_text("Please send an MP3 audio file.")
        return

    MAX_BYTES = 20 * 1024 * 1024
    file_size = getattr(file_obj, "file_size", None)
    if file_size and file_size > MAX_BYTES:
        await message.reply_text(
            "⚠️ That file is too large (Telegram allows bots to download up to 20 MB).\n"
            "Please send a smaller MP3."
        )
        return

    status_msg = await message.reply_text("⬇️ Downloading audio…")
    try:
        tg_file = await file_obj.get_file()
        upload_dir = os.path.join("temp", "remix_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        dest = os.path.join(upload_dir, f"upload_{update.effective_user.id}_{song_id}.mp3")
        await tg_file.download_to_drive(dest)
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to download the file: {e}")
        return

    await _process_remix_ref_mp3(message, context, dest, song_id)


async def ms_receive_remix_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive an uploaded video, extract its audio, and route to remix flow."""
    song_id = context.user_data.get("awaiting_remix_upload")
    if not song_id:
        return

    message = update.message
    file_obj = None
    if message.video:
        file_obj = message.video
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        file_obj = message.document

    if not file_obj:
        return

    # Telegram bots cannot download files larger than 20 MB
    MAX_BYTES = 20 * 1024 * 1024
    file_size = getattr(file_obj, "file_size", None)
    if file_size and file_size > MAX_BYTES:
        await message.reply_text(
            "⚠️ That video is too large (Telegram allows bots to download up to 20 MB).\n"
            "Please trim the video or extract the audio as an MP3 and send that instead."
        )
        return

    status_msg = await message.reply_text("⬇️ Downloading video…")
    try:
        tg_file = await file_obj.get_file()
        upload_dir = os.path.join("temp", "remix_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        vid_dest = os.path.join(upload_dir, f"upload_{update.effective_user.id}_{song_id}.mp4")
        await tg_file.download_to_drive(vid_dest)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to download the video: {e}")
        return

    try:
        await status_msg.edit_text("🎬 Extracting audio from video…")
        mp3_dest = await asyncio.get_event_loop().run_in_executor(
            None, lambda: extract_audio_from_video(vid_dest)
        )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Could not extract audio from video: {e}")
        return

    await _process_remix_ref_mp3(message, context, mp3_dest, song_id)


async def ms_remix_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a library song to use as its own reference (song's MP3 = reference)."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[1])  # remixself_{song_id}

    song = get_song_by_id(song_id)
    if not song or not song.mp3_path or not os.path.exists(str(song.mp3_path)):
        await query.message.reply_text("MP3 not found for that song.")
        return

    context.user_data[f"remix_ref_{song_id}"] = str(song.mp3_path)
    await _show_remix_language_picker(context.bot, query.message.chat_id, song_id)


async def ms_remix_lyrics_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a song for lyrics after uploading a reference MP3."""
    query = update.callback_query
    await query.answer()
    song_id = int(query.data.split("_")[1])  # remixlyrics_{song_id}

    uploaded_ref = context.user_data.pop("remix_uploaded_ref", None)
    if not uploaded_ref or not os.path.exists(uploaded_ref):
        await query.message.reply_text("Uploaded MP3 not found. Please start over.")
        return

    context.user_data[f"remix_ref_{song_id}"] = uploaded_ref
    await _show_remix_language_picker(context.bot, query.message.chat_id, song_id)


async def _show_remix_ext_language_picker(bot, chat_id):
    """Language picker for external-upload remix (no song_id — uses remixextgen_ callbacks)."""
    languages = get_enabled_song_languages()
    buttons = []
    for i in range(0, len(languages), 2):
        row = [InlineKeyboardButton(languages[i], callback_data=f"remixextgen_{languages[i]}")]
        if i + 1 < len(languages):
            row.append(InlineKeyboardButton(languages[i + 1], callback_data=f"remixextgen_{languages[i + 1]}"))
        buttons.append(row)
    await bot.send_message(
        chat_id=chat_id,
        text="🌍 *Choose the target language for the remix:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def ms_remix_ext_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate remix for an externally uploaded MP3 whose lyrics were auto-transcribed."""
    query = update.callback_query
    await query.answer()

    target_language = query.data[len("remixextgen_"):]  # remixextgen_{language}

    ref_mp3 = context.user_data.get("remix_ext_ref")
    source_lyrics = context.user_data.get("remix_ext_lyrics")

    if not ref_mp3 or not os.path.exists(ref_mp3) or not source_lyrics:
        await query.message.reply_text("Session data lost. Please upload the MP3 again.")
        return

    credit_reserved = deduct_credit(query.from_user.id)
    if not credit_reserved:
        await query.message.reply_text(
            "❌ Not enough credits to remix.\nUse /buycredits to purchase more."
        )
        return

    await query.edit_message_text("⏳ Remixing song...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "⏳ Remixing song...",
        start_percent=1,
        max_percent=95,
        total_seconds=REMIX_QUEUE_SECONDS,
    )
    try:
        notifier = make_progress_notifier(asyncio.get_running_loop(), query.message)

        # Translate transcribed lyrics to target language
        translated_lyrics = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: translate_lyrics(
                source_lyrics, "auto", target_language,
                progress_callback=notifier,
            ),
        )

        # audio2audio generation
        mp3_path = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_music_remix(
                ref_mp3,
                "",
                translated_lyrics,
                language=target_language,
                singer_gender="female",
                progress_callback=notifier,
            ),
        )

        await stop_progress_message(progress_task, progress_stop, query.message, "✅ Remix complete!")

        # Save new song entry
        user = get_user(query.from_user.id)
        new_song = save_song(
            telegram_id=query.from_user.id,
            style="",
            topic="Remix",
            mood="",
            description="",
            language=target_language,
            lyrics=translated_lyrics,
        )
        if new_song:
            update_song_mp3(new_song.id, mp3_path)

        context.user_data.pop("remix_ext_ref", None)
        context.user_data.pop("remix_ext_lyrics", None)

        await context.bot.send_audio(
            chat_id=query.message.chat_id,
            audio=open(mp3_path, "rb"),
            title=f"Remix ({target_language})",
            caption=f"🔄 Remix in *{target_language}*\n\n💎 Remaining Credits: {user.credits if user else '?'}",
            parse_mode="Markdown",
        )

    except Exception as e:
        refund_credit(query.from_user.id)
        await stop_progress_message(progress_task, progress_stop, query.message, "Remix failed")
        error_msg = f"❌ Remix failed:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


async def ms_remix_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Translate lyrics + generate audio2audio remix in selected language."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 2)  # remixgen_{song_id}_{language}
    song_id = int(parts[1])
    target_language = parts[2]

    song = get_song_by_id(song_id)
    if not song or song.user_id != query.from_user.id:
        await query.message.reply_text("Song not found.")
        return

    ref_mp3 = context.user_data.get(f"remix_ref_{song_id}")
    if not ref_mp3 or not os.path.exists(ref_mp3):
        await query.message.reply_text("Reference MP3 not found. Please start over.")
        return

    credit_reserved = deduct_credit(query.from_user.id)
    if not credit_reserved:
        await query.message.reply_text(
            "❌ Not enough credits to remix.\nUse /buycredits to purchase more."
        )
        return

    await query.edit_message_text("⏳ Remixing song...\nPreparing request...")
    progress_task, progress_stop = await start_timed_progress_message(
        query.message,
        "⏳ Remixing song...",
        start_percent=1,
        max_percent=95,
        total_seconds=REMIX_QUEUE_SECONDS,
    )
    try:
        notifier = make_progress_notifier(asyncio.get_running_loop(), query.message)

        # Step 1 — translate lyrics
        source_language = song.language or "English"
        translated_lyrics = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: translate_lyrics(
                song.lyrics, source_language, target_language,
                progress_callback=notifier,
            ),
        )

        # Step 2 — audio2audio generation
        style_prompt = f"{song.style or ''}, {song.mood or ''}".strip(", ")
        mp3_path = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_music_remix(
                ref_mp3,
                style_prompt,
                translated_lyrics,
                language=target_language,
                singer_gender="female",
                progress_callback=notifier,
            ),
        )

        await stop_progress_message(progress_task, progress_stop, query.message, "✅ Remix complete!")

        # Save new song entry
        user = get_user(query.from_user.id)
        new_song = save_song(
            telegram_id=query.from_user.id,
            style=song.style,
            topic=song.topic,
            mood=song.mood,
            description=song.description,
            language=target_language,
            lyrics=translated_lyrics,
        )
        if new_song:
            update_song_mp3(new_song.id, mp3_path)

        context.user_data.pop(f"remix_ref_{song_id}", None)

        await context.bot.send_audio(
            chat_id=query.message.chat_id,
            audio=open(mp3_path, "rb"),
            title=f"{song.topic or 'Remix'} ({target_language})",
            caption=f"🔄 Remix in *{target_language}*\n\n💎 Remaining Credits: {user.credits if user else '?'}",
            parse_mode="Markdown",
        )

    except Exception as e:
        refund_credit(query.from_user.id)
        await stop_progress_message(progress_task, progress_stop, query.message, "Remix failed")
        error_msg = f"❌ Remix failed:\n{str(e)}"
        if len(error_msg) > 4096:
            error_msg = error_msg[:4090] + "..."
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_msg)


song_detail_handler = CallbackQueryHandler(
    song_detail,
    pattern=r"^song_\d+$"
)

ms_mp3_handler = CallbackQueryHandler(ms_gen_mp3, pattern=r"^ms_mp3_\d+$")
ms_cov_handler = CallbackQueryHandler(ms_gen_cover, pattern=r"^ms_cov_\d+$")
ms_cov_use_handler = CallbackQueryHandler(ms_use_generated_cover, pattern=r"^ms_cov_use_\d+$")
ms_cov_upload_handler = CallbackQueryHandler(ms_upload_cover, pattern=r"^ms_cov_upload(_video)?_\d+$")
ms_vid_handler = CallbackQueryHandler(ms_prompt_video_subtitles, pattern=r"^ms_vid_prompt_\d+$")
ms_vid_choice_handler = CallbackQueryHandler(ms_gen_video, pattern=r"^ms_vid_(yes|no)_\d+$")
ms_skip_handler = CallbackQueryHandler(ms_skip, pattern=r"^ms_skip_\d+$")
ms_uploaded_cover_handler = MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.VIDEO, ms_receive_uploaded_cover)

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
ms_remix_lang_handler = CallbackQueryHandler(ms_remix_pick_language, pattern=r"^remixlang_\d+$")
ms_remix_src_lib_handler = CallbackQueryHandler(ms_remix_src_lib, pattern=r"^remsrc_lib_\d+$")
ms_remix_src_sel_handler = CallbackQueryHandler(ms_remix_src_sel, pattern=r"^remsrc_sel_\d+_\d+$")
ms_remix_src_up_handler = CallbackQueryHandler(ms_remix_src_up, pattern=r"^remsrc_up_\d+$")
ms_remix_src_yt_handler = CallbackQueryHandler(ms_remix_src_yt, pattern=r"^remsrc_yt_\d+$")
ms_remix_audio_handler = MessageHandler(filters.AUDIO | filters.Document.MimeType("audio/mpeg"), ms_receive_remix_audio)
ms_remix_url_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, ms_receive_remix_url)
ms_remix_self_handler = CallbackQueryHandler(ms_remix_self, pattern=r"^remixself_\d+$")
ms_remix_lyrics_handler = CallbackQueryHandler(ms_remix_lyrics_pick, pattern=r"^remixlyrics_\d+$")
ms_remix_gen_handler = CallbackQueryHandler(ms_remix_generate, pattern=r"^remixgen_\d+_.+$")
ms_remix_ext_gen_handler = CallbackQueryHandler(ms_remix_ext_generate, pattern=r"^remixextgen_.+$")
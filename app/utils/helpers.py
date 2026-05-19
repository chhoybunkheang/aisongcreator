import asyncio
from contextlib import suppress

from telegram import error as tg_error
from telegram.constants import ChatAction


async def _safe_edit_progress(message, text):
	try:
		await message.edit_text(text)
	except tg_error.BadRequest as exc:
		if "Message is not modified" not in str(exc):
			pass


async def _progress_worker(
	message,
	label,
	stop_event,
	start_percent=5,
	step=5,
	max_percent=95,
	delay=2,
):
	percent = start_percent
	await _safe_edit_progress(message, f"{label} {percent}%")

	while not stop_event.is_set():
		await asyncio.sleep(delay)
		if stop_event.is_set():
			break

		if percent < max_percent:
			percent = min(percent + step, max_percent)
			await _safe_edit_progress(message, f"{label} {percent}%")


async def start_progress_message(message, label):
	stop_event = asyncio.Event()
	task = asyncio.create_task(_progress_worker(message, label, stop_event))
	return task, stop_event


async def stop_progress_message(task, stop_event, message=None, final_text=None):
	stop_event.set()
	task.cancel()
	with suppress(asyncio.CancelledError):
		await task

	if message is not None and final_text:
		await _safe_edit_progress(message, final_text)

async def retry_telegram_call(callback, *args, retries=3, delay=2, **kwargs):
	last_error = None

	for attempt in range(retries):
		try:
			return await callback(*args, **kwargs)
		except (tg_error.NetworkError, tg_error.TimedOut) as exc:
			last_error = exc
			if attempt == retries - 1:
				raise

			await asyncio.sleep(delay)

	raise last_error


async def send_video_with_status(
	bot,
	chat_id,
	video,
	caption=None,
	status_message=None,
	upload_text=None,
	complete_text=None,
	**kwargs,
):
	if status_message is not None and upload_text:
		await _safe_edit_progress(status_message, upload_text)

	await retry_telegram_call(
		bot.send_chat_action,
		chat_id=chat_id,
		action=ChatAction.UPLOAD_VIDEO,
	)

	sent_message = await retry_telegram_call(
		bot.send_video,
		chat_id=chat_id,
		video=video,
		caption=caption,
		**kwargs,
	)

	if status_message is not None and complete_text:
		await _safe_edit_progress(status_message, complete_text)

	return sent_message


async def send_audio_with_status(
	bot,
	chat_id,
	audio,
	title=None,
	caption=None,
	status_message=None,
	upload_text=None,
	complete_text=None,
	**kwargs,
):
	if status_message is not None and upload_text:
		await _safe_edit_progress(status_message, upload_text)

	await retry_telegram_call(
		bot.send_chat_action,
		chat_id=chat_id,
		action=ChatAction.UPLOAD_VOICE,
	)

	sent_message = await retry_telegram_call(
		bot.send_audio,
		chat_id=chat_id,
		audio=audio,
		title=title,
		caption=caption,
		**kwargs,
	)

	if status_message is not None and complete_text:
		await _safe_edit_progress(status_message, complete_text)

	return sent_message


async def send_photo_with_status(
	bot,
	chat_id,
	photo,
	caption=None,
	status_message=None,
	upload_text=None,
	complete_text=None,
	**kwargs,
):
	if status_message is not None and upload_text:
		await _safe_edit_progress(status_message, upload_text)

	await retry_telegram_call(
		bot.send_chat_action,
		chat_id=chat_id,
		action=ChatAction.UPLOAD_PHOTO,
	)

	sent_message = await retry_telegram_call(
		bot.send_photo,
		chat_id=chat_id,
		photo=photo,
		caption=caption,
		**kwargs,
	)

	if status_message is not None and complete_text:
		await _safe_edit_progress(status_message, complete_text)

	return sent_message

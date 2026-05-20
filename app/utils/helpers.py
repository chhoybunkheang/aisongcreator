import asyncio
import os
from contextlib import suppress

from telegram import error as tg_error
from telegram.constants import ChatAction

FLOW_MESSAGE_STATE_KEYS = (
	"active_flow_message_id",
	"song_flow_message_id",
	"settings_flow_message_id",
	"mysongs_flow_message_id",
	"start_flow_message_id",
	"buycredits_flow_message_id",
)

SLOW_PROGRESS_SECONDS = 45
SLOW_PROGRESS_SUFFIX = "\n\n⌛ This is taking longer than usual. The bot is still working."
_progress_trackers = {}
_progress_trackers_by_stop_event = {}


def _tracked_message_parts(tracked_value, fallback_chat_id=None):
	if isinstance(tracked_value, dict):
		return tracked_value.get("chat_id", fallback_chat_id), tracked_value.get("message_id")

	return fallback_chat_id, tracked_value


async def safe_delete_message(message):
	if message is None:
		return

	try:
		await message.delete()
	except tg_error.BadRequest:
		pass


async def clear_tracked_flow_messages(context, keep_state_key=None, chat_id=None):
	chat_data = context.chat_data if context.chat_data is not None else None
	if not chat_data:
		return

	bot = getattr(context, "bot", None)
	if bot is None:
		return

	for state_key in FLOW_MESSAGE_STATE_KEYS:
		if state_key == keep_state_key or state_key == "start_flow_message_id":
			continue

		tracked_value = chat_data.pop(state_key, None)
		tracked_chat_id, message_id = _tracked_message_parts(tracked_value, chat_id)
		if message_id is None:
			continue
		if tracked_chat_id is None:
			continue

		with suppress(tg_error.BadRequest, tg_error.Forbidden):
			await bot.delete_message(chat_id=tracked_chat_id, message_id=message_id)


async def replace_flow_message(context, send_callback, *args, state_key="active_flow_message_id", **kwargs):
	chat_data = context.chat_data if context.chat_data is not None else {}
	bot = getattr(context, "bot", None)
	chat_id = kwargs.get("chat_id")

	await clear_tracked_flow_messages(context, keep_state_key=state_key, chat_id=chat_id)
	previous_chat_id, previous_message_id = _tracked_message_parts(chat_data.get(state_key), chat_id)

	if previous_message_id is not None:
		if bot is not None and previous_chat_id is not None:
			with suppress(tg_error.BadRequest, tg_error.Forbidden):
				await bot.delete_message(chat_id=previous_chat_id, message_id=previous_message_id)

	sent_message = await send_callback(*args, **kwargs)
	sent_chat = getattr(sent_message, "chat", None)
	sent_chat_id = getattr(sent_chat, "id", None)
	chat_data[state_key] = {
		"chat_id": sent_chat_id,
		"message_id": sent_message.message_id,
	}
	return sent_message


def clear_flow_message_tracking(context, state_key="active_flow_message_id"):
	if context.chat_data is None:
		return

	context.chat_data.pop(state_key, None)


async def _safe_edit_progress(message, text):
	try:
		await message.edit_text(text)
	except tg_error.BadRequest as exc:
		if "Message is not modified" not in str(exc):
			pass


def _tracker_key(message):
	return id(message)


def _render_progress_text(tracker, text):
	base_text = text or ""
	if tracker and tracker.get("show_percent"):
		current_percent = int(tracker.get("current_percent") or 0)
		percent_line = f"📊 Queue Status: {current_percent}%"
		base_text = f"{base_text}\n{percent_line}" if base_text else percent_line
	if tracker and tracker.get("slow_active"):
		if base_text.endswith(SLOW_PROGRESS_SUFFIX):
			return base_text
		return f"{base_text}{SLOW_PROGRESS_SUFFIX}"
	return base_text


async def _safe_edit_tracked_progress(message, text):
	tracker = _progress_trackers.get(_tracker_key(message))
	if tracker is not None:
		tracker["last_text"] = text
	await _safe_edit_progress(message, _render_progress_text(tracker, text))


async def _slow_progress_worker(message, stop_event, delay):
	await asyncio.sleep(delay)
	if stop_event.is_set():
		return

	tracker = _progress_trackers.get(_tracker_key(message))
	if not tracker:
		return

	tracker["slow_active"] = True
	await _safe_edit_progress(message, _render_progress_text(tracker, tracker.get("last_text") or ""))


async def update_progress_message(message, text):
	await _safe_edit_tracked_progress(message, text)


def make_progress_notifier(loop, message):
	def notify(text):
		loop.call_soon_threadsafe(asyncio.create_task, _safe_edit_tracked_progress(message, text))

	return notify


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
	await _safe_edit_tracked_progress(message, f"{label} {percent}%")

	while not stop_event.is_set():
		await asyncio.sleep(delay)
		if stop_event.is_set():
			break

		if percent < max_percent:
			percent = min(percent + step, max_percent)
			await _safe_edit_tracked_progress(message, f"{label} {percent}%")


async def _timed_progress_worker(
	message,
	stop_event,
	start_percent,
	max_percent,
	total_seconds,
):
	tracker = _progress_trackers.get(_tracker_key(message))
	if tracker is None:
		return

	tracker["current_percent"] = start_percent
	await _safe_edit_progress(message, _render_progress_text(tracker, tracker.get("last_text") or ""))

	percent_span = max(max_percent - start_percent, 0)
	if percent_span <= 0:
		return

	delay = max(float(total_seconds) / percent_span, 0.1)
	percent = start_percent

	while not stop_event.is_set() and percent < max_percent:
		await asyncio.sleep(delay)
		if stop_event.is_set():
			break

		percent += 1
		tracker = _progress_trackers.get(_tracker_key(message))
		if tracker is None:
			break

		tracker["current_percent"] = percent
		await _safe_edit_progress(message, _render_progress_text(tracker, tracker.get("last_text") or ""))


async def start_progress_message(message, label, auto_increment=True):
	stop_event = asyncio.Event()
	tracker = {
		"last_text": label,
		"slow_active": False,
		"show_percent": False,
		"current_percent": 0,
		"slow_task": asyncio.create_task(_slow_progress_worker(message, stop_event, SLOW_PROGRESS_SECONDS)),
	}
	_progress_trackers[_tracker_key(message)] = tracker
	_progress_trackers_by_stop_event[id(stop_event)] = (_tracker_key(message), tracker)
	if not auto_increment:
		await _safe_edit_tracked_progress(message, label)
		return None, stop_event

	task = asyncio.create_task(_progress_worker(message, label, stop_event))
	return task, stop_event


async def start_timed_progress_message(
	message,
	label,
	start_percent=1,
	max_percent=100,
	total_seconds=150,
):
	stop_event = asyncio.Event()
	tracker = {
		"last_text": label,
		"slow_active": False,
		"show_percent": True,
		"current_percent": start_percent,
		"slow_task": asyncio.create_task(_slow_progress_worker(message, stop_event, SLOW_PROGRESS_SECONDS)),
	}
	_progress_trackers[_tracker_key(message)] = tracker
	_progress_trackers_by_stop_event[id(stop_event)] = (_tracker_key(message), tracker)
	task = asyncio.create_task(
		_timed_progress_worker(
			message,
			stop_event,
			start_percent,
			max_percent,
			total_seconds,
		)
	)
	return task, stop_event


async def stop_progress_message(task, stop_event, message=None, final_text=None):
	stop_event.set()
	tracker = None
	tracker_entry = _progress_trackers_by_stop_event.pop(id(stop_event), None)
	if tracker_entry is not None:
		message_key, tracker = tracker_entry
		_progress_trackers.pop(message_key, None)
	elif message is not None:
		tracker = _progress_trackers.pop(_tracker_key(message), None)
	if tracker is not None:
		slow_task = tracker.get("slow_task")
		if slow_task is not None:
			slow_task.cancel()
			with suppress(asyncio.CancelledError):
				await slow_task
	if task is not None:
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


def _uploaded_file_is_empty(upload):
	if upload is None:
		return True

	if isinstance(upload, (str, os.PathLike)):
		return not os.path.exists(upload) or os.path.getsize(upload) <= 0

	if hasattr(upload, "seek") and hasattr(upload, "tell"):
		try:
			current_position = upload.tell()
			upload.seek(0, os.SEEK_END)
			size = upload.tell()
			upload.seek(current_position)
			return size <= 0
		except (OSError, ValueError):
			return False

	return False


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
	if _uploaded_file_is_empty(video):
		raise ValueError("Video file is empty")

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

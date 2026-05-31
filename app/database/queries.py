def set_credits(telegram_id, credits):
    db = SessionLocal()
    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )
    if not user:
        db.close()
        return False
    user.credits = credits
    db.commit()
    db.close()
    return True
import json
import os
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.database.db import SessionLocal
from app.database.models import (
    AppSetting,
    PaymentRequest,
    ReferralInvite,
    ReferralReward,
    Song,
    User,
)

LANGUAGE_SETTING_KEY = "enabled_song_languages"
PAYMENT_QR_SETTING_KEY = "payment_qr_file_ids"
DEFAULT_SONG_LANGUAGES = [
    "English",
    "Khmer",
    "Vietnamese",
    "Chinese",
    "Japanese",
]
REFERRAL_INVITES_PER_REWARD = 2
REFERRAL_REWARD_CREDITS = 2


def _delete_file_if_exists(file_path):

    if not file_path:
        return False

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
    except OSError:
        return False

    return False


def _prune_empty_songs(db, user_id):

    songs = (
        db.query(Song)
        .filter(Song.user_id == user_id)
        .all()
    )

    deleted_count = 0
    for song in songs:
        if song.lyrics or song.mp3_path or song.cover_path or song.source_video_path or song.video_path:
            continue

        db.delete(song)
        deleted_count += 1

    return deleted_count


# -----------------------------------
# CREATE USER
# -----------------------------------
def create_user(telegram_id, name):

    db = SessionLocal()

    existing_user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if existing_user:
        db.close()
        return existing_user

    user = User(
        telegram_id=str(telegram_id),
        name=name,
        credits=1,
    )

    db.add(user)

    db.commit()

    db.refresh(user)

    db.close()

    return user


# -----------------------------------
# SAVE SONG
# -----------------------------------
def save_song(
    telegram_id,
    style,
    topic,
    mood,
    description,
    language,
    lyrics
):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return None

    song = Song(
        user_id=user.id,
        style=style,
        topic=topic,
        mood=mood,
        description=description,
        language=language,
        lyrics=lyrics,
    )

    db.add(song)

    db.commit()

    db.refresh(song)

    db.close()

    return song


# -----------------------------------
# UPDATE SONG MP3 PATH
# -----------------------------------
def update_song_mp3(song_id, mp3_path):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.mp3_path = mp3_path
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG COVER PATH
# -----------------------------------
def update_song_cover(song_id, cover_path):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.cover_path = cover_path
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG SOURCE VIDEO PATH
# -----------------------------------
def update_song_source_video(song_id, source_video_path):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.source_video_path = source_video_path
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG VIDEO PATH
# -----------------------------------
def update_song_video(song_id, video_path):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.video_path = video_path
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG LYRICS
# -----------------------------------
def update_song_lyrics(song_id, lyrics):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.lyrics = lyrics
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG CONTEXT
# -----------------------------------
def update_song_context(song_id, topic=None, style=None):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        if topic is not None:
            song.topic = topic
        if style is not None:
            song.style = style
        db.commit()

    db.close()


# -----------------------------------
# UPDATE SONG SUBTITLE TIMING
# -----------------------------------
def update_song_subtitle_timing(song_id, subtitle_timing):

    db = SessionLocal()

    song = db.query(Song).filter(Song.id == song_id).first()

    if song:
        song.subtitle_timing = subtitle_timing
        db.commit()

    db.close()


# -----------------------------------
# GET USER SONGS
# -----------------------------------
def get_user_songs(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return []

    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .order_by(Song.created_at.desc())
        .all()
    )

    db.close()

    return songs

# -----------------------------------
# GET SONG BY ID
# -----------------------------------
def get_song_by_id(song_id):

    db = SessionLocal()

    song = (
        db.query(Song)
        .filter(Song.id == song_id)
        .first()
    )

    db.close()

    return song

# -----------------------------------
# GET USER
# -----------------------------------
def get_user(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    db.close()

    return user


def get_all_user_summaries():

    db = SessionLocal()

    users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .all()
    )

    summaries = []
    for user in users:
        song_count = (
            db.query(Song)
            .filter(Song.user_id == user.id)
            .count()
        )
        summaries.append({
            "name": user.name or "Unknown",
            "telegram_id": user.telegram_id or "",
            "credits": user.credits or 0,
            "song_count": song_count,
            "created_at": user.created_at,
        })

    db.close()

    return summaries


def get_enabled_song_languages():

    db = SessionLocal()

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == LANGUAGE_SETTING_KEY)
        .first()
    )

    db.close()

    if not setting or not setting.value:
        return DEFAULT_SONG_LANGUAGES.copy()

    try:
        languages = json.loads(setting.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_SONG_LANGUAGES.copy()

    if not isinstance(languages, list):
        return DEFAULT_SONG_LANGUAGES.copy()

    normalized_languages = [str(language) for language in languages if str(language).strip()]
    return normalized_languages or DEFAULT_SONG_LANGUAGES.copy()


def update_enabled_song_languages(languages):

    db = SessionLocal()

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == LANGUAGE_SETTING_KEY)
        .first()
    )

    serialized_languages = json.dumps(languages, ensure_ascii=False)

    if setting:
        setting.value = serialized_languages
    else:
        setting = AppSetting(
            key=LANGUAGE_SETTING_KEY,
            value=serialized_languages,
        )
        db.add(setting)

    db.commit()
    db.close()


def _load_json_setting_value(db, setting_key, default_value):

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == setting_key)
        .first()
    )

    if not setting or not setting.value:
        return default_value, setting

    try:
        parsed = json.loads(setting.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_value, setting

    return parsed, setting


def get_payment_qr_file_ids():

    db = SessionLocal()

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == PAYMENT_QR_SETTING_KEY)
        .first()
    )

    db.close()

    if not setting or not setting.value:
        return {}

    try:
        file_ids = json.loads(setting.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

    if not isinstance(file_ids, dict):
        return {}

    normalized = {}
    for package, file_id in file_ids.items():
        package_key = str(package).strip()
        file_id_value = str(file_id).strip()
        if package_key and file_id_value:
            normalized[package_key] = file_id_value

    return normalized


def get_payment_qr_file_id(package_credits):

    file_ids = get_payment_qr_file_ids()
    return file_ids.get(str(package_credits), "")


def update_payment_qr_file_id(package_credits, file_id):

    db = SessionLocal()

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == PAYMENT_QR_SETTING_KEY)
        .first()
    )

    if setting and setting.value:
        try:
            file_ids = json.loads(setting.value)
        except (TypeError, ValueError, json.JSONDecodeError):
            file_ids = {}
    else:
        file_ids = {}

    if not isinstance(file_ids, dict):
        file_ids = {}

    file_ids[str(package_credits)] = str(file_id)
    serialized_file_ids = json.dumps(file_ids, ensure_ascii=False)

    if setting:
        setting.value = serialized_file_ids
    else:
        setting = AppSetting(
            key=PAYMENT_QR_SETTING_KEY,
            value=serialized_file_ids,
        )
        db.add(setting)

    db.commit()
    db.close()


def delete_payment_qr_file_id(package_credits):

    db = SessionLocal()

    setting = (
        db.query(AppSetting)
        .filter(AppSetting.key == PAYMENT_QR_SETTING_KEY)
        .first()
    )

    if not setting or not setting.value:
        db.close()
        return False

    try:
        file_ids = json.loads(setting.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        db.close()
        return False

    if not isinstance(file_ids, dict):
        db.close()
        return False

    package_key = str(package_credits)
    if package_key not in file_ids:
        db.close()
        return False

    file_ids.pop(package_key, None)

    if file_ids:
        setting.value = json.dumps(file_ids, ensure_ascii=False)
    else:
        db.delete(setting)

    db.commit()
    db.close()
    return True


# -----------------------------------
# DEDUCT CREDIT
# -----------------------------------
def deduct_credit(telegram_id, minimum_credits=1):

    db = SessionLocal()

    try:
        required_credits = max(int(minimum_credits or 1), 1)
        # minimum_credits is an eligibility threshold; each generated asset costs 1 credit.
        updated_rows = (
            db.query(User)
            .filter(
                User.telegram_id == str(telegram_id),
                User.credits >= required_credits,
            )
            .update({User.credits: User.credits - 1}, synchronize_session=False)
        )

        if not updated_rows:
            db.rollback()
            return False

        db.commit()
        return True
    finally:
        db.close()


def refund_credit(telegram_id, credits=1):

    db = SessionLocal()

    try:
        refund_amount = max(int(credits or 0), 0)
        if refund_amount <= 0:
            return False

        updated_rows = (
            db.query(User)
            .filter(User.telegram_id == str(telegram_id))
            .update({User.credits: User.credits + refund_amount}, synchronize_session=False)
        )

        if not updated_rows:
            db.rollback()
            return False

        db.commit()
        return True
    finally:
        db.close()

# -----------------------------------
# ADD CREDITS
# -----------------------------------
def add_credits(telegram_id, credits):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return False

    user.credits += credits

    db.commit()

    db.close()

    return True


def get_referral_progress(telegram_id):

    db = SessionLocal()

    telegram_id_str = str(telegram_id)
    invite_count = (
        db.query(ReferralInvite)
        .filter(ReferralInvite.inviter_telegram_id == telegram_id_str)
        .count()
    )
    reward_rows = (
        db.query(ReferralReward)
        .filter(ReferralReward.inviter_telegram_id == telegram_id_str)
        .all()
    )

    db.close()

    current_cycle_count = invite_count % REFERRAL_INVITES_PER_REWARD
    total_reward_credits = sum(int(row.credits_awarded or 0) for row in reward_rows)

    return {
        "invite_count": invite_count,
        "current_cycle_count": current_cycle_count,
        "invites_per_reward": REFERRAL_INVITES_PER_REWARD,
        "reward_credits": REFERRAL_REWARD_CREDITS,
        "total_reward_credits": total_reward_credits,
        "reward_count": len(reward_rows),
    }


def register_referral_start(inviter_telegram_id, invited_telegram_id):

    inviter_telegram_id = str(inviter_telegram_id or "").strip()
    invited_telegram_id = str(invited_telegram_id or "").strip()

    if not inviter_telegram_id or not invited_telegram_id:
        return {"status": "invalid"}

    if inviter_telegram_id == invited_telegram_id:
        return {"status": "self_referral"}

    db = SessionLocal()
    try:
        inviter_exists = (
            db.query(User.id)
            .filter(User.telegram_id == inviter_telegram_id)
            .first()
        )
        invited_exists = (
            db.query(User.id)
            .filter(User.telegram_id == invited_telegram_id)
            .first()
        )

        if not inviter_exists or not invited_exists:
            return {"status": "invalid"}

        existing_invite = (
            db.query(ReferralInvite.id)
            .filter(ReferralInvite.invited_telegram_id == invited_telegram_id)
            .first()
        )
        if existing_invite:
            return {"status": "already_recorded"}

        try:
            with db.begin_nested():
                db.add(
                    ReferralInvite(
                        inviter_telegram_id=inviter_telegram_id,
                        invited_telegram_id=invited_telegram_id,
                    )
                )
                db.flush()
        except IntegrityError:
            return {"status": "already_recorded"}

        invite_count = (
            db.query(ReferralInvite)
            .filter(ReferralInvite.inviter_telegram_id == inviter_telegram_id)
            .count()
        )
        earned_reward_count = invite_count // REFERRAL_INVITES_PER_REWARD

        awarded_reward_count = 0
        for milestone in range(1, earned_reward_count + 1):
            try:
                with db.begin_nested():
                    db.add(
                        ReferralReward(
                            inviter_telegram_id=inviter_telegram_id,
                            milestone=milestone,
                            credits_awarded=REFERRAL_REWARD_CREDITS,
                        )
                    )
                    db.flush()
                    awarded_reward_count += 1
            except IntegrityError:
                pass

        granted_credits = awarded_reward_count * REFERRAL_REWARD_CREDITS
        if granted_credits > 0:
            (
                db.query(User)
                .filter(User.telegram_id == inviter_telegram_id)
                .update({User.credits: User.credits + granted_credits}, synchronize_session=False)
            )

        db.commit()

        return {
            "status": "recorded",
            "invite_count": invite_count,
            "granted_credits": granted_credits,
            "reward_credits": REFERRAL_REWARD_CREDITS,
            "invites_per_reward": REFERRAL_INVITES_PER_REWARD,
        }
    finally:
        db.close()


def create_payment_request(telegram_id, credits, payment_method, receipt_file_id, receipt_file_unique_id):

    db = SessionLocal()

    payment_request = PaymentRequest(
        telegram_id=str(telegram_id),
        credits=int(credits),
        payment_method=str(payment_method),
        receipt_file_id=str(receipt_file_id),
        receipt_file_unique_id=str(receipt_file_unique_id),
        status="pending",
    )

    db.add(payment_request)
    db.commit()
    db.refresh(payment_request)
    db.close()

    return payment_request


def process_payment_request(payment_request_id, status):

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"approved", "rejected"}:
        return "invalid_status", None

    db = SessionLocal()

    payment_request = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.id == int(payment_request_id))
        .first()
    )

    if not payment_request:
        db.close()
        return "not_found", None

    if payment_request.status != "pending":
        current_status = payment_request.status
        db.close()
        return "already_processed", current_status

    updated_rows = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.id == payment_request.id, PaymentRequest.status == "pending")
        .update(
            {
                PaymentRequest.status: normalized_status,
                PaymentRequest.processed_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )

    if updated_rows != 1:
        db.rollback()
        db.close()
        return "already_processed", None

    if normalized_status == "approved":
        user = (
            db.query(User)
            .filter(User.telegram_id == payment_request.telegram_id)
            .first()
        )
        if not user:
            db.rollback()
            db.close()
            return "user_not_found", None

        user.credits += payment_request.credits

    db.commit()

    result = {
        "telegram_id": payment_request.telegram_id,
        "credits": payment_request.credits,
        "payment_method": payment_request.payment_method,
        "status": normalized_status,
    }
    db.close()
    return "processed", result


# -----------------------------------
# DELETE USER LYRICS
# -----------------------------------
def delete_user_lyrics(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return 0

    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .all()
    )

    deleted_count = 0
    for song in songs:
        if song.lyrics:
            song.lyrics = None
            deleted_count += 1

    _prune_empty_songs(db, user.id)
    db.commit()
    db.close()

    return deleted_count


# -----------------------------------
# DELETE USER MP3
# -----------------------------------
def delete_user_mp3(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return 0

    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .all()
    )

    deleted_count = 0
    for song in songs:
        if song.mp3_path:
            _delete_file_if_exists(song.mp3_path)
            song.mp3_path = None
            deleted_count += 1

    _prune_empty_songs(db, user.id)
    db.commit()
    db.close()

    return deleted_count


# -----------------------------------
# DELETE USER MP4
# -----------------------------------
def delete_user_mp4(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return 0

    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .all()
    )

    deleted_count = 0
    for song in songs:
        if song.video_path:
            _delete_file_if_exists(song.video_path)
            song.video_path = None
            deleted_count += 1

    _prune_empty_songs(db, user.id)
    db.commit()
    db.close()

    return deleted_count


# -----------------------------------
# RESET USER SONG DATA
# -----------------------------------
def reset_user_song_data(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return 0

    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .all()
    )

    deleted_count = 0
    for song in songs:
        _delete_file_if_exists(song.mp3_path)
        _delete_file_if_exists(song.cover_path)
        _delete_file_if_exists(song.source_video_path)
        _delete_file_if_exists(song.video_path)
        db.delete(song)
        deleted_count += 1

    db.commit()
    db.close()

    return deleted_count


# -----------------------------------
# DELETE ONE SONG LYRICS
# -----------------------------------
def delete_song_lyrics(song_id, telegram_id):

    db = SessionLocal()

    song = (
        db.query(Song)
        .join(User, Song.user_id == User.id)
        .filter(Song.id == song_id, User.telegram_id == str(telegram_id))
        .first()
    )

    if not song or not song.lyrics:
        db.close()
        return False

    song.lyrics = None
    _prune_empty_songs(db, song.user_id)
    db.commit()
    db.close()

    return True


# -----------------------------------
# DELETE ONE SONG MP3
# -----------------------------------
def delete_song_mp3(song_id, telegram_id):

    db = SessionLocal()

    song = (
        db.query(Song)
        .join(User, Song.user_id == User.id)
        .filter(Song.id == song_id, User.telegram_id == str(telegram_id))
        .first()
    )

    if not song or not song.mp3_path:
        db.close()
        return False

    _delete_file_if_exists(song.mp3_path)
    song.mp3_path = None
    _prune_empty_songs(db, song.user_id)
    db.commit()
    db.close()

    return True


# -----------------------------------
# DELETE ONE SONG MP4
# -----------------------------------
def delete_song_mp4(song_id, telegram_id):

    db = SessionLocal()

    song = (
        db.query(Song)
        .join(User, Song.user_id == User.id)
        .filter(Song.id == song_id, User.telegram_id == str(telegram_id))
        .first()
    )

    if not song or not song.video_path:
        db.close()
        return False

    _delete_file_if_exists(song.video_path)
    song.video_path = None
    _prune_empty_songs(db, song.user_id)
    db.commit()
    db.close()

    return True
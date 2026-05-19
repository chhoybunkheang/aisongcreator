import json
import os

from app.database.db import SessionLocal
from app.database.models import (
    AppSetting,
    Song,
    User,
)

LANGUAGE_SETTING_KEY = "enabled_song_languages"
DEFAULT_SONG_LANGUAGES = [
    "English",
    "Khmer",
    "Vietnamese",
    "Chinese",
    "Japanese",
]


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
        if song.lyrics or song.mp3_path or song.cover_path or song.video_path:
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


# -----------------------------------
# DEDUCT CREDIT
# -----------------------------------
def deduct_credit(telegram_id):

    db = SessionLocal()

    user = (
        db.query(User)
        .filter(User.telegram_id == str(telegram_id))
        .first()
    )

    if not user:
        db.close()
        return False

    if user.credits <= 0:
        db.close()
        return False

    user.credits -= 1

    db.commit()

    db.close()

    return True

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
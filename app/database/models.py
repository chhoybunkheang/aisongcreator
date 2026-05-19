from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.database.db import Base


# -----------------------------------
# USER TABLE
# -----------------------------------
class User(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    telegram_id = Column(String, unique=True)

    name = Column(String)

    credits = Column(Integer, default=3)

    created_at = Column(DateTime, default=datetime.utcnow)


# -----------------------------------
# SONG TABLE
# -----------------------------------
class Song(Base):

    __tablename__ = "songs"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    style = Column(String)

    topic = Column(String)

    mood = Column(String)

    language = Column(String)

    lyrics = Column(Text)

    subtitle_timing = Column(Text, nullable=True)

    mp3_path = Column(String, nullable=True)

    cover_path = Column(String, nullable=True)

    video_path = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# -----------------------------------
# APP SETTINGS TABLE
# -----------------------------------
class AppSetting(Base):

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)

    key = Column(String, unique=True, nullable=False)

    value = Column(Text, nullable=True)
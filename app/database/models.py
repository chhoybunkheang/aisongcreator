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

    credits = Column(Integer, default=1)

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


# -----------------------------------
# PAYMENT REQUEST TABLE
# -----------------------------------
class PaymentRequest(Base):

    __tablename__ = "payment_requests"

    id = Column(Integer, primary_key=True)

    telegram_id = Column(String, nullable=False)

    credits = Column(Integer, nullable=False)

    payment_method = Column(String, nullable=False)

    receipt_file_id = Column(String, nullable=False)

    receipt_file_unique_id = Column(String, nullable=False)

    status = Column(String, nullable=False, default="pending")

    created_at = Column(DateTime, default=datetime.utcnow)

    processed_at = Column(DateTime, nullable=True)


# -----------------------------------
# REFERRAL INVITES TABLE
# -----------------------------------
class ReferralInvite(Base):

    __tablename__ = "referral_invites"

    id = Column(Integer, primary_key=True)

    inviter_telegram_id = Column(String, nullable=False)

    invited_telegram_id = Column(String, unique=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)


# -----------------------------------
# REFERRAL REWARDS TABLE
# -----------------------------------
class ReferralReward(Base):

    __tablename__ = "referral_rewards"

    id = Column(Integer, primary_key=True)

    inviter_telegram_id = Column(String, nullable=False)

    milestone = Column(Integer, nullable=False)

    credits_awarded = Column(Integer, nullable=False, default=2)

    created_at = Column(DateTime, default=datetime.utcnow)
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATABASE_URL, SQLITE_DB_PATH

if DATABASE_URL.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)


# Create engine
engine = create_engine(
    DATABASE_URL,
    echo=False
)


# Create session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Base model
Base = declarative_base()


# -----------------------------------
# MIGRATE: add new columns if missing
# -----------------------------------
def migrate_db():
    inspector = inspect(engine)
    existing = [col["name"] for col in inspector.get_columns("songs")]
    new_columns = {
        "subtitle_timing": "TEXT",
        "mp3_path": "VARCHAR",
        "cover_path": "VARCHAR",
        "source_video_path": "VARCHAR",
        "video_path": "VARCHAR",
        "description": "TEXT",
    }
    with engine.connect() as conn:
        for col, col_type in new_columns.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE songs ADD COLUMN {col} {col_type}"))
                conn.commit()
                print(f"[DB] Added column: songs.{col}")

        existing_indexes = {index["name"] for index in inspector.get_indexes("referral_rewards")}
        unique_index_name = "uq_referral_rewards_inviter_milestone"
        if unique_index_name not in existing_indexes:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_referral_rewards_inviter_milestone "
                    "ON referral_rewards (inviter_telegram_id, milestone)"
                )
            )
            conn.commit()
            print("[DB] Ensured unique index: referral_rewards(inviter_telegram_id, milestone)")
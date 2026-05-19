from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# SQLite database
DATABASE_URL = "sqlite:///data/users.db"


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
        "video_path": "VARCHAR",
    }
    with engine.connect() as conn:
        for col, col_type in new_columns.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE songs ADD COLUMN {col} {col_type}"))
                conn.commit()
                print(f"[DB] Added column: songs.{col}")
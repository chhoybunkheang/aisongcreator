import os
from pathlib import Path

from app.services.video_service import create_music_video

songs_dir = Path("media/generated/songs")
covers_dir = Path("media/generated/covers")
videos_dir = Path("media/generated/videos")

song_files = sorted(songs_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
cover_files = sorted(
    [*covers_dir.glob("*.jpg"), *covers_dir.glob("*.jpeg"), *covers_dir.glob("*.png")],
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)

if not song_files:
    raise FileNotFoundError(f"No .mp3 files found in: {songs_dir}")

if not cover_files:
    raise FileNotFoundError(f"No .jpg, .jpeg, or .png files found in: {covers_dir}")

audio_path = str(song_files[0])
image_path = str(cover_files[0])
output_path = str(videos_dir / "test.mp4")

if not os.path.exists(audio_path):
    raise FileNotFoundError(f"Missing test audio file: {audio_path}")

if not os.path.exists(image_path):
    raise FileNotFoundError(f"Missing test image file: {image_path}")

os.makedirs(os.path.dirname(output_path), exist_ok=True)

create_music_video(
    audio_path=audio_path,
    image_path=image_path,
    output_path=output_path,
)

print(f"✅ Video created: {output_path}")
from telegram import ReplyKeyboardMarkup


def get_main_menu():
    keyboard = [
        ["🎵 Create Song", "📝 My Lyrics"],
        ["🎵 My MP3", "🎬 My MP4"],
        ["⚙️ Settings", "💎 Add Credit"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )
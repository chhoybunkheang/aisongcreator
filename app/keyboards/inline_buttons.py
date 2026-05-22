from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_create_song_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎵 Create Song", callback_data="create_song")],
    ]
    return InlineKeyboardMarkup(keyboard)

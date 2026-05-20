MAX_LYRICS_LENGTH = 3000
MAX_STYLE_LENGTH = 80
MAX_TOPIC_LENGTH = 120
MAX_MOOD_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500


def _normalize_single_line(value):
	return " ".join(str(value or "").split()).strip()


def validate_lyrics(value):
	lyrics = str(value or "").strip()
	if not lyrics:
		return None, "❌ Lyrics cannot be empty."
	if len(lyrics) > MAX_LYRICS_LENGTH:
		return None, f"❌ Lyrics are too long. Please keep them under {MAX_LYRICS_LENGTH} characters."
	return lyrics, None


def validate_style(value):
	style = _normalize_single_line(value)
	if not style:
		return None, "❌ Music style cannot be empty."
	if len(style) > MAX_STYLE_LENGTH:
		return None, f"❌ Music style is too long. Please keep it under {MAX_STYLE_LENGTH} characters."
	return style, None


def validate_topic(value):
	topic = _normalize_single_line(value)
	if not topic:
		return None, "❌ Song topic cannot be empty."
	if len(topic) > MAX_TOPIC_LENGTH:
		return None, f"❌ Song topic is too long. Please keep it under {MAX_TOPIC_LENGTH} characters."
	return topic, None


def validate_mood(value):
	mood = _normalize_single_line(value)
	if not mood:
		return None, "❌ Mood cannot be empty."
	if len(mood) > MAX_MOOD_LENGTH:
		return None, f"❌ Mood is too long. Please keep it under {MAX_MOOD_LENGTH} characters."
	return mood, None


def validate_description(value):
	description = str(value or "").strip()
	if not description:
		return "", None
	if len(description) > MAX_DESCRIPTION_LENGTH:
		return None, f"❌ Description is too long. Please keep it under {MAX_DESCRIPTION_LENGTH} characters."
	return description, None

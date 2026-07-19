"""SMS segment math shared by the renderers."""

SMS_LIMIT = 160


def message_length(message: str) -> float:
    """Computes the byte length of a string including emojis."""
    return len(message.encode(encoding='utf_16_le')) / 2

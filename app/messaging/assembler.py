"""SMS segment math and packing of reply blocks into single-SMS messages."""

from dataclasses import dataclass

# Limits for one SMS sent on its own. Every packed message goes out as an
# independent SMS, never as a part of a concatenated message, so no room
# is reserved for a concatenation header (the 153/67 per-part limits do
# not apply). Any character outside the GSM alphabet puts the whole
# message in UCS-2, which holds 70 UTF-16 code units instead of 160.
SMS_LIMIT = 160
UCS2_LIMIT = 70

# GSM-7 default alphabet (3GPP TS 23.038). Extension characters are sent
# as an escape septet plus the character, so they cost two.
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789"
    ":;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑܧ¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXTENSION = set("^{}\\[~]|€")


@dataclass
class Block:
    """One self-contained unit of a reply, with renderings largest first.

    An optional block never gets a message to itself: unless something
    required shares its message, it is dropped."""
    ladder: list[str]
    optional: bool = False


def _gsm_septets(text: str):
    total = 0
    for char in text:
        if char in _GSM_BASIC:
            total += 1
        elif char in _GSM_EXTENSION:
            total += 2
        else:
            return None
    return total


def segment_cost(text: str) -> str:
    """The cost of text as one SMS against its encoding's limit,
    e.g. '148/160 GSM-7' or '64/70 UCS-2'."""
    septets = _gsm_septets(text)
    if septets is not None:
        return f"{septets}/{SMS_LIMIT} GSM-7"
    return f"{len(text.encode('utf_16_le')) // 2}/{UCS2_LIMIT} UCS-2"


def fits_segment(text: str) -> bool:
    """True when text fits a single SMS: 160 GSM-7 septets, or 70 UTF-16
    units when any character forces the UCS-2 encoding."""
    septets = _gsm_septets(text)
    if septets is not None:
        return septets <= SMS_LIMIT
    return len(text.encode('utf_16_le')) / 2 <= UCS2_LIMIT


def _best_rendering(block: Block) -> str:
    for rendering in block.ladder:
        if fits_segment(rendering):
            return rendering
    # Nothing fits: use the smallest rendering anyway. assemble() gives it
    # a message of its own, so only that one message runs oversize.
    return block.ladder[-1]


def as_text(blocks: list[Block], separator: str = '\n\n') -> str:
    """The flattened single-string view of blocks: exactly what joining
    the assembled messages with the separator reconstructs."""
    return separator.join(assemble(blocks, separator))


def assemble(blocks: list[Block], separator: str = '\n\n') -> list[str]:
    """Pack blocks into single-SMS messages, in order, whole blocks only.

    Each block contributes its largest rendering that fits one SMS on its
    own; when none fit, its smallest rendering becomes a message by
    itself, the one message allowed to run oversize. Messages fill
    greedily: a block that no longer fits starts the next message. A
    block whose chosen rendering is empty has nothing to say and is
    dropped, and a message holding only optional blocks is dropped whole.
    Joining the returned messages with the separator reproduces the
    separator-joined content of the blocks that shipped, exactly.
    """
    messages = []
    current = ''
    current_required = False
    for block in blocks:
        text = _best_rendering(block)
        if not text:
            continue
        candidate = f"{current}{separator}{text}" if current else text
        if current and not fits_segment(candidate):
            if current_required:
                messages.append(current)
            current = text
            current_required = not block.optional
        else:
            current = candidate
            current_required = current_required or not block.optional
    if current and current_required:
        messages.append(current)
    return messages

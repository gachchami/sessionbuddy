"""Shared HTTP presentation helpers."""

import re
from urllib.parse import quote

ATTACHMENT_ASCII_LIMIT = 120


def attachment_header(filename: str, *, ascii_suffix: str = "") -> str:
    """Return a single-line RFC 5987 attachment header with an ASCII fallback."""
    normalized = "".join(
        "-" if character in "/\\" or ord(character) < 32 or ord(character) == 127 else character
        for character in filename
    )
    safe_ascii = "".join(
        character
        for character in normalized
        if character.isascii()
        and (character.isalnum() or character in ".-_ ")
    )
    safe_ascii = "-".join(re.sub(r"\.{2,}", ".", safe_ascii).split())
    suffix = "".join(
        character
        for character in ascii_suffix
        if character.isascii()
        and (character.isalnum() or character in ".-_")
    )
    suffix = suffix[-(ATTACHMENT_ASCII_LIMIT - 1) :]
    if suffix:
        if safe_ascii.endswith(suffix):
            safe_ascii = safe_ascii[: -len(suffix)]
        safe_ascii = safe_ascii.lstrip(".-")
        prefix_limit = max(1, ATTACHMENT_ASCII_LIMIT - len(suffix))
        safe_ascii = safe_ascii[:prefix_limit].rstrip(".- ")
        safe_ascii = (
            f"{safe_ascii}{suffix}"
            if safe_ascii
            else suffix.lstrip(".-") or "download"
        )
    else:
        safe_ascii = safe_ascii.lstrip(".-")
        safe_ascii = safe_ascii[:ATTACHMENT_ASCII_LIMIT].rstrip(".- ") or "download"
    encoded = quote(normalized, safe="")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"

"""Portuguese phonetic spelling for TTS input only."""

import re
from functools import lru_cache
from unicodedata import normalize

PT_BR_PRONUNCIATION = {
    "ARPANET": "Arpanét",
    "NSFNET": "Ene Esse Efe Nét",
    "World Wide Web": "Uórld Uáid Uéb",
    "NeXT": "Nékst",
    "QR Code": "Quê Érre Côud",
}

@lru_cache(maxsize=32)
def _rules(locale, overrides):
    entries = {
        key.casefold(): (key, value, key == "NeXT")
        for key, value in PT_BR_PRONUNCIATION.items()
    } if locale == "pt-br" else {}
    for key, value in overrides:
        entries[key.casefold()] = (key, value, False)
    if not entries:
        return None, {}
    alternatives, replacements = [], {}
    for index, (key, value, case_sensitive) in enumerate(sorted(entries.values(), key=lambda item: -len(item[0]))):
        group = f"term{index}"
        search = r"\s+".join(map(re.escape, key.split()))
        if case_sensitive:
            search = f"(?-i:{search})"
        alternatives.append(f"(?P<{group}>{search})")
        replacements[group] = value
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)
    return pattern, replacements


def pronounce(text, locale, overrides=None):
    custom = tuple(
        (normalize("NFC", " ".join(key.split())), normalize("NFC", value))
        for key, value in (overrides or {}).items() if key.strip()
    )
    pattern, spellings = _rules((locale or "").lower(), custom)
    if pattern is None:
        return text
    return pattern.sub(lambda match: spellings[match.lastgroup], normalize("NFC", text))

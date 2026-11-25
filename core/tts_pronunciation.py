"""Portuguese phonetic spelling for TTS input only."""

import re

PT_BR_PRONUNCIATION = {
    "ARPANET": "Arpanét",
    "NSFNET": "Ene Esse Efe Nét",
    "World Wide Web": "Uórld Uáid Uéb",
    "NeXT": "Nékst",
    "QR Code": "Quê Érre Côud",
}

_spellings = {key.casefold(): value for key, value in PT_BR_PRONUNCIATION.items()}
_pattern = re.compile(r"(?<!\w)(?:" + "|".join(
    r"\s+".join(map(re.escape, key.split())) if key != "NeXT" else "(?-i:NeXT)"
    for key in sorted(PT_BR_PRONUNCIATION, key=len, reverse=True)
) + r")(?!\w)", re.IGNORECASE)


def pronounce(text, locale):
    if (locale or "").lower() != "pt-br":
        return text
    return _pattern.sub(lambda match: _spellings[" ".join(match.group().split()).casefold()], text)

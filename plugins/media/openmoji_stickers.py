"""Fetch published emoji assets on demand and keep an atomic local raster cache."""

import re
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from PIL import Image

from core.registry import register_media
from plugins.media.local_pack import resolve_media as resolve_local_media

ALIASES = {
    "question_mark": "2753", "exclamation": "2757", "spark": "2728", "raindrop": "1F4A7",
    "olive_branch": "1F33F", "dove": "1F54A", "stink": "1F4A8", "star": "2B50",
}
RELEASES = {
    "openmoji": ("hfg-gmuend/openmoji", "16.0.0", "color"),
    "twemoji": ("jdecked/twemoji", "v16.0.1", "assets"),
}
MAX_ICON_BYTES = 512 * 1024


def _codepoints(kind):
    if not isinstance(kind, str) or not kind:
        raise ValueError("Each sticker requires a kind")
    if kind in ALIASES:
        return ALIASES[kind]
    if re.fullmatch(r"[A-Fa-f0-9]{2,6}(?:-[A-Fa-f0-9]{2,6})*", kind):
        codes = [int(value, 16) for value in kind.split("-")]
        if all(0x20 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF for code in codes):
            return "-".join(f"{code:04X}" for code in codes)
    elif all(ord(character) > 127 or character in "#*0123456789" for character in kind):
        return "-".join(f"{ord(character):04X}" for character in kind)
    raise ValueError(f"Unknown sticker {kind!r}. Use an alias, emoji or hexadecimal code points")


def _read_icon(url):
    with urlopen(url, timeout=10) as response:
        data = response.read(MAX_ICON_BYTES + 1)
    if not 0 < len(data) <= MAX_ICON_BYTES:
        raise ValueError("Unexpected sticker file size")
    return data


def _cache_file(path, payload):
    with NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(payload)
            temporary.close()
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def sticker_path(kind, assets_root, *, source="openmoji", format="svg"):
    if source not in RELEASES or format not in {"png", "svg"}:
        raise ValueError("Sticker source must be openmoji or twemoji, with format png or svg")
    code = _codepoints(kind)
    repository, version, tree = RELEASES[source]
    if source == "twemoji":
        if "200D" not in code.split("-"):
            code = "-".join(value for value in code.split("-") if value != "FE0F")
        code = "-".join(f"{int(value, 16):x}" for value in code.split("-"))
    cache = Path(assets_root) / "assets" / "stickers" / "cache" / f"{source}-{version}-{format}"
    path = cache / f"{code}.png"
    if path.is_file():
        return path

    folder = "svg" if format == "svg" else "72x72"
    url = f"https://raw.githubusercontent.com/{repository}/{version}/{tree}/{folder}/{code}.{format}"
    original = cache / f"{code}.svg"
    try:
        data = original.read_bytes() if format == "svg" and original.is_file() else _read_icon(url)
        if format == "svg":
            from cairosvg import svg2png

            png = svg2png(bytestring=data, output_width=384, output_height=384, unsafe=False)
        else:
            png = data
        with Image.open(BytesIO(png)) as icon:
            image = icon.convert("RGBA")
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Could not load the {source} sticker {kind!r} from {url}") from error
    if not image.getbbox():
        raise ValueError(f"The {source} sticker {kind!r} is entirely transparent")
    cache.mkdir(parents=True, exist_ok=True)
    if format == "svg" and not original.is_file():
        _cache_file(original, data)
    _cache_file(path, png)
    return path


@register_media("openmoji_stickers")
def resolve_media(scene, timing, assets_root):
    files = resolve_local_media(scene, timing, assets_root)
    stickers = scene.options.get("stickers", ())
    if not isinstance(stickers, (list, tuple)) or any(not isinstance(sticker, dict) for sticker in stickers):
        raise ValueError(f"Scene {scene.id!r}: stickers must be an array of sticker objects")
    for index, sticker in enumerate(stickers):
        key = f"sticker:{index}"
        if key not in files:
            files[key] = sticker_path(sticker.get("kind"), assets_root, source=sticker.get("source", "openmoji"),
                                      format=sticker.get("format", "svg"))
    return files

from io import BytesIO

from PIL import Image

from plugins.media import openmoji_stickers


def test_openmoji_svg_raster_and_original_are_cached(tmp_path, monkeypatch):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7"/></svg>'
    fetched = []

    def read(url):
        fetched.append(url)
        return svg

    monkeypatch.setattr(openmoji_stickers, "_read_icon", read)
    path = openmoji_stickers.sticker_path("raindrop", tmp_path)
    assert path.with_suffix(".svg").read_bytes() == svg
    with Image.open(path) as icon:
        assert icon.size == (384, 384)
        assert icon.mode == "RGBA"
    assert openmoji_stickers.sticker_path("💧", tmp_path) == path
    assert fetched == ["https://raw.githubusercontent.com/hfg-gmuend/openmoji/16.0.0/color/svg/1F4A7.svg"]


def test_twemoji_png_url_uses_canonical_codepoints(tmp_path, monkeypatch):
    data = BytesIO()
    Image.new("RGBA", (72, 72), "#3696bf").save(data, format="PNG")
    fetched = []

    def read(url):
        fetched.append(url)
        return data.getvalue()

    monkeypatch.setattr(openmoji_stickers, "_read_icon", read)
    path = openmoji_stickers.sticker_path("1️⃣", tmp_path, source="twemoji", format="png")
    assert path.name == "31-20e3.png"
    assert fetched == ["https://raw.githubusercontent.com/jdecked/twemoji/v16.0.1/assets/72x72/31-20e3.png"]

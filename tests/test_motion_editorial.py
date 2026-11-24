from pathlib import Path

import pytest
from PIL import Image

from cartridges.motion_editorial.renderer import TEMPLATE, Options, composition_payload, editorial_captions
from core.html_adapter import open_composition
from core.schema import AudioTrackSpec, CaptionBlock, EpisodeSpec, SceneTiming, WordTiming

ROOT = Path(__file__).resolve().parents[1]


def test_singleton_caption_is_merged_without_changing_words_or_timestamps():
    words = (
        WordTiming("A", 0, .1), WordTiming("origem.", .1, .4),
        WordTiming("Depois", .9, 1.1),
        WordTiming("surgiu", 1.18, 1.4), WordTiming("outro.", 1.4, 1.6),
    )
    blocks = editorial_captions(CaptionBlock(group) for group in (words[:2], words[2:3], words[3:]))
    assert [block.text for block in blocks] == ["A origem.", "Depois surgiu outro."]
    assert tuple(word for block in blocks for word in block.words) == words


@pytest.mark.parametrize(("width", "height"), [(360, 640), (640, 360)])
def test_browser_seek_is_repeatable_and_highlights_the_spoken_word(width, height):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).exists():
            shell = Path(runtime.chromium.executable_path).parents[2]
            if not any(shell.glob("chromium_headless_shell-*")):
                pytest.skip("Install Playwright Chromium to run the browser tests")
    spec = EpisodeSpec.from_json((ROOT / "examples/origem_qr_code.json").read_bytes())
    words = (WordTiming("Código", 5, 6), WordTiming("comparado.", 6, 8))
    timing = SceneTiming(AudioTrackSpec(Path("speech.wav"), words, samples=384000),
                         0, 240, 30, (CaptionBlock(words),), True)
    scene = spec.scenes[2]
    payload = composition_payload(scene.id, Options.model_validate(scene.options), timing)
    with open_composition(TEMPLATE, width=width, height=height, payload=payload) as capture:
        first = capture(5.6, .7)
        second = capture(7, .875)
        assert first == capture(5.6, .7)

    def highlight_x(frame):
        image = Image.frombytes("RGB", (width, height), frame)
        horizontal = width > height
        left, right = (.045, .38) if horizontal else (.075, .925)
        top, bottom = (.76, .91) if horizontal else (.74, .83)
        points = []
        for y in range(round(top * height), round(bottom * height)):
            for x in range(round(left * width), round(right * width)):
                red, green, blue = image.getpixel((x, y))
                if red > 219 and 130 < green < 205 and 25 < blue < 94:
                    points.append(x)
        assert points
        return sum(points) / len(points)

    assert highlight_x(first) < highlight_x(second)

import json
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from cartridges.motion_editorial.renderer import TEMPLATE, Options, composition_payload, editorial_captions
from core.encoder import run_ffmpeg
from core.html_adapter import encode_scenes, open_composition
from core.schema import AudioTrackSpec, CaptionBlock, EpisodeSpec, SceneRender, SceneTiming, WordTiming

ROOT = Path(__file__).resolve().parents[1]


def require_chromium():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        expected = Path(runtime.chromium.executable_path)
        if not expected.exists() and not any(expected.parents[2].glob("chromium_headless_shell-*")):
            pytest.skip("Install Playwright Chromium to run the browser tests")


def test_singleton_caption_is_merged_without_changing_words_or_timestamps():
    words = (
        WordTiming("A", 0, .1), WordTiming("origem.", .1, .4),
        WordTiming("Depois", .9, 1.1),
        WordTiming("surgiu", 1.18, 1.4), WordTiming("outro.", 1.4, 1.6),
    )
    blocks = editorial_captions(CaptionBlock(group) for group in (words[:2], words[2:3], words[3:]))
    assert [block.text for block in blocks] == ["A origem.", "Depois surgiu outro."]
    assert tuple(word for block in blocks for word in block.words) == words


def test_parallel_concat_keeps_frame_count_and_script_order(tmp_path):
    width, height, fps = 160, 90, 30
    source = AudioTrackSpec(Path("fixture.wav"), samples=0)
    timeline, renders = [], []
    start = 0
    for count, red in ((17, 30), (62, 215), (1, 120), (11, 60)):
        timeline.append(SceneTiming(source, start, count, fps))
        renders.append(SceneRender([bytes((red, 40, 40)) * (width * height)] * count, web=True))
        start += count
    output = tmp_path / "assembled.mp4"
    encode_scenes(timeline, renders, width=width, height=height, fps=fps, output=output, workers=2)
    metadata = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)])
    stream = json.loads(metadata)["streams"][0]
    assert stream["avg_frame_rate"] == "30/1"
    assert int(stream["nb_frames"]) == int(stream["duration_ts"]) == start
    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-pix_fmt", "rgb24",
                                     "-f", "rawvideo", "pipe:1"])
    reds = [pixels[scene.start_frame * width * height * 3] for scene in timeline]
    assert reds[0] < reds[3] < reds[2] < reds[1]


@pytest.mark.parametrize(("width", "height"), [(360, 640), (640, 360)])
def test_browser_seek_is_repeatable_and_highlights_the_spoken_word(width, height):
    require_chromium()
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


def test_frame_zero_png_alpha_and_jpeg_mix_with_ffmpeg(tmp_path):
    require_chromium()
    width, height = 360, 202
    background = tmp_path / "background.mp4"
    run_ffmpeg("-v", "error", "-f", "lavfi", "-i", "testsrc2=s=192x144:r=24:d=1",
               "-c:v", "libx264", str(background))
    source = AudioTrackSpec(Path("voice.wav"), samples=24000)
    timing = SceneTiming(source, 0, 12, 30)
    data = {"context": "REDE 1969", "lines": ["ORIGEM        UCLA", "DESTINO       SRI"]}
    frames = []
    for format, mode in (("png", "broll_video"), ("jpeg", "grid_glow")):
        options = Options(kicker="ARQUIVO", headline="Primeira mensagem", module="terminal_code", bg_mode=mode, data=data)
        payload = composition_payload("qa", options, timing)
        with open_composition(TEMPLATE, width=width, height=height, payload=payload, frame_format=format) as capture:
            frames.append([capture(index / 30, index / 12) for index in range(12)])
    opening = Image.open(BytesIO(frames[0][0]))
    assert opening.getchannel("A").getpixel((0, 100)) < 200
    assert Image.open(BytesIO(frames[1][0])).format == "JPEG"
    output = tmp_path / "mixed.mp4"
    encode_scenes([timing, SceneTiming(source, 12, 12, 30)], [
        SceneRender(frames[0], web=True, frame_format="png", background=background),
        SceneRender(frames[1], web=True, frame_format="jpeg"),
    ], width=width, height=height, fps=30, output=output, workers=2)
    probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                                     "-of", "json", str(output)])
    stream = json.loads(probe)["streams"][0]
    assert (stream["avg_frame_rate"], int(stream["nb_frames"])) == ("30/1", 24)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
                                   "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    image = Image.frombytes("RGB", (width, height), raw)
    title = image.crop((14, 28, 140, 92))
    stage = image.crop((155, 55, 350, 151))
    assert sum(min(pixel) > 185 for pixel in title.get_flattened_data()) > 30
    assert sum(min(pixel) > 90 for pixel in stage.get_flattened_data()) > 15
    assert sum(r > 95 and r > 1.22 * g and g > 1.20 * b for r, g, b in stage.get_flattened_data()) > 2

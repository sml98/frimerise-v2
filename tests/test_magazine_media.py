import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image, ImageDraw

from cartridges.cutout_paper import magazine, renderer
from core import color_grade, cutout_matting
from core.schema import AudioTrackSpec, EpisodeSpec, SceneTiming


def test_approved_matte_is_cached_and_decontaminates_the_edge(tmp_path, monkeypatch):
    source = tmp_path / "photo.png"
    photo = Image.new("RGB", (7, 7), "blue")
    ImageDraw.Draw(photo).rectangle((2, 2, 4, 4), fill="red")
    photo.save(source)
    matte = Image.new("L", photo.size, 0)
    ImageDraw.Draw(matte).rectangle((1, 1, 5, 5), fill=100)
    ImageDraw.Draw(matte).rectangle((2, 2, 4, 4), fill=255)
    predictions = []

    def predict(image, device):
        assert device == "cpu"
        predictions.append(image.size)
        return matte

    monkeypatch.setenv("FRIMERISE_CUTOUT_CACHE", str(tmp_path / "cutouts"))
    monkeypatch.setattr(cutout_matting, "_predict", predict)
    with ThreadPoolExecutor(2) as pool:
        first, second = pool.map(lambda _: cutout_matting.remove_background(source, "cpu"), range(2))
    assert first == second and predictions == [photo.size]
    with Image.open(first) as result:
        assert result.mode == "RGBA" and result.info["matting_license"] == "MIT"
        assert result.getpixel((0, 0)) == (0, 0, 0, 0)
        r, _, b, alpha = result.getpixel((2, 1))
        assert r > b and 0 < alpha < 255
    assert cutout_matting.matting_model(first) == "birefnet-general-lite"


def test_luts_are_valid_ffmpeg_tables_and_keep_the_matte(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIMERISE_GRADE_CACHE", str(tmp_path / "graded"))
    folder = tmp_path / "Grade [teste]: John's LUTs"
    folder.mkdir()
    for table in color_grade.LUT_DIR.glob("*.cube"):
        shutil.copy(table, folder)
    monkeypatch.setattr(color_grade, "LUT_DIR", folder)
    source = tmp_path / "source.png"
    original = Image.new("RGBA", (24, 16), (173, 108, 66, 137))
    original.save(source)
    grades = []
    for name in color_grade.PROFILES:
        path = color_grade.grade_photo(source, name + ".cube")
        assert color_grade.grade_photo(source, name) == path
        with Image.open(path) as image:
            assert image.getchannel("A").tobytes() == original.getchannel("A").tobytes()
            grades.append(image.getpixel((12, 8))[:3])
    assert len(set(grades)) == 3


@pytest.mark.parametrize(("module", "fields"), [
    ("photo_cutout", {"media_query": "Portrait", "photo_label": "Arquivo visual"}),
    ("newspaper_clipping", {"media_query": "Portrait", "clipping_headline": "Edição de arquivo",
                            "highlight": "Testemunho local"}),
    ("collage_board", {"photos": [{"media_query": "one"}, {"media_query": "two"}], "board_stamp": "REVISÃO"}),
])
@pytest.mark.parametrize(("width", "height", "aspect"), [(180, 320, "9:16"), (320, 180, "16:9")])
def test_magazine_modules_use_timed_paper_poses(tmp_path, monkeypatch, module, fields, width, height, aspect):
    monkeypatch.setattr(magazine, "fetch_image", lambda *_: tmp_path / "photo.png")
    monkeypatch.setattr(magazine, "_copy", lambda _, lut, size, seed: (
        Image.new("RGBA", size, (39, 112 + seed % 50, 142, 255)), cutout_matting.CPU_MODEL,
    ))
    spec = EpisodeSpec("Teste", "cutout_paper", width=width, height=height, aspect_ratio=aspect, fps=30,
                       scenes=[{"id": "foto", "plugin": "cutout_paper", "narration": "Uma imagem contada.",
                                "module": module, **fields}])
    timing = SceneTiming(AudioTrackSpec(tmp_path / "voice.wav", samples=48000), 0, 30, 30, continuous=True)
    rendered = renderer.render_scene(spec, spec.scenes[0], timing, {})
    frames = list(rendered.frames)
    assert rendered.matting_models == (cutout_matting.CPU_MODEL,)
    assert rendered.color_luts == ("vintage_paper_warm",)
    assert len(frames) == 30 and frames[0] != frames[-1]
    # Frames 28 and 29 share the same 12 Hz texture sample. The rig still moves at 30 fps.
    assert frames[-2] != frames[-1]

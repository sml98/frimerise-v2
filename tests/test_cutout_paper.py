from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cartridges.cutout_paper import render_scene
from cartridges.cutout_paper.__main__ import with_aspect
from cartridges.cutout_paper.assets_procedural import emu, supplied
from cartridges.cutout_paper.foley import pcm
from cartridges.cutout_paper.paper_fx import cutout, tabletop
from cartridges.cutout_paper import renderer
from cartridges.cutout_paper.renderer import PARALLAX, camera_shake, drawing_step, impact_frame
from core.registry import discover, get_plugin
from core.schema import AudioTrackSpec, EpisodeSpec, SceneTiming, WordTiming

ROOT = Path(__file__).resolve().parents[1]


def test_new_cartridge_is_discovered_alongside_crayon_book():
    discover()
    assert get_plugin("style", "cutout_paper") is render_scene
    assert get_plugin("style", "crayon_book").__module__ == "plugins.styles.crayon_book"
    spec = EpisodeSpec.from_json((ROOT / "examples" / "guerra_dos_emus.json").read_bytes())
    assert len(spec.scenes) == 5 and spec.fps == 30 and spec.tts_locale == "pt-BR"
    for aspect, size in (("9:16", (1080, 1920)), ("16:9", (1920, 1080))):
        variant = with_aspect(spec, aspect)
        assert (variant.width, variant.height) == size


def test_drawing_grid_and_two_frame_impact_agree_at_30_fps():
    assert [drawing_step(frame, 30) for frame in range(10)] == [0, 0, 0, 1, 1, 2, 2, 2, 3, 3]
    assert PARALLAX == (.3, 1.0, 1.2)
    hit = impact_frame(.83, 30, 300)
    assert hit == 25 and drawing_step(hit, 30) == 10
    assert [frame for frame in range(hit - 2, hit + 4) if camera_shake(frame, hit, 540) != (0, 0)] == [25, 26]


def test_fibers_torn_edge_and_pivots_change_the_appearance():
    assert tabletop((160, 120), "kraft", 7) is tabletop((160, 120), "kraft", 7)
    assert np.asarray(tabletop((160, 120), "kraft", 7))[:, :, 0].std() > 3
    artwork = Image.new("RGBA", (50, 50), "#4b5043")
    piece = cutout(artwork)
    center = piece.width // 2
    assert piece.getpixel((center, center))[3] == 255
    assert piece.getpixel((center, (piece.height - artwork.height) // 2 - 1))[:3] == (250, 244, 231)
    assert emu(96, 0).tobytes() != emu(96, 3).tobytes()


@pytest.mark.parametrize("width,height,aspect", [(180, 320, "9:16"), (320, 180, "16:9")])
def test_style_yields_rgb_frames_and_syncs_foley_to_visible_impact(tmp_path, width, height, aspect):
    spec = EpisodeSpec("Mapa", "cutout_paper", aspect_ratio=aspect, width=width, height=height, fps=30,
                       scenes=[{"id": "test", "plugin": "cutout_paper", "narration": "Em dois passos.",
                                "layout": "custom", "stamp": "1932", "stamp_at_sec": .09,
                                "cutouts": [{"asset": "mapa", "layer": "mid", "motion": "pop"}]}])
    svg = tmp_path / "map.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
                   '<rect width="40" height="20" fill="#78916e"/></svg>')
    assert supplied(svg, 60, svg.stat().st_mtime_ns).mode == "RGBA"
    words = (WordTiming("Em", .05, .10), WordTiming("dois", .12, .22), WordTiming("passos", .25, .51))
    timing = SceneTiming(AudioTrackSpec(tmp_path / "voice.wav", words, samples=26000), 0, 18, 30)
    rendered = render_scene(spec, spec.scenes[0], timing, {"mapa": svg})
    frames = list(rendered.frames)
    assert len(frames) == 18 and all(len(frame) == 3 * width * height for frame in frames)
    hit = next(effect for effect in rendered.effects if effect.path.stem == "stamp_hit")
    assert hit.at_sec == impact_frame(.09, 30, 18) / 30
    assert frames[round(hit.at_sec * 30) - 1] != frames[round(hit.at_sec * 30)]
    stamp_pcm = pcm("stamp_hit")
    assert stamp_pcm[0] == stamp_pcm[-1] == 0 and np.max(np.abs(stamp_pcm)) > 2000


def test_emu_caption_blocks_keep_short_numbers_and_complete_phrases():
    spec = EpisodeSpec.from_json((ROOT / "examples" / "guerra_dos_emus.json").read_bytes())
    for scene, number in zip(spec.scenes, ("1932", "20.000", "10.000", None, "986"), strict=True):
        options = renderer.Options.model_validate(scene.options)
        blocks = list(renderer._caption_blocks(scene.narration, options.caption_numbers, 49))
        assert all(renderer.word_key(text.split()[-1]) not in renderer.DANGLING for _, text in blocks)
        if number:
            assert any(number in text for _, text in blocks)
    assert renderer._caption_tokens("Em mil novecentos e trinta e dois, a missão chegou.", {
        "mil novecentos e trinta e dois": "1932",
    })[:3] == [(0, "Em"), (1, "1932,"), (7, "a")]


def test_emu_portrait_art_and_overlays_respect_separate_bands():
    stage = renderer.Stage((540, 960))
    spec = EpisodeSpec.from_json((ROOT / "examples" / "guerra_dos_emus.json").read_bytes())

    def within(name, bbox):
        x0, y0, x1, y1 = bbox
        left, top, right, bottom = stage.band(name)
        assert left <= x0 < x1 <= right and top <= y0 < y1 <= bottom, (name, bbox)

    for index, (scene, beat) in enumerate(zip(spec.scenes, (26, 44, 52, 41, 89), strict=True)):
        options = renderer.Options.model_validate(scene.options)
        for image, (x, y) in renderer._portrait_header(stage, options):
            within("header", (x, y, x + image.width, y + image.height))
        for age in range(7):
            image, (x, y) = renderer._portrait_stamp(stage, options.stamp, age, index)
            within("stamp", (x, y, x + image.width, y + image.height))
        audio = AudioTrackSpec(ROOT / "voice.wav", samples=460000)
        for _, image, (x, y) in renderer._captions(stage, scene.narration, audio, options.caption_numbers):
            within("caption", (x, y, x + image.width, y + image.height))

        layers = renderer._stage_frame(stage, options, tuple(stage.blank() for _ in range(3)), [], beat, 10, 10)
        zoom, x, y = renderer._camera(options.camera, beat / 12, 10, stage.unit)
        motion = 1 + (zoom - 1) * .32, x * .55, y * .49
        for pulse in (0, 1, 2):
            for layer, depth in zip(layers, PARALLAX, strict=True):
                shake = camera_shake(pulse, 0, stage.unit)
                bbox = renderer._view(layer, motion, depth, shake).getbbox()
                if bbox:
                    within("action", bbox)


def test_emu_peck_lowers_the_head_without_shifting_the_feet():
    upright = emu(180, 1, "look")
    peck = emu(180, 26, "look")
    assert upright.size == peck.size
    assert peck.getbbox()[1] > upright.getbbox()[1] + 14
    assert peck.getbbox()[3] == upright.getbbox()[3]
    assert emu(180, 1, "proud").tobytes() != emu(180, 8, "proud").tobytes()

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from cartridges.cutout_paper import magazine, renderer
from cartridges.cutout_paper.paper_fx import tape_card_corner
from cartridges.motion_editorial.renderer import TEMPLATE, Options, composition_payload
from core import audio_aligner, cutout_matting
from core.engine import _picture_audit
from core.encoder import run_ffmpeg
from core.html_adapter import open_composition
from core.media_fetcher import clean_author, media_credit, write_media_credits
from core.motion import ease_out_back, visual_cues
from core.qc_gate import caption_errors, monotony_check, qc_gate
from core.schema import AudioTrackSpec, EpisodeSpec, SceneRender, SceneTiming, WordTiming


def test_only_explicit_commercial_matting_checkpoints_are_allowed(tmp_path):
    assert cutout_matting.MODEL_LICENSES == {"birefnet-general-lite": "MIT", "ZhengPeng7/BiRefNet": "MIT"}
    for forbidden in ("bria-rmbg", "RMBG-2.0", "unreviewed-model"):
        info = PngImagePlugin.PngInfo()
        info.add_text("matting_model", forbidden)
        info.add_text("matting_license", "MIT")
        png = tmp_path / "unapproved.png"
        Image.new("RGBA", (8, 8)).save(png, pnginfo=info)
        with pytest.raises(ValueError, match="aprovado"):
            cutout_matting.matting_model(png)
        with pytest.raises(ValueError, match="allowlist"):
            _picture_audit([SceneRender((), matting_models=(forbidden,))], ())


def test_alignment_and_qc_reject_orphans_and_mid_sentence_cuts():
    scene_texts = ("A rede cresceu nos Estados Unidos.", "Em 1989, uma rede ligou o mundo!")
    spec = EpisodeSpec("Guardrail", "motion_editorial", fps=30, scenes=[
        {"id": str(i), "plugin": "motion_editorial", "narration": text} for i, text in enumerate(scene_texts)
    ])
    words = tuple(WordTiming(token, i * .22, i * .22 + .17) for i, token in enumerate(" ".join(scene_texts).split()))
    candidates = audio_aligner._boundaries(words, 30, 120)
    (boundary,) = audio_aligner._script_cuts(spec.scenes, words, candidates, 120, 30)
    assert boundary.sentence and words[boundary.word_index].text == "Em"
    bad_spec = [type(spec.scenes[0])("0", "motion_editorial", "A rede cresceu nos Estados"),
                type(spec.scenes[0])("1", "motion_editorial", "Unidos. Em 1989, uma rede ligou o mundo!")]
    with pytest.raises(ValueError, match="precisa terminar"):
        audio_aligner._script_cuts(bad_spec, words, candidates, 120, 30)
    report = []
    for index, chunk in enumerate((words[:boundary.word_index], words[boundary.word_index:])):
        blocks = audio_aligner.caption_blocks(chunk)
        report.append({"id": str(index), "end_sec": 4, "words": [{"word": word.text} for word in chunk],
                       "caption_blocks": [{"text": block.text} for block in blocks]})
    assert caption_errors(report) == (0, [])
    report[0]["words"][-1]["word"] = "Unidos"
    report[0]["caption_blocks"].append({"text": "Unidos."})
    orphans, boundaries = caption_errors(report)
    assert orphans == 1 and boundaries[0]["at_sec"] == 4


@pytest.mark.parametrize(("raw", "expected"), [
    ("Unknown author Unknown author", "Unknown author"),
    ("<a>Maria Alves</a> <span>Maria Alves</span>", "Maria Alves"),
    ("João JOÃO João Almeida, Ana Campos", "João Almeida, Ana Campos"),
])
def test_credit_deduplicates_provider_text_and_existing_cache(tmp_path, raw, expected):
    assert clean_author(raw).casefold() == expected.casefold()
    photo = tmp_path / "media.jpg"
    credit = {"title": "Arquivo", "source": "Wikimedia Commons", "author": raw, "url": "https://commons.wikimedia.org/"}
    photo.with_suffix(".json").write_text(json.dumps(credit), encoding="utf-8")
    assert media_credit(photo)["author"].casefold() == expected.casefold()
    report = tmp_path / "fontes.md"
    write_media_credits(report, [credit])
    assert f" · {expected}".casefold() in report.read_text().casefold()
    assert " ".join(raw.split()).casefold() not in report.read_text().casefold()


def test_depth_title_keeps_exact_geometry_during_every_camera_cue(tmp_path):
    photo, subject = tmp_path / "photo.png", tmp_path / "subject.png"
    Image.new("RGB", (80, 96), "#795e40").save(photo)
    Image.new("RGBA", (80, 96), "#d08a41aa").save(subject)
    options = Options(kicker="ARQUIVO", headline="A fotografia", module="subject_depth_title", media_query="Foto")
    timing = SceneTiming(AudioTrackSpec(tmp_path / "voice.wav", samples=480000), 0, 300, 30)
    payload = composition_payload("a", options, timing, {"photo": "/_media/photo.png", "subject": "/_media/subject.png"})
    assets = {photo.name: photo, subject.name: subject}
    for width, height in ((180, 320), (320, 180)):
        with open_composition(TEMPLATE, width=width, height=height, payload=payload, assets=assets) as capture:
            frames = [capture(at, timing.progress(at)) for at in (0, 2.87, 5.83, 8.60)]
        assert len(set(frames)) == 4
    mock = tmp_path / "broken"
    shutil.copytree(TEMPLATE.parent, mock)
    stylesheet = mock / "styles.css"
    with stylesheet.open("a", encoding="utf-8") as stream:
        stream.write("\n.depth-subject { object-position: 67% 20% !important; }\n")
    with pytest.raises(Exception, match="bg_transform != fg_transform"):
        with open_composition(mock / TEMPLATE.name, width=180, height=320, payload=payload, assets=assets):
            pass


def test_tape_fits_only_a_card_corner_without_touching_silhouette(monkeypatch):
    canvas = Image.new("RGBA", (180, 320))
    card = (16, 100, 95, 192)
    assert tape_card_corner(canvas, card, 30, blocked=((19, 103, 66, 150),), seed=1) is None
    assert canvas.getbbox() is None
    bounds = tape_card_corner(canvas, card, 30, blocked=((101, 92, 150, 200),), seed=1)
    assert bounds and bounds[2] < 101

    def forbidden(*_args, **_kwargs):
        pytest.fail("Uma silhueta recebeu fita adesiva")

    monkeypatch.setattr(magazine, "tape_card_corner", forbidden)
    texture = Image.new("RGBA", (60, 92), "#292929")
    prepared = magazine.Magazine((12, 86, 170, 206), (magazine.Piece((texture,), None),), (), None)
    first = Image.new("RGBA", canvas.size)
    second = first.copy()
    magazine.draw_magazine(first, prepared, "photo_cutout", 0.0, 9, renderer._center_piece)
    magazine.draw_magazine(second, prepared, "photo_cutout", 1 / 30, 9, renderer._center_piece)
    assert first.getbbox() and first.tobytes() != second.tobytes()


def test_monotony_ignores_captions_but_detects_motion_at_an_exact_time():
    scenes = [{"id": "v", "plugin": "cutout_paper", "start_sec": 0.0, "end_sec": 9.0}]
    assert visual_cues(10) == (2.75, 5.5, 8.25)
    assert 1.03 <= max(ease_out_back(x / 1000) for x in range(1001)) <= 1.05

    def frames():
        for i in range(54):
            frame = np.full((160, 90, 3), 60, dtype=np.uint8)
            frame[117:128, 14:60] = i * 4
            if i < 18 or i >= 36:
                x = 15 + i % 18
                frame[50:89, x:x + 26] = (225, 142, 80)
            yield i / 6, frame

    stats, dead, opening, thumbnails = monotony_check(frames(), scenes)
    assert len(stats) == 3 and [(item["start_sec"], item["end_sec"]) for item in dead] == [(3.0, 6.0)]
    assert opening is not None and thumbnails[0] is not None


def test_real_qc_fails_black_frozen_silent_video_and_still_saves_the_grid(tmp_path):
    video = tmp_path / "black.mp4"
    run_ffmpeg("-v", "error", "-f", "lavfi", "-i", "color=black:s=160x90:r=30:d=3.5", "-f", "lavfi",
               "-i", "anullsrc=r=48000:cl=stereo:d=3.5", "-c:v", "libx264", "-c:a", "aac", str(video))
    scene = {"id": "black", "start_sec": 0.0, "end_sec": 3.5, "words": [], "caption_blocks": []}
    report_path, grid = tmp_path / "qa.json", tmp_path / "qa_grid.png"
    result = qc_gate(video, [scene], 3.5, report_path=report_path, grid_path=grid)
    assert not result["passed"] and not result["frame_0_nonempty"]
    assert all(result["ffmpeg_intervals"][kind] for kind in ("black", "freeze", "silence"))
    assert result["lufs"] is None and grid.is_file() and report_path.is_file()

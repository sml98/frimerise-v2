import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from cartridges.cutout_paper import magazine, renderer
from cartridges.cutout_paper.paper_fx import tape_card_corner
from cartridges.motion_editorial.renderer import TEMPLATE, Options, composition_payload, prepare_media
from core import audio_aligner, broll_selector, cutout_matting, media_fetcher
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


@pytest.fixture(scope="module")
def test_reel(tmp_path_factory):
    folder = tmp_path_factory.mktemp("broll")
    video = folder / "film.mp4"
    run_ffmpeg("-v", "error", "-filter_complex_threads", "1",
               "-f", "lavfi", "-i", "smptebars=s=160x90:r=30:d=2.5",
               "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=3.1",
               "-f", "lavfi", "-i", "color=black:s=160x90:r=30:d=2.4",
               "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=3.2,hue=h=135",
               "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[out]", "-map", "[out]",
               "-c:v", "libx264", "-threads", "1", str(video))
    return video


def test_broll_detects_shots_rejects_black_and_static_titles_and_caches_them(test_reel, tmp_path, monkeypatch):
    index = tmp_path / "index"
    monkeypatch.setenv("FRIMERISE_BROLL_INDEX", str(index))
    folder, shots = broll_selector.index_shots(test_reel)
    assert len(shots) == 2
    assert [(s.start_sec, s.end_sec) for s in shots] == [(2.5, 5.6), (8.0, 11.2)]
    assert all((folder / shot.frame).is_file() for shot in shots)
    (manifest,) = index.glob("*.json")
    assert len(json.loads(manifest.read_text())["shots"]) == 2

    def unexpected(*_):
        pytest.fail("O mesmo vídeo foi indexado de novo")

    monkeypatch.setattr(broll_selector, "_index", unexpected)
    assert broll_selector.index_shots(test_reel) == (folder, shots)


def test_broll_cpu_fallback_and_semantic_ranking_keep_cuts_exact(test_reel, tmp_path, monkeypatch):
    monkeypatch.setenv("FRIMERISE_BROLL_INDEX", str(tmp_path))

    def unavailable(*_):
        raise ImportError

    monkeypatch.setattr(broll_selector, "_semantic_scores", unavailable)
    quality = broll_selector.select_shot(test_reel, "Filmagem de arquivo", duration_sec=2.4, device="cpu")
    assert quality.method == "visual_cpu" and quality.device == "cpu" and quality.model is None
    assert quality.shot_start_sec <= quality.start_sec < quality.end_sec <= quality.shot_end_sec

    def score(_folder, shots, visual, narration, device):
        assert len(shots) == 2 and visual == "Pessoas e uma central" and narration == "O sinal chegou."
        assert device == "cuda"
        return [.15, .83]

    monkeypatch.setattr(broll_selector, "_semantic_scores", score)
    choice = broll_selector.select_shot(test_reel, "Pessoas e uma central", "O sinal chegou.", duration_sec=12, device="cuda")
    assert tuple(choice) == (8.0, 11.2, .83)
    assert choice.method == "siglip2" and choice.model == broll_selector.MODEL


def test_local_media_file_wins_over_search_for_both_cartridges(test_reel, tmp_path, monkeypatch):
    monkeypatch.setenv("FRIMERISE_MEDIA_CACHE", str(tmp_path / "media"))
    monkeypatch.setenv("FRIMERISE_BROLL_INDEX", str(tmp_path / "index"))
    monkeypatch.setenv("FRIMERISE_GRADE_CACHE", str(tmp_path / "grade"))
    monkeypatch.setattr(broll_selector, "scoring_device", lambda *_: "visual")

    def forbidden(*_args, **_kwargs):
        pytest.fail("media_file acionou o provedor remoto")

    monkeypatch.setattr(media_fetcher, "_fetch", forbidden)
    photo = tmp_path / "Flow camisa.png"
    Image.new("RGBA", (35, 70), (87, 37, 20, 140)).save(photo)
    with media_fetcher.capture_media_credits(assets_root=tmp_path) as credits:
        image = media_fetcher.fetch_image("busca ignorada", media_file=photo.name)
        with Image.open(image) as loaded:
            assert loaded.getchannel("A").getextrema() == (140, 140)
        opts = Options(kicker="CAPA", headline="Arquivo local", module="terminal_code", data={"lines": ["ARQUIVO"]},
                       media_file=str(test_reel), broll_query="arquivo remoto")
        editorial = prepare_media(opts, "O arquivo apareceu.")
        assert editorial.video and editorial.selection["method"] == "visual_cpu"
        assert editorial.video_lut == "editorial_teal_amber"
        spec = EpisodeSpec("Papel", "cutout_paper", width=180, height=320, aspect_ratio="9:16", fps=30,
                           scenes=[{"id": "flow", "plugin": "cutout_paper", "narration": "O arquivo apareceu.",
                                    "media_file": str(test_reel), "media_query": "busca ignorada"}])
        timing = SceneTiming(AudioTrackSpec(tmp_path / "voice.wav", samples=48000), 0, 30, 30, continuous=True)
        paper = renderer.render_scene(spec, spec.scenes[0], timing, {})
        shot = list(paper.frames)
        assert len(shot) == 30 and shot[0] != shot[-1] and paper.color_luts == ("vintage_paper_warm",)
        assert paper.broll_selection["source"] == test_reel.name
    assert all(entry["source"] == "Arquivo local (Google Flow / próprio)" for entry in credits)
    report = tmp_path / "fontes.md"
    write_media_credits(report, credits)
    assert "Arquivo local (Google Flow / próprio)" in report.read_text()


class DirectorProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, system, prompt, *, max_tokens):
        from core.director import Usage

        self.requests.append((system, prompt, max_tokens))
        return json.dumps(next(self.responses), ensure_ascii=False), Usage(17, 13, 30)


DIRECTOR_NARRATION = (
    "O correio atravessou a serra ao fim da tarde. Na manhã seguinte, cartas e jornais chegaram juntos à estação central.",
    "A equipe abriu as malas junto aos trilhos. Dentro delas, encontrou os registros das viagens e o mapa do percurso.",
)


def _director_response(first, second):
    return {"title": "O correio da serra", "scenes": [
        dict(headline="O percurso", **first), dict(headline="O arquivo", **second),
    ]}


@pytest.mark.parametrize(("cartridge", "visuals", "modules"), [
    ("cutout_paper", ({"module": "photo_cutout", "media_query": "rail mail carriage"},
                      {"module": "newspaper_clipping", "media_query": "rail post office", "clipping_headline": "O mapa"}),
     ("photo_cutout", "newspaper_clipping")),
    ("motion_editorial", ({"module": "quote_callout", "kicker": "Arquivo", "data": {"quote": "A estação"}},
                           {"module": "news_headline", "kicker": "Imprensa",
                            "data": {"headline": "O mapa", "source": "Arquivo", "excerpt": "O mapa",
                                     "highlight": "mapa"}}),
     ("quote_callout", "news_headline")),
    ("crayon_book", ({"background": "mountains"}, {"background": "workshop"}), ("mountains", "workshop")),
])
def test_director_topic_uses_only_the_selected_skill(monkeypatch, cartridge, visuals, modules):
    from core import director

    loaded = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "SKILL.md":
            loaded.append(path.relative_to(director.ROOT).as_posix())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    provider = DirectorProvider(_director_response(*[
        dict(narration=text, **visual) for text, visual in zip(DIRECTOR_NARRATION, visuals, strict=True)
    ]))
    plan = director.plan_episode(cartridge, "9:16", topic="A mala postal", duration=18, provider=provider)
    assert loaded == [director.direction_for(cartridge).skill]
    assert plan.modules == modules
    assert [scene.narration for scene in plan.episode.scenes] == list(DIRECTOR_NARRATION)
    assert (plan.episode.width, plan.episode.height, plan.episode.fps) == (1080, 1920, 30)
    assert plan.episode.tts_locale == "pt-BR" and plan.episode.audio_mode == "continuous"
    assert "145 palavras/min" in provider.requests[0][1]
    assert plan.usage == director.Usage(17, 13, 30)


@pytest.mark.parametrize("issue", ["unknown", "missing", "repeat"])
def test_director_validates_renderer_options_and_repairs_once(issue):
    from core import director

    first = {"module": "photo_cutout", "media_query": "rail mail carriage", "narration": DIRECTOR_NARRATION[0]}
    second = {"module": "newspaper_clipping", "media_query": "rail post office",
              "clipping_headline": "O mapa", "narration": DIRECTOR_NARRATION[1]}
    invalid = {
        "unknown": {**first, "module": "invalid"},
        "missing": {**second, "clipping_headline": ""},
        "repeat": {**first, "narration": DIRECTOR_NARRATION[1]},
    }[issue]
    bad = _director_response(first, invalid)
    good = _director_response(first, second)
    provider = DirectorProvider(bad, good)
    plan = director.plan_episode("cutout_paper", "16:9", topic="A mala postal", duration=18, provider=provider)
    assert len(provider.requests) == 2
    assert "falha de validação" in provider.requests[1][1]
    assert plan.usage == director.Usage(34, 26, 60)

    provider = DirectorProvider(bad, bad)
    with pytest.raises(director.DirectorError, match="após um reparo"):
        director.plan_episode("cutout_paper", "16:9", topic="A mala postal", duration=18, provider=provider)
    assert len(provider.requests) == 2


def test_produce_audio_keeps_the_original_source_and_transcribes_only_once(tmp_path, monkeypatch, capsys):
    import sys

    import frimerise
    from core import director

    audio = tmp_path / "locucao.wav"
    run_ffmpeg("-v", "error", "-f", "lavfi", "-i", "sine=duration=14", str(audio))
    words = tuple(WordTiming(word, start + index * .4, start + index * .4 + .24)
                  for start, sentence in ((.4, "O correio chegou cedo."), (7.2, "O jornal trouxe notícias."))
                  for index, word in enumerate(sentence.split()))
    calls = []

    def transcribe(path, locale, *, initial_prompt):
        calls.append(path)
        assert locale == "pt-BR" and initial_prompt is None
        return words

    monkeypatch.setattr(audio_aligner, "transcribe_words", transcribe)
    provider = DirectorProvider(_director_response(
        {"module": "photo_cutout", "media_query": "rail mail carriage"},
        {"module": "newspaper_clipping", "clipping_headline": "As notícias", "media_query": "archive newspaper"},
    ))
    monkeypatch.setitem(director.PROVIDERS, "deepseek", lambda: provider)

    def render(spec, output, *, audio, aligned_timeline, audio_report, **flags):
        assert audio == tmp_path / "locucao.wav"
        assert [scene.narration for scene in spec.scenes] == ["O correio chegou cedo.", "O jornal trouxe notícias."]
        assert aligned_timeline[0].end_sec == aligned_timeline[1].start_sec
        assert aligned_timeline[1].end_sec == 14 and aligned_timeline[0].audio.path.is_file()
        assert flags["target_duration_sec"] is None and flags["continuous"]
        assert flags["public_domain_only"]
        qc = output.with_name("episodio_qc")
        qc.mkdir()
        (qc / "relatorio_qc_blindagem.json").write_text('{"passed": true, "monotony_windows_failed": 0, "fps": 30}')
        Image.new("RGB", (8, 8)).save(qc / "qa_grid.png")
        audio_report.write_text('{"lufs": -14, "matting_license": "MIT"}')

    monkeypatch.setattr(frimerise, "render", render)
    dest = tmp_path / "exports"
    monkeypatch.setattr(sys, "argv", ["frimerise", "produce", "--audio", str(audio), "--cartridge",
                                      "cutout_paper", "--aspect", "9:16", "--output-dir", str(dest)])
    frimerise.main()
    assert len(calls) == 1 and len(provider.requests) == 1
    assert "O correio chegou cedo." in provider.requests[0][1]
    assert "omita narration" in provider.requests[0][1]
    report = json.loads((dest / "relatorio_director.json").read_bytes())
    assert report["qc_passed"] and report["total_tokens"] == 30 and report["lufs"] == -14
    assert report["input_mode"] == "audio" and report["provider"] == "deepseek"
    assert (dest / "episodio_gerado.json").is_file() and (dest / "qa_grid.png").is_file()
    assert "Renderizando 2 cenas" in capsys.readouterr().out


def test_director_plan_only_and_edited_local_media(tmp_path, monkeypatch):
    import sys

    import frimerise
    from core import director

    provider = DirectorProvider(_director_response(
        {"module": "photo_cutout", "media_query": "rail mail carriage", "narration": DIRECTOR_NARRATION[0]},
        {"module": "newspaper_clipping", "media_query": "station", "clipping_headline": "O mapa", "narration": DIRECTOR_NARRATION[1]},
    ))
    monkeypatch.setitem(director.PROVIDERS, "deepseek", lambda: provider)
    monkeypatch.setattr(frimerise, "render", lambda *args, **kwargs: pytest.fail("plan-only renderizou"))
    dest = tmp_path / "exports"
    monkeypatch.setattr(sys, "argv", ["frimerise", "produce", "--topic", "Correio", "--duration", "18", "--cartridge",
                                      "cutout_paper", "--aspect", "16:9", "--output-dir", str(dest), "--plan-only"])
    frimerise.main()
    (script,) = dest.iterdir()
    assert script.name == "episodio_gerado.json"
    data = json.loads(script.read_bytes())
    data["scenes"][0]["options"]["media_file"] = "local.png"
    script.write_text(json.dumps(data))
    Image.new("RGB", (12, 12)).save(tmp_path / "local.png")
    monkeypatch.setattr(sys, "argv", ["frimerise", "produce", "--episode", str(script), "--assets-root",
                                      str(tmp_path), "--output-dir", str(dest), "--plan-only"])
    frimerise.main()
    assert len(provider.requests) == 1
    assert EpisodeSpec.from_json(script.read_bytes()).scenes[0].options["media_file"] == "local.png"
    (tmp_path / "local.png").unlink()
    with pytest.raises(director.DirectorError, match="media_file precisa"):
        director.validate_episode(EpisodeSpec.from_json(script.read_bytes()), assets_root=tmp_path)


def test_deepseek_credentials_are_read_privately_from_environment_or_settings(tmp_path, monkeypatch, capsys, caplog):
    import io
    import sqlite3
    from urllib.error import HTTPError

    from core import director

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    db = tmp_path / "frimerise.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE settings(key text, value text)")
        connection.execute("INSERT INTO settings VALUES (?, ?)", ("deepseek_api_key", "db-test-secret"))
    monkeypatch.setenv("FRIMERISE_DB", str(db))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    requests = []

    def fail(request, **kwargs):
        requests.append(request)
        payload = json.loads(request.data)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        raise HTTPError(request.full_url, 401, request.get_header("Authorization"), {}, io.BytesIO(b"private body"))

    monkeypatch.setattr(director, "urlopen", fail)
    with pytest.raises(director.DirectorError, match="HTTP 401") as error:
        director.DeepSeek().complete("JSON", "Plano", max_tokens=100)
    assert requests[-1].get_header("Authorization") == "Bearer db-test-secret"
    assert b"db-test-secret" not in requests[-1].data and "db-test-secret" not in str(error.value)
    assert error.value.__suppress_context__

    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-test-secret")

    def echo(request, **kwargs):
        assert request.get_header("Authorization") == "Bearer env-test-secret"
        reply = {"usage": {"prompt_tokens": 17, "completion_tokens": 13, "total_tokens": 30},
                 "choices": [{"message": {"content": '{"title":"env-test-secret"}'}}]}
        return io.StringIO(json.dumps(reply))

    monkeypatch.setattr(director, "urlopen", echo)
    with pytest.raises(director.DirectorError, match="credencial") as error:
        director.DeepSeek().complete("JSON", "Plano", max_tokens=100)
    captured = capsys.readouterr()
    assert "env-test-secret" not in str(error.value) + captured.out + captured.err + caplog.text


@pytest.mark.parametrize("working_source", ("local", "studio"))
def test_deepseek_checks_default_database_paths_after_an_invalid_env_key(tmp_path, monkeypatch, working_source):
    import io
    import sqlite3
    from urllib.error import HTTPError

    from core import director

    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("FRIMERISE_DB", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "invalid-env-key")
    studio = home / "frimerise-cinema-studio" / "data" / "frimerise.db"
    studio.parent.mkdir(parents=True)
    for db, value in (
        (tmp_path / "frimerise.db", "local-key" if working_source == "local" else "invalid-env-key"),
        (studio, "studio-key"),
    ):
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE settings(key text, value text)")
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("DEEPSEEK_API_KEY", value))
    headers = []
    reply = {"usage": {"prompt_tokens": 18, "completion_tokens": 14, "total_tokens": 32},
             "choices": [{"message": {"content": '{"title":"Novo"}'}}]}

    def respond(request, **kwargs):
        header = request.get_header("Authorization")
        headers.append(header)
        if header == "Bearer invalid-env-key":
            raise HTTPError(request.full_url, 401, "unauthorized", {}, io.BytesIO())
        return io.StringIO(json.dumps(reply))

    monkeypatch.setattr(director, "urlopen", respond)
    _, usage = director.DeepSeek().complete("JSON", "Plano", max_tokens=100)
    assert usage.total_tokens == 32
    assert headers == ["Bearer invalid-env-key", f"Bearer {working_source}-key"]


@pytest.mark.parametrize(("failure", "reason"), [
    ("missing", "Credencial DeepSeek ausente"),
    ("rejected", "HTTP 401"),
    ("timeout", "Falha na conexão"),
    ("zero_tokens", "contagem de tokens válida"),
])
def test_produce_topic_stops_without_using_previous_episode(tmp_path, monkeypatch, capsys, failure, reason):
    import io
    import sys
    from urllib.error import HTTPError

    import frimerise
    from core import director

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("FRIMERISE_DB", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    if failure != "missing":
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-topic")

    def respond(request, **kwargs):
        if failure == "missing":
            pytest.fail("O diretor chamou a API sem credencial")
        if failure == "rejected":
            raise HTTPError(request.full_url, 401, "test-secret-topic", {}, io.BytesIO())
        if failure == "timeout":
            raise TimeoutError("test-secret-topic")
        content = _director_response(
            {"module": "photo_cutout", "media_query": "rail mail carriage", "narration": DIRECTOR_NARRATION[0]},
            {"module": "newspaper_clipping", "media_query": "rail archive", "clipping_headline": "O mapa",
             "narration": DIRECTOR_NARRATION[1]},
        )
        response = {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "choices": [{"message": {"content": json.dumps(content)}}]}
        return io.StringIO(json.dumps(response))

    monkeypatch.setattr(director, "urlopen", respond)
    monkeypatch.setattr(frimerise, "render", lambda *args, **kwargs: pytest.fail("Renderizou sem plano do provedor"))
    destination = tmp_path / "result"
    destination.mkdir()
    stale = {"episodio_gerado.json": '{"title":"A Guerra dos Emus"}', "relatorio_director.json": '{"total_tokens":0}'}
    for name, data in stale.items():
        (destination / name).write_text(data)
    monkeypatch.setattr(sys, "argv", ["frimerise", "produce", "--topic", "A história do vampiro de 1725", "--duration",
                                      "18", "--cartridge", "cutout_paper", "--aspect", "9:16",
                                      "--output-dir", str(destination)])
    with pytest.raises(SystemExit) as error:
        frimerise.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "produce --topic:" in captured.err and reason in captured.err
    assert "test-secret-topic" not in captured.out + captured.err
    assert {path.name: path.read_text() for path in destination.iterdir()} == stale


def test_produce_media_policy_does_not_reuse_cache_with_a_different_license(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIMERISE_MEDIA_CACHE", str(tmp_path))
    restrictive = media_fetcher.Candidate(("ignored",), "page", "Author", "CC BY-SA 4.0", "Commons", ".jpg")
    open_asset = media_fetcher.Candidate(("ignored",), "page", "Author", "Public domain", "Commons", ".jpg")
    monkeypatch.setattr(media_fetcher, "_commons", lambda *args: iter((restrictive, open_asset)))

    def checked(candidate, stage, *args):
        image = stage / "asset.jpg"
        Image.new("RGB", (12, 12)).save(image)
        return image

    monkeypatch.setattr(media_fetcher, "_checked_asset", checked)
    freely_licensed = media_fetcher.fetch_image("rail", "wikimedia")
    with media_fetcher.capture_media_credits(public_domain_only=True):
        domain = media_fetcher.fetch_image("rail", "wikimedia")
    assert domain != freely_licensed
    assert media_credit(freely_licensed)["license"] == "CC BY-SA 4.0"
    assert media_credit(domain)["license"] == "Public domain"


def test_cutout_custom_archive_illustration_keeps_the_complete_image(tmp_path, monkeypatch):
    from core import director

    original = tmp_path / "gravura.png"
    Image.new("RGB", (80, 60), "#ad7070").save(original)
    options = {"layout": "custom", "media_query": "railway archive engraving", "media_file": original.name,
               "headline": "O mapa inteiro", "photo_label": "Gravura de arquivo"}
    spec = director._episode("cutout_paper", "9:16", "Ilustrações", [
        {"id": "s01", "plugin": "cutout_paper", "narration": DIRECTOR_NARRATION[0], "options": options},
    ])
    assert director.validate_episode(spec, assets_root=tmp_path) == ("custom",)
    monkeypatch.setattr(media_fetcher, "_fetch", lambda *args, **kwargs: pytest.fail("media_file não teve prioridade"))
    monkeypatch.setattr(magazine, "remove_background", lambda *args: pytest.fail("gravura perdeu o fundo"))
    monkeypatch.setattr(magazine, "grade_photo", lambda source, lut: source)
    timing = SceneTiming(AudioTrackSpec(original, samples=48_000), 0, 30, 30, continuous=True)
    with media_fetcher.capture_media_credits(assets_root=tmp_path):
        scene = renderer.render_scene(spec, spec.scenes[0], timing, None)
        assert next(iter(scene.frames))
    assert scene.color_luts == ("vintage_paper_warm",) and not scene.matting_models


@pytest.mark.parametrize("extension", ("png", "mp4"))
def test_crayon_book_local_media_keeps_the_page_timeline(tmp_path, monkeypatch, extension):
    from core import director
    from plugins.styles import crayon_book

    source = tmp_path / f"flow.{extension}"
    if extension == "png":
        Image.new("RGB", (120, 96), "#c8b34d").save(source)
    else:
        run_ffmpeg("-v", "error", "-f", "lavfi", "-i", "testsrc2=size=120x96:rate=30:duration=0.5",
                   "-pix_fmt", "yuv420p", str(source))
    monkeypatch.setenv("FRIMERISE_MEDIA_CACHE", str(tmp_path / "media"))
    monkeypatch.setenv("FRIMERISE_GRADE_CACHE", str(tmp_path / "grades"))
    monkeypatch.setenv("FRIMERISE_BROLL_INDEX", str(tmp_path / "broll"))
    spec = EpisodeSpec("Arquivo", "crayon_book", width=160, height=90, fps=30, scenes=[
        {"id": "s01", "plugin": "crayon_book", "headline": "Uma página",
         "narration": DIRECTOR_NARRATION[0], "media_file": source.name},
    ])
    expected = "local_photo" if extension == "png" else "local_film"
    assert director.validate_episode(spec, assets_root=tmp_path) == (expected,)
    timing = SceneTiming(AudioTrackSpec(source, samples=24_000), 0, 15, 30, continuous=True)
    with media_fetcher.capture_media_credits(assets_root=tmp_path) as credits:
        scene = crayon_book.render_scene(spec, spec.scenes[0], timing, None)
        frames = list(scene.frames)
    assert len(frames) == timing.frame_count and all(len(frame) == 160 * 90 * 3 for frame in frames)
    assert scene.color_luts == ("doc_clean_neutral",)
    assert credits[0]["source"] == media_fetcher.LOCAL_SOURCE
    if extension == "mp4":
        assert scene.broll_selection["method"] == "local_original"
        assert frames[0] != frames[-1]


def test_playwright_finds_image_text_collision_and_safe_area_overflow(tmp_path):
    page = tmp_path / "layout.html"
    page.write_text("""<!doctype html><style>body{margin:0}.photo,span{position:absolute}
.photo{left:12vw;top:21vh;width:65vw;height:25vh}span{left:13vw;top:24vh}
#unsafe{left:0;top:0}</style><div class=photo data-qc-image="foto"></div>
<span data-qc-text="texto">Colisão visível</span><span id=unsafe data-qc-text="fora">Fora</span>
<script>window.__renderAt=()=>{};</script>""")
    audit = {}
    with open_composition(page, width=360, height=640, payload={}, layout_audit=audit) as capture:
        capture(0, 0)
    assert audit["frames"] == 1
    assert audit["violations"] == [{"at_sec": 0, "type": "collision", "text": "texto", "image": "foto"},
                                    {"at_sec": 0, "type": "unsafe_text", "text": "fora"}]


def test_newspaper_uses_disjoint_zones_in_the_actual_browser(tmp_path, monkeypatch):
    from cartridges.cutout_paper import sequence

    image = tmp_path / "image.png"
    Image.new("RGB", (120, 160), "#c9a477").save(image)
    monkeypatch.setattr(sequence, "fetch_image", lambda *args, **kwargs: image)
    monkeypatch.setattr(sequence, "grade_photo", lambda source, lut: source)
    spec = EpisodeSpec("Arquivo", "cutout_paper", aspect_ratio="9:16", width=1080, height=1920, fps=30,
                       dynamic_edit=True, scenes=[{"plugin": "cutout_paper", "id": "a", "narration": "O jornal saiu.",
                                                   "module": "newspaper_clipping", "headline": "O jornal",
                                                   "clipping_headline": "Um relato em Viena", "media_query": "jornal",
                                                   "clipping_text": "A carta chegou à redação do jornal.",
                                                   "publication": "Arquivo", "highlight": "RELATO",
                                                   "transition": "zoom_punch"}])
    scene = spec.scenes[0]
    timing = SceneTiming(AudioTrackSpec(image, samples=48_000), 0, 15, 30)
    rendered = sequence.render_sequence(spec, scene, timing, renderer.Options.model_validate(scene.options))
    assert len(list(rendered.frames)) == 15
    assert rendered.layout_audit == {"frames": 15, "violations": []}
    assert not renderer.Options().show_header


@pytest.mark.parametrize("action", ("walk_to_target", "inspect_with_lantern", "dig_or_open", "flee_run", "crowd_gather"))
def test_paper_puppet_actions_use_only_image_assets(action, tmp_path, monkeypatch):
    from cartridges.cutout_paper import sequence

    image = tmp_path / "recorte.png"
    actor = Image.new("RGBA", (80, 120))
    actor.paste("#916f45", (20, 20, 60, 110))
    actor.save(image)
    monkeypatch.setattr(sequence, "fetch_image", lambda *args, **kwargs: image)
    monkeypatch.setattr(sequence, "grade_photo", lambda source, lut: source)
    monkeypatch.setattr(sequence, "remove_background", lambda path: pytest.fail("The asset already has transparency"))
    shutil.copy2(sequence.DIRECTORY / "sequence.js", tmp_path)
    template = (sequence.DIRECTORY / "sequence.html").read_text()
    (tmp_path / "sequence.html").write_text(template + """<script>
const drawActor = window.renderAt;
window.renderAt = (...args) => {
  drawActor(...args);
  const actor = document.querySelector("[data-actor]");
  if (actor?.children.length !== 1 || !actor.firstElementChild.matches("img[data-source-asset]"))
    throw new Error("A figure must contain a single image asset");
  if (document.querySelector("[data-joint], .puppet"))
    throw new Error("Procedural figure found");
};
</script>""")
    monkeypatch.setattr(sequence, "DIRECTORY", tmp_path)
    spec = EpisodeSpec("Teatro", "cutout_paper", aspect_ratio="9:16", width=180, height=320, fps=30, scenes=[
        {"plugin": "cutout_paper", "id": "a", "narration": "Dois homens chegaram.",
         "module": "action_cutout_puppet", "action": action, "media_file": image.name},
    ])
    scene = spec.scenes[0]
    timing = SceneTiming(AudioTrackSpec(Path("voice.wav"), samples=48_000), 0, 10, 30)
    rendered = sequence.render_sequence(spec, scene, timing, renderer.Options.model_validate(scene.options))
    frames = list(rendered.frames)
    assert len(set(frames)) > 2
    assert rendered.layout_audit == {"frames": 10, "violations": []}


def test_paper_puppet_requires_a_source_image():
    with pytest.raises(ValueError, match="recortes reais"):
        renderer.Options(module="action_cutout_puppet", head_cutout_file="retrato.png")


def test_macro_caption_surface_and_alignment_are_audited(tmp_path, monkeypatch):
    from cartridges.cutout_paper import sequence
    from core.editing import attach_visual_cues
    from core.engine import _alignment_data
    from core.qc_gate import editorial_checks
    from core.schema import CaptionBlock

    image = tmp_path / "page.png"
    Image.new("RGB", (108, 192), "white").save(image)
    monkeypatch.setattr(sequence, "fetch_image", lambda *args, **kwargs: image)
    monkeypatch.setattr(sequence, "grade_photo", lambda source, lut: source)
    shutil.copy2(sequence.DIRECTORY / "sequence.js", tmp_path)
    template = (sequence.DIRECTORY / "sequence.html").read_text()
    (tmp_path / "sequence.html").write_text(template + """<script>
const drawMacro = window.renderAt;
window.renderAt = (...args) => {
  drawMacro(...args);
  const macro = document.querySelector("#macro");
  const zoom = macro.transform.baseVal.consolidate().matrix.a;
  if (zoom < 2.2 || zoom > 2.6 || !macro.querySelector('rect[fill="url(#macro-shadow)"]'))
    throw new Error("Macro requires a zoomed plate with a vignette");
  const surface = document.querySelector(".surface");
  if (args[0] >= .32 && surface) surface.style.background = "transparent";
};
</script>""")
    monkeypatch.setattr(sequence, "DIRECTORY", tmp_path)
    spec = EpisodeSpec("Arquivo", "cutout_paper", aspect_ratio="9:16", width=180, height=320, fps=30, scenes=[
        {"plugin": "cutout_paper", "id": "s08", "module": "full_bleed_macro",
         "narration": "O jornal diz vampyri.", "visual_anchor": "vampyri", "media_file": image.name},
    ])
    words = (WordTiming("O", 0, .08), WordTiming("jornal", .08, .15),
             WordTiming("diz", .15, .21), WordTiming("vampyri.", np.float64(.21), .45))
    timing = SceneTiming(AudioTrackSpec(image, words, samples=24000), 0, 15, 30, (CaptionBlock(words),), True)
    (timing,) = attach_visual_cues(spec.scenes, [timing])
    scene = spec.scenes[0]
    rendered = sequence.render_sequence(spec, scene, timing, renderer.Options.model_validate(scene.options))
    assert len(list(rendered.frames)) == 15
    audit = rendered.layout_audit
    assert audit["caption_frames"] == 14
    assert any(fault["type"] == "caption_contrast" for fault in audit["violations"])
    metadata = _alignment_data(spec, [timing], [rendered])
    checks, report = editorial_checks(metadata, [None], .5)
    assert checks["audio_visual_anchors"] and not checks["caption_contrast"]
    assert report["audio_visual_anchors"][0]["visual_cue_at_sec"] == 6 / 30
    json.dumps(report, allow_nan=False)
    metadata[0]["visual_cue_at_sec"] = .3
    assert not editorial_checks(metadata, [None], .5)[0]["audio_visual_anchors"]


def test_impact_slam_inhibits_caption_output_and_has_a_measured_duration(monkeypatch):
    from cartridges.cutout_paper import sequence
    from core.schema import CaptionBlock

    spec = EpisodeSpec("Carimbo", "cutout_paper", fps=30, scenes=[
        {"plugin": "cutout_paper", "id": "a", "narration": "Nove mortes.", "module": "impact_slam", "impact_text": "9 MORTES"},
    ])
    scene = spec.scenes[0]
    options = renderer.Options.model_validate(scene.options)
    words = (WordTiming("Nove", .1, .3), WordTiming("mortes.", .4, .6))
    timing = SceneTiming(AudioTrackSpec(Path("voice.wav"), words, samples=144000), 0, 90, 30,
                         (CaptionBlock(words),), True)
    payloads = []
    monkeypatch.setattr(sequence, "render_frames", lambda *args, **kwargs: payloads.append(kwargs["payload"]) or ())
    sequence.render_sequence(spec, scene, timing, options)
    assert scene.captions_hidden and payloads[0]["captions"] == [] and payloads[0]["impact"] == "9 MORTES"
    with pytest.raises(ValueError, match="2 e 3[,.]5"):
        sequence.render_sequence(spec, scene, SceneTiming(timing.audio, 0, 120, 30, continuous=True), options)


def test_authored_pronunciation_keeps_original_words_and_dramatic_pauses(tmp_path, monkeypatch):
    import wave

    from core import tts_engine
    from core.schema import Speech

    original = "Frombald ouviu a família Blagojević."
    changed = "Frômbalt ouviu a família Blagóiévitch."
    overrides = {"Frombald": "Frômbalt", "Blagojević": "Blagóiévitch"}

    def voice(text, **opts):
        assert text == changed
        return Speech(opts["output"], tuple(WordTiming(word, i / 5, (i+1) / 5)
                                            for i, word in enumerate(text.split())), locale="pt-BR")

    monkeypatch.setattr(tts_engine, "get_plugin", lambda *_: voice)
    source = tmp_path / "one_tts_take.wav"
    speech = tts_engine.synthesize_one("edge_kokoro_tts", original, voice_id=None, speech_style=None, output=source,
                                      locale="pt-BR", pronunciation_overrides=overrides)
    assert speech.spoken_text == changed and " ".join(w.text for w in speech.words) == original
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x02\x00" * 96000)
    spec = EpisodeSpec("Pausa", "cutout_paper", fps=30, scenes=[
        {"plugin": "cutout_paper", "id": "first", "narration": original, "pause_after_sec": .5},
        {"plugin": "cutout_paper", "id": "next", "narration": "O relato permanece.", "pause_after_sec": 0},
    ])
    takes = [SceneTiming(AudioTrackSpec(source, (WordTiming("Blagojević.", .7, .8),), samples=48000), 0, 30, 30,
                         continuous=True),
             SceneTiming(AudioTrackSpec(source, (WordTiming("O", .1, .2),), samples=48000, start_sample=48000), 30, 30, 30,
                         continuous=True)]
    edited = audio_aligner.add_authored_pauses(takes, spec.scenes, tmp_path / "pause.wav")
    assert edited[0].duration_sec == 1.5 and edited[1].start_sec == 1.5
    assert edited[0].audio.path == edited[1].audio.path
    assert edited[1].audio.words == takes[1].audio.words
    with wave.open(str(edited[0].audio.path)) as audio:
        audio.setpos(48000)
        assert audio.readframes(24000) == bytes(48000)


def test_grave_edge_voice_receives_unicode_overrides_and_preserves_caption_spelling(tmp_path, monkeypatch):
    import edge_tts

    from core import tts_engine
    from core.director import infer_tone
    from core.registry import discover
    from plugins.tts import edge_kokoro_tts

    original = "Passou pelos Habsburgo. Frombald escreveu Blagojević e vampyri."
    changed = "Passou pelos Rábsburgo. Frômbald escreveu Blagoievítch e vampíri."
    overrides = {"HABSBURGO": "Rábsburgo", "frombald": "Frômbald", "Blagojevic\u0301": "Blagoievítch", "vampyri": "vampíri"}
    calls = []

    class Communicate:
        def __init__(self, text, voice, *, pitch, rate, boundary, receive_timeout):
            self.texts = iter((text,))
            calls.append((text, voice, pitch, rate, boundary))

        def save_sync(self, path, metadata):
            (spoken,) = self.texts
            Path(path).write_bytes(b"fake mp3")
            Path(metadata).write_text("\n".join(json.dumps({"type": "WordBoundary", "text": token,
                                                           "offset": index * 2000000, "duration": 1500000})
                                                 for index, token in enumerate(spoken.split())))

    discover()
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(tts_engine, "get_plugin", lambda *_: edge_kokoro_tts.synthesize)
    speech = tts_engine.synthesize_one("edge_kokoro_tts", original, voice_id=None, speech_style=None,
                                       output=tmp_path / "take", locale="pt-BR", continuous=True,
                                       tone=infer_tone("O vampiro de 1725"), pronunciation_overrides=overrides)
    assert calls == [(changed, "pt-BR-AntonioNeural", "-6Hz", "-4%", "WordBoundary")]
    assert speech.spoken_text == changed and " ".join(word.text for word in speech.words) == original
    assert (speech.voice_id, speech.pitch, speech.rate) == calls[0][1:4]
    assert tts_engine.pronounce("exFrombald, FROMBALD e vampyric.", "pt-BR", overrides) == (
        "exFrombald, Frômbald e vampyric."
    )


def test_studio_profile_defaults_without_changing_legacy_or_explicit_voices():
    from dataclasses import replace

    from core.director import configure_production

    old = EpisodeSpec("Arquivo", "cutout_paper", fps=30,
                      scenes=[{"id": "a", "plugin": "cutout_paper", "narration": "O jornal saiu."}])
    assert configure_production(old) is old
    studio = configure_production(old, "studio_sota")
    assert (studio.tts_plugin, studio.tts_fallback_plugin, studio.tts_locale) == (
        "chatterbox_ptbr", "edge_kokoro_tts", "pt-BR"
    )
    explicit = replace(old, tts_plugin="edge_kokoro_tts", voice_id="pt-BR-FabioNeural")
    assert configure_production(explicit, "studio_sota").voice_id == "pt-BR-FabioNeural"
    with pytest.raises(ValueError, match="30 fps"):
        configure_production(replace(old, fps=24), "studio_sota")


def test_chatterbox_safe_chunks_retries_and_pauses():
    from core.tts_plugins import chatterbox_ptbr as voice

    original = "O Dr. Almeida encontrou o manuscrito. A data era 1725. A notícia atravessou a fronteira."
    chunks = voice.split_chunks(original)
    assert len(chunks) == 2 and " ".join(chunks) == original
    assert all(voice.expected_duration_sec(text) <= 18 for text in chunks)
    assert voice.prosody_for_tone("historical_suspense") == (.65, .38)
    tried = []

    def generate(attempt):
        tried.append(attempt)
        length = int(voice.expected_duration_sec(chunks[0]) * 48_000) if attempt == 2 else 1
        return np.full(length, .12, dtype=np.float32)

    pcm = voice.validated_chunk(generate, chunks[0], 48_000)
    assert tried == [0, 1, 2] and pcm.ndim == 1
    part = np.ones(48_000, np.float32)
    assert voice.stitch_chunks([part, part]).size == 96_000 - 864
    paused = voice.stitch_chunks([part, part], [.45, 0])
    assert np.count_nonzero(paused[48_000:69_600]) == 0
    assert paused.size == 117_600
    ending = "A causa das mortes ficou sem resposta."
    assert chunks[-1] == "A data era 1725. A notícia atravessou a fronteira."
    assert voice._matches_spoken_text(ending, "A causa das mortes ficou sem resposta.")
    assert not voice._matches_spoken_text(ending, "Mas " + ending)
    assert not voice._matches_spoken_text(ending, "A mais é T'Name. " + ending)
    checks = []

    def verify(samples):
        checks.append(samples.size)
        return len(checks) == 2

    voice.validated_chunk(lambda attempt: pcm, ending, 48_000, verify=verify)
    assert len(checks) == 2


def test_editorial_qwen_rejects_low_scores_and_requires_json_schema(tmp_path, monkeypatch):
    from io import BytesIO

    from core import editorial_vlm_qc as editorial

    page = tmp_path / "qa_grid_p1.png"
    Image.new("RGB", (20, 20)).save(page)
    pages = [(page, ("s01",))]
    scenes = [{"id": "s01", "narration": "O jornal saiu.", "captions_expected": False, "suppress_captions": True}]
    monkeypatch.delenv("FRIMERISE_VLM_BACKEND", raising=False)
    assert editorial.audit_editorial(pages, scenes) is None
    assert editorial.audit_editorial(pages, scenes, required=True)["overall_passed"] is False
    assert editorial.audit_editorial(pages, scenes, skip=True)["status"] == "skipped"
    monkeypatch.setenv("FRIMERISE_VLM_BACKEND", "api")
    monkeypatch.setenv("FRIMERISE_VLM_API_BASE", "https://editorial.example")
    monkeypatch.setenv("FRIMERISE_VLM_API_KEY", "synthetic-test-key")

    def complete(request, timeout):
        body = json.loads(request.data)
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] and schema["schema"]["properties"]["scenes"]["maxItems"] == 1
        prompt = body["messages"][1]["content"][1]["text"]
        assert "O jornal saiu." in prompt and "Nas cenas s01, a ausência de legenda de fala foi intencional." in prompt
        scores = {"scenes": [{"scene_id": "s01", "av_match_score": 2, "caption_legibility_score": 5,
                              "visual_craft_score": 4, "issues": ["Imagem de outro local."],
                              "overall_passed": True}], "overall_passed": True}
        return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(scores)}}]}).encode())

    monkeypatch.setattr(editorial, "_urlopen", complete)
    report = editorial.audit_editorial(pages, scenes, required=True)
    assert report["executed"] and report["overall_passed"] is False
    assert report["scenes"][0]["overall_passed"] is False
    assert report["scenes"][0]["model_overall_passed"] is True and report["model_page_verdicts"] == [True]
    assert "synthetic-test-key" not in json.dumps(report)


def test_qwen_schema_tokenizer_uses_core_enforcer_with_transformers_five(monkeypatch):
    import sys
    from types import SimpleNamespace

    from core.editorial_vlm_qc import _schema_tokenizer

    def build(tokens, decode, eos, bitmask, size):
        return tokens, decode, eos, bitmask, size

    monkeypatch.setitem(sys.modules, "lmformatenforcer.tokenenforcer", SimpleNamespace(TokenEnforcerTokenizerData=build))

    class Tokenizer:
        all_special_ids = [2]
        eos_token_id = 2

        def __len__(self):
            return 3

        def encode(self, text, *, add_special_tokens):
            assert text == "0" and not add_special_tokens
            return [0]

        def decode(self, ids):
            return "".join({0: "0", 1: " a", 2: "�"}[value] for value in ids).lstrip()

    tokens, decode, eos, bitmask, size = _schema_tokenizer(Tokenizer())
    assert tokens == [(0, "0", False), (1, " a", True)]
    assert decode([0, 1, 2]) == "0 a" and (eos, bitmask, size) == (2, False, 3)


def test_depth_25d_uses_only_approved_models_and_inpaints_once(tmp_path, monkeypatch):
    from core import depth_25d

    assert set(depth_25d.DA3_REVISION) == {"DA3-SMALL", "DA3-BASE", "DA3MONO-LARGE"}
    assert (depth_25d.DEPTH_LICENSE, depth_25d.MIGAN_LICENSE) == ("Apache-2.0", "MIT")
    with pytest.raises(ValueError):
        renderer.Options(module="photo_25d_parallax", depth_model="DA3-LARGE-1.1", media_file="gravura.jpg")
    source = tmp_path / "original.png"
    Image.new("RGB", (96, 120), "#dad3ae").save(source)
    prediction = np.ones((96, 72), dtype=np.float32)
    prediction[:, 36:] *= 4
    calls = []
    monkeypatch.setenv("FRIMERISE_DEPTH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(depth_25d, "_depth_cpp", lambda image, model: prediction)

    def inpaint(image, mask):
        calls.append(mask)
        assert mask.dtype == np.bool_ and 0 < mask.mean() < .5
        return np.where(mask[:, :, None], 35, image).astype(np.uint8)

    monkeypatch.setattr(depth_25d, "_inpaint_migan", inpaint)
    plate = depth_25d.prepare_depth_plate(source, device="cpu")
    assert depth_25d.prepare_depth_plate(source, device="cpu") == plate and len(calls) == 1
    with Image.open(plate.depth) as image:
        assert np.asarray(image).dtype == np.uint16 and image.getextrema() == (0, 65535)
    with Image.open(plate.background) as image:
        assert np.any(np.asarray(image) == 35)


def test_embeddinggemma_ranks_ptbr_and_penalizes_repeated_shots():
    from core import semantic_broll as semantic

    with pytest.raises(ValueError, match="float32"):
        semantic._precision("cpu", "float16")
    with pytest.raises(ValueError, match="bfloat16"):
        semantic._precision("cuda", "float16")
    first = (1., 0.) + (0.,) * 254
    second = (.98, .2) + (0.,) * 254
    assert np.linalg.norm(semantic._normalized(np.repeat(4., 768))) == pytest.approx(1)
    candidates = [
        semantic.BrollCandidate(name, i * 4., i * 4. + 4, (i * 4. + 1, i * 4. + 2, i * 4. + 3),
                                "Prelinger Archives", "Public domain", 4, "4:3", vector)
        for i, (name, vector) in enumerate((("press-a", first), ("press-b", second)))
    ]

    def embed(query):
        assert "prensa tipográfica" in query
        return first

    ranked = semantic.rank_media_for_sentence("Uma prensa tipográfica imprimia jornais.", candidates, set(), encode_query=embed)
    assert ranked[0].candidate.id == "press-a" and ranked[0].score == pytest.approx(1)
    reused = semantic.rank_media_for_sentence("Uma prensa tipográfica imprimia jornais.", candidates, {"press-a"}, encode_query=embed)
    assert reused[0].candidate.id == "press-b" and reused[-1].rank_score < reused[-1].score


def test_adaptive_music_deterministic_fallback_and_ezaudio_resample(tmp_path, monkeypatch):
    import wave

    from core import music_generator
    from core.music_generator import adaptive_music
    from core.sfx_engine import ezaudio_resample

    monkeypatch.delenv("FRIMERISE_COLAB", raising=False)
    settings = {"topic": "Arquivo noturno", "tone": "dark_mystery",
                "moods": ("tense_low", "drop_silence", "resolve"), "duration_sec": 45}
    first = adaptive_music(tmp_path / "first.wav", **settings)
    second = adaptive_music(tmp_path / "second.wav", **settings)
    assert (first.method, first.license, first.bpm, first.musical_key) == (
        "procedural_cc0", "CC0-1.0", 88, "A minor"
    )
    assert first.path.read_bytes() == second.path.read_bytes()
    with wave.open(str(first.path)) as audio:
        assert (audio.getframerate(), audio.getnchannels()) == (48_000, 2)
    assert ezaudio_resample(np.ones(2400, dtype=np.float32), 24_000).shape == (4800,)
    with pytest.raises(ValueError, match="24"):
        ezaudio_resample(np.ones(2400, dtype=np.float32), 44_100)

    monkeypatch.setenv("FRIMERISE_COLAB", "1")
    monkeypatch.setenv("FRIMERISE_ACE_STEP_ROOT", str(tmp_path / "ace"))
    monkeypatch.setenv("MPLBACKEND", "module://matplotlib_inline.backend_inline")

    def worker(command, *, input, env, **kwargs):
        assert env["MPLBACKEND"] == "Agg"
        target = Path(json.loads(input)[0])
        target.write_bytes(b"audio" * 10)

    monkeypatch.setattr(music_generator.subprocess, "run", worker)
    assert adaptive_music(tmp_path / "worker.wav", **settings).method == "ace_step_1_5"


def test_sfx_transitions_footsteps_and_music_drop_are_on_the_scene_clock(tmp_path):
    from core.audio_master import MusicBed, music_expression
    from core.editing import beat_cuts
    from core.sfx_engine import STEP_PERIOD, STEP_START, generate_kit, sound_cues

    spec = EpisodeSpec("Som", "cutout_paper", fps=30, scenes=[
        {"plugin": "cutout_paper", "id": "a", "narration": "A aldeia esperou.", "music_mood": "drop_silence"},
        {"plugin": "cutout_paper", "id": "b", "narration": "Ele voltou.", "transition": "cut_on_beat",
         "module": "action_cutout_puppet", "action": "walk_to_target", "music_mood": "pulse_build"},
    ])
    source = tmp_path / "voice.wav"
    timings = [SceneTiming(AudioTrackSpec(source, (WordTiming("esperou.", .70, .81),), samples=48000),
                           0, 30, 30, continuous=True),
               SceneTiming(AudioTrackSpec(source, (WordTiming("Ele", .13, .29),), samples=144000, start_sample=48000),
                           30, 90, 30, continuous=True)]
    amended, on_beat = beat_cuts(spec.scenes, timings, MusicBed(source, 4, (.94,)))
    assert on_beat == {"b"} and amended[1].start_frame == 28 and amended[-1].end_sec == 4
    assert music_expression(spec.scenes, amended).startswith("gte(n,0)*lt(n,44800)*(0)")
    effects = sound_cues(spec.scenes[1], amended[1])
    assert effects[0].at_sec == 0 and effects[0].path.name == "stamp_hit.wav"
    steps = [cue.at_sec for cue in effects if cue.path.name == "footsteps_gravel.wav"]
    assert steps[:2] == [round(time*30)/30 for time in (STEP_START, STEP_START+STEP_PERIOD)]
    kit = tmp_path / "kit"
    generate_kit(kit)
    assert len(list(kit.glob("*.wav"))) == 8


def test_dynamic_qc_measures_palette_pace_collisions_and_reuse():
    from core.qc_gate import editorial_checks

    starts = [0, 3, 8, 11, 16, 19, 24, 29]
    scenes = [{"id": str(i), "start_sec": start, "end_sec": end, "dynamic_edit": True,
               "module": "full_bleed_macro" if i == 7 else "photo_cutout", "image_ids": ("used",) if i < 4 else (),
               "layout_audit": {"frames": 10, "violations": []}} for i, (start, end) in enumerate(
                   zip(starts, starts[1:] + [34], strict=True))]
    scenes[0]["layout_audit"]["violations"].append({"type": "collision", "at_sec": .25, "text": "x", "image": "y"})
    frames = [np.full((160, 90, 3), 45 if j == 0 else 190, dtype=np.uint8) for j in range(len(scenes))]
    checks, data = editorial_checks(scenes, frames, 34)
    assert checks == {"layout_collision": False, "visual_diversity": True, "image_reuse": False,
                      "dynamic_rhythm": True, "header_frequency": True}
    assert data["max_image_reuse"] == 4 and data["layout_frames_checked"] == 80

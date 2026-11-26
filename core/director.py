"""Plan a native episode with one small provider request and at most one repair."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from math import ceil, floor
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from .audio_aligner import _ends_sentence, align_audio
from .editing import phrase_start
from .media_fetcher import capture_media_credits, database_setting, local_media_file
from .registry import discover, get_plugin
from .schema import EpisodeSpec, SceneTiming, WordTiming
from .tts_engine import default_edge_voice

ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
WORDS_PER_SEC = 145 / 60


class DirectorError(ValueError):
    """A safe message suitable for the CLI and for a repair prompt."""


@dataclass(frozen=True)
class DirectionProfile:
    skill: str
    excerpt: tuple[str, str]
    notes: str
    options: type[BaseModel]
    module: Callable[[BaseModel, dict, bool], str]


def direction_for(cartridge):
    profile = get_plugin("direction", cartridge, optional=True)
    if profile is None:
        discover()
        profile = get_plugin("direction", cartridge, optional=True)
    if profile is None:
        raise DirectorError("O cartucho ainda não oferece direção automática.")
    return profile


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other):
        return Usage(self.prompt_tokens + other.prompt_tokens, self.completion_tokens + other.completion_tokens,
                     self.total_tokens + other.total_tokens)


class Provider(Protocol):
    def complete(self, system: str, prompt: str, *, max_tokens: int) -> tuple[str, Usage]: ...


def _deepseek_keys():
    keys = [os.environ.get("DEEPSEEK_API_KEY", "").strip()]
    studio_database = Path.home() / "frimerise-cinema-studio" / "data" / "frimerise.db"
    for path in (os.environ.get("FRIMERISE_DB"), Path.cwd() / "frimerise.db", studio_database):
        if path:
            keys.append(database_setting(path, "deepseek_api_key"))
    return tuple(dict.fromkeys(key for key in keys if key))


class DeepSeek:
    endpoint = "https://api.deepseek.com/chat/completions"
    model = "deepseek-flash"

    def complete(self, system, prompt, *, max_tokens):
        keys = _deepseek_keys()
        if not keys:
            raise DirectorError(
                "Credencial DeepSeek ausente. Configure DEEPSEEK_API_KEY ou settings.deepseek_api_key "
                "no FRIMERISE_DB, no frimerise.db do diretório atual ou no banco padrão do Cinema Studio."
            )
        if any(key in system or key in prompt for key in keys):
            raise DirectorError("A credencial não pode fazer parte do roteiro ou das instruções.")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
            "max_tokens": max_tokens, "temperature": .35,
        }
        for key in keys:
            request = Request(self.endpoint, json.dumps(payload).encode(), {
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            })
            try:
                with urlopen(request, timeout=120) as response:
                    result = json.load(response)
                raw_usage = result["usage"]
                counts = [raw_usage[field] for field in ("prompt_tokens", "completion_tokens", "total_tokens")]
                content = result["choices"][0]["message"]["content"] or ""
                if (any(type(count) is not int or count < 0 for count in counts)
                        or counts[2] <= 0 or counts[0] + counts[1] != counts[2]
                        or not isinstance(content, str) or (content.strip() and not counts[1])):
                    raise ValueError
            except HTTPError as error:
                if error.code == 401:
                    continue
                raise DirectorError(f"DeepSeek rejeitou a chamada (HTTP {error.code}).") from None
            except (URLError, OSError):
                raise DirectorError("Falha na conexão com a API DeepSeek.") from None
            except (ValueError, KeyError, TypeError, AttributeError, IndexError):
                raise DirectorError("DeepSeek respondeu sem uma contagem de tokens válida para a geração.") from None
            try:
                decoded = json.dumps(json.loads(content), ensure_ascii=False)
            except ValueError:
                decoded = ""
            if any(candidate in content or candidate in decoded for candidate in keys):
                raise DirectorError("Resposta do provedor contém a credencial e foi descartada.")
            return content, Usage(*counts)
        raise DirectorError(
            "DeepSeek rejeitou a autenticação de todas as credenciais encontradas (HTTP 401). "
            "Atualize DEEPSEEK_API_KEY ou settings.deepseek_api_key no frimerise.db."
        ) from None


PROVIDERS = {"deepseek": DeepSeek}


@dataclass(frozen=True)
class Plan:
    episode: EpisodeSpec
    modules: tuple[str, ...]
    usage: Usage
    timeline: tuple[SceneTiming, ...] = ()


def _words(text):
    return len(re.findall(r"\b[\w]+(?:[-’'][\w]+)*\b", text))


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def _module(scene, profile, *, assets_root, generated):
    model = profile.options
    allowed_fields = set(model.model_fields)
    if scene.keys() - allowed_fields:
        raise DirectorError("retire os campos que não pertencem ao cartucho")
    local_files = [scene.get("media_file")]
    photos = scene.get("photos")
    if isinstance(photos, list):
        local_files.extend(photo.get("media_file") for photo in photos if isinstance(photo, dict))
    if generated and any(path is not None for path in local_files):
        raise DirectorError("substitua media_file por uma consulta; caminhos são fornecidos pelo usuário")
    if not generated and any(path is not None for path in local_files):
        with capture_media_credits(assets_root=assets_root):
            try:
                for path in local_files:
                    if path is not None:
                        local_media_file(path)
            except (TypeError, ValueError, OSError):
                raise DirectorError("media_file precisa apontar para um PNG, JPG ou MP4 local existente")
    try:
        options = model.model_validate(scene)
    except ValidationError as error:
        fields = ", ".join(".".join(str(part) for part in issue["loc"] if isinstance(part, (int, str))) or "opções do módulo"
                           for issue in error.errors(include_input=False, include_context=False, include_url=False))
        # Error details can incorporate untrusted provider strings. Limit diagnostics to safe field names.
        fields = " ".join(part for part in fields.split() if re.fullmatch(r"[\w.,]+", part))
        raise DirectorError(f"preencha com valores válidos os campos {fields or 'do módulo'}") from None
    if not has_text(options.headline):
        raise DirectorError("informe headline")
    return profile.module(options, scene, generated)


def validate_episode(episode, *, assets_root=ROOT, generated=False):
    """Validate the actual renderer options without loading other cartridge skills."""
    cartridge = episode.niche_skill
    profile = direction_for(cartridge)
    if episode.aspect_ratio not in DIMENSIONS or episode.fps != 30:
        raise DirectorError("Use um cartucho suportado, formato 9:16 ou 16:9 e 30 fps.")
    modules = []
    issues = []
    previous = None
    for index, scene in enumerate(episode.scenes, 1):
        try:
            if scene.plugin != cartridge:
                raise DirectorError("use o mesmo plugin do episódio")
            if _words(scene.narration) < 2 or not _ends_sentence(scene.narration.split()[-1]):
                raise DirectorError("a narração deve ter pelo menos duas palavras e encerrar em . ! ou ?")
            if scene.visual_anchor and phrase_start(
                scene.visual_anchor, (WordTiming(token, 0, 0) for token in scene.narration.split())
            ) is None:
                raise DirectorError("a âncora visual precisa ser um trecho literal da narração")
            if scene.broll_clip:
                local = scene.options.get("media_file")
                if isinstance(scene.broll_clip, str) and Path(scene.broll_clip).suffix.lower() == ".mp4":
                    local = local or scene.broll_clip
                if generated and local:
                    raise DirectorError("arquivos locais de B-roll devem ser fornecidos pelo usuário")
                if local:
                    with capture_media_credits(assets_root=assets_root):
                        try:
                            if local_media_file(local).suffix.lower() != ".mp4":
                                raise ValueError
                        except (OSError, ValueError, TypeError):
                            raise DirectorError("broll_clip precisa de um MP4 local existente") from None
                if not isinstance(scene.broll_clip, str) and not (scene.broll_clip.query.strip() or local):
                    raise DirectorError("broll_clip precisa de query ou media_file")
                module = "broll_clip"
            else:
                selected = profile if scene.style == cartridge else direction_for(scene.style)
                module = _module(scene.options, selected, assets_root=assets_root, generated=generated)
            if module == previous and (generated or not scene.allow_consecutive_module or not scene.visual_description):
                raise DirectorError("troque o módulo visual repetido em cenas consecutivas")
        except DirectorError as error:
            issues.append(f"Cena {index}: {error}.")
            previous = None
        else:
            modules.append(module)
            previous = module
    if issues:
        raise DirectorError(" ".join(issues[:6]))
    return tuple(modules)


def infer_tone(topic):
    topic = topic or ""
    if re.search(r"\b(?:mist[eé]rio|vampir\w*|sobrenatural|assombra\w*|terror|horror)\b", topic, re.I):
        return "dark_mystery"
    if re.search(r"\bsuspense\b", topic, re.I):
        return "suspense"
    if re.search(r"\b(?:document[aá]rio|hist[oó]ric\w*|hist[oó]ria real|1\d{3})\b", topic, re.I):
        return "historical_documentary"
    return "neutral"


def configure_production(episode, profile=None):
    """Select studio defaults while retaining explicit TTS choices on edited plans."""
    if profile is not None:
        episode = replace(episode, production_profile=profile)
    if episode.production_profile == "studio_sota" and episode.tts_plugin == "gemini_tts":
        episode = replace(episode, tts_plugin="chatterbox_ptbr", tts_fallback_plugin="edge_kokoro_tts",
                          tts_locale=episode.tts_locale or "pt-BR", voice_id=None)
    return episode


def _episode(cartridge, aspect, title, scenes, *, tone="neutral", production_profile="standard"):
    width, height = DIMENSIONS[aspect]
    studio = production_profile == "studio_sota"
    return EpisodeSpec(
        title, cartridge, scenes=scenes, aspect_ratio=aspect, width=width, height=height, fps=30,
        tts_locale="pt-BR", tts_plugin="chatterbox_ptbr" if studio else "edge_kokoro_tts",
        voice_id=None if studio else default_edge_voice(tone), tts_fallback_plugin="edge_kokoro_tts" if studio else None,
        audio_mode="continuous", tone=tone, production_profile=production_profile,
    )


def _decode(content, cartridge, aspect, scene_count, duration, transcript, *, tone="neutral", production_profile="standard"):
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise DirectorError("Devolva um objeto JSON completo.") from None
    if not isinstance(data, dict) or set(data) != {"title", "scenes"} or not has_text(data.get("title")):
        raise DirectorError("Informe exatamente title e scenes, com title não vazio.")
    scenes = data["scenes"]
    if not isinstance(scenes, list) or len(scenes) != scene_count:
        raise DirectorError(f"Devolva exatamente {scene_count} cenas.")
    reserved = {"id", "plugin", "options", "pause_after_sec", "cut_music_for_punchline"}
    scene_fields = ("cartridge_override", "broll_clip", "transition", "music_mood", "suppress_captions",
                    "visual_anchor", "visual_description")
    result = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or reserved & scene.keys():
            raise DirectorError(f"Cena {index}: use um objeto de opções diretas, sem campos internos do episódio.")
        fields = scene.copy()
        if transcript is not None:
            if "narration" in fields:
                raise DirectorError(f"Cena {index}: remova narration; o áudio fornece o texto literal.")
            narration = transcript.scenes[index - 1].narration
        else:
            narration = fields.pop("narration", "")
            if not has_text(narration):
                raise DirectorError(f"Cena {index}: informe narration.")
            ideal = duration * WORDS_PER_SEC / scene_count
            minimum = max(2, min(floor(ideal), floor(7 * WORDS_PER_SEC)))
            maximum = max(ceil(ideal) + 2, ceil(11 * WORDS_PER_SEC))
            if not minimum <= _words(narration) <= maximum:
                raise DirectorError(f"Cena {index}: escreva {minimum} a {maximum} palavras.")
        edit = {key: fields.pop(key) for key in scene_fields if key in fields}
        result.append({"id": f"s{index:02d}", "plugin": cartridge, "narration": narration,
                       "pause_after_sec": 0.0, "options": fields, **edit})
    if duration is not None:
        target = duration * WORDS_PER_SEC
        actual = sum(_words(scene["narration"]) for scene in result)
        if abs(actual - target) > max(3, target * .10):
            raise DirectorError(f"A narração tem {actual} palavras e precisa de aproximadamente {round(target)}.")
    try:
        return _episode(cartridge, aspect, data["title"].strip(), result, tone=tone,
                        production_profile=production_profile)
    except ValidationError:
        raise DirectorError("Corrija os tipos e valores dos campos de edição, transição e B-roll.") from None


def _guide(profile):
    raw = (ROOT / profile.skill).read_text(encoding="utf-8")
    start, end = profile.excerpt
    _, marker, section = raw.partition(start)
    excerpt, closing, _ = section.partition(end)
    if not marker or not closing:
        raise DirectorError("O guia do cartucho não contém a seção esperada para direção.")
    return (marker + excerpt).strip()


def plan_episode(cartridge, aspect, *, topic=None, duration=None, audio=None, work=None, provider=None, tone=None,
                 production_profile="standard"):
    """For existing speech, transcribe first and let the provider supply visuals only."""
    profile = direction_for(cartridge)
    tone = tone or infer_tone(topic)
    if aspect not in DIMENSIONS:
        raise DirectorError("Formato desconhecido.")
    if (topic is None) == (audio is None):
        raise DirectorError("Use exatamente um de --topic ou --audio.")
    transcript = None
    timeline = ()
    if audio is not None:
        audio = Path(audio).expanduser().resolve()
        if audio.suffix.lower() not in {".mp3", ".wav"} or not audio.is_file() or work is None:
            raise DirectorError("Informe um arquivo MP3 ou WAV válido para --audio.")
        if duration is not None:
            raise DirectorError("Em --audio, a gravação determina a duração.")
        shell = _episode(cartridge, aspect, audio.stem, [{"id": "s01", "plugin": cartridge, "narration": "Áudio original."}],
                         production_profile=production_profile)
        transcript, times = align_audio(shell, audio, work, auto_scenes=True, reference_text="")
        if not any(timing.audio.words for timing in times):
            raise DirectorError("A gravação não contém fala detectável.")
        timeline = tuple(times)
        scene_count = len(transcript.scenes)
        segments = [[round(t.duration_sec, 2), s.narration] for s, t in zip(transcript.scenes, timeline, strict=True)]
        prompt = "Áudio transcrito [duração, fala] por cena. Devolva apenas opções visuais na mesma ordem; omita narration.\n"
        prompt += json.dumps(segments, ensure_ascii=False, separators=(",", ":"))
    else:
        if not has_text(topic) or not isinstance(duration, (int, float)) or not 3 <= duration <= 600:
            raise DirectorError("Informe um tema e duração de 3 a 600 segundos.")
        scene_count = max(1, round(duration / 9))
        prompt = (
            f"Tema: {json.dumps(topic, ensure_ascii=False)}\n"
            f"Escreva narration em cada cena: PT-BR, terceira pessoa, sem falas diretas, "
            f"{round(duration * WORDS_PER_SEC)} palavras no total, cerca de {round(duration * WORDS_PER_SEC / scene_count)} "
            "por cena, ritmo de 145 palavras/min e cenas equilibradas de 7–11s quando couber. "
            "Feche cada cena com ponto, exclamação ou interrogação. Use fatos verificáveis, "
            "atribua relatos e qualifique primazias históricas disputadas. "
            "O tema acima determina todo o conteúdo; use exemplos do guia apenas como referência de formato."
        )
    system = (
        f"Direção de vídeo {cartridge} {aspect}. Retorne JSON compacto {{\"title\":\"…\",\"scenes\":[{{…}}]}} "
        f"com exatamente {scene_count} cenas e campos de composição diretamente em cada objeto. "
        "Não repita o módulo (ou ambiente ilustrado) em cenas seguidas. "
        "Não invente arquivos locais, créditos ou dados. Para busca histórica prefira acervo em domínio público.\n"
        f"{profile.notes}\nGuia do cartucho:\n{_guide(profile)}"
    )
    provider = provider if provider is not None else DeepSeek()
    usage = Usage()
    repair = ""
    for attempt in range(2):
        content, consumed = provider.complete(system, prompt + repair, max_tokens=max(768, 250 * scene_count + 120))
        if consumed.total_tokens <= 0:
            raise DirectorError("O provedor não confirmou consumo positivo de tokens para o plano.")
        usage += consumed
        try:
            episode = _decode(content, cartridge, aspect, scene_count, duration, transcript, tone=tone,
                              production_profile=production_profile)
            modules = validate_episode(episode, generated=True)
        except DirectorError as error:
            if attempt:
                raise DirectorError(f"Plano inválido após um reparo: {error}") from None
            repair = "\nRefaça o JSON inteiro corrigindo esta falha de validação: " + str(error)
        else:
            return Plan(episode, modules, usage, timeline)
    raise AssertionError("unreachable")

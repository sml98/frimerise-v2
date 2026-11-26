"""Chatterbox's dedicated Brazilian Portuguese checkpoint in a Colab L4 worker.

The loader uses the official MIT package and the same checkpoint pairing as
https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-pt-br/tree/9e51582
with the tokenizer and safetensors decoder from the dedicated PT-BR release.
"""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import wave
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from core.registry import TTSUnavailable
from core.schema import SAMPLE_RATE, Speech
from core.text_utils import word_key

MODEL = "ResembleAI/Chatterbox-Multilingual-pt-br"
MODEL_REVISION = "b3952f18bc2eaa72b9bd7c17d2c4653bcad4770d"
BASE_MODEL = "ResembleAI/chatterbox"
BASE_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
CROSSFADE_SEC = 0.018
MAX_CHUNK_SEC = 18
CHARS_PER_SECOND = 15
MAX_RETRIES = 2

_ROOT = Path(__file__).resolve().parents[2]
_SENTENCE_END = re.compile(r"""[.!?…]+["'»”’)\]]*(?=\s|$)""")
_ABBREVIATIONS = {"art", "dr", "dra", "ex", "p", "pág", "prof", "profa", "séc", "sr", "sra", "srta", "vol"}
_DRAMATIC_TONES = {"dark_mystery", "historical_suspense", "historical_documentary", "suspense", "horror"}

# Exit statuses communicate the failure stage without exposing model or transport logs.
NO_L4 = 20
MODEL_FAILED = 21
SYNTHESIS_FAILED = 22
INVALID_REQUEST = 23
_WORKER_ERRORS = {
    NO_L4: "Chatterbox PT-BR requer uma GPU NVIDIA L4 no Colab.",
    MODEL_FAILED: "Falha ao carregar o checkpoint Chatterbox PT-BR ou suas dependências na L4.",
    SYNTHESIS_FAILED: "Chatterbox PT-BR não gerou uma faixa válida após as tentativas.",
    INVALID_REQUEST: "O worker Chatterbox PT-BR recebeu um roteiro ou referência WAV inválidos.",
}


class WorkerError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(_WORKER_ERRORS[status])


def expected_duration_sec(text):
    return max(0.75, len(text) / CHARS_PER_SECOND)


def _sentences(text):
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.group() == "." and match.start() != 0:
            preceding = re.search(r"[^\W\d_]+$", text[start:match.start()])
            if preceding:
                token = preceding.group()
                if token.casefold() in _ABBREVIATIONS or (len(token) == 1 and token.isupper()):
                    continue
        sentence = text[start:match.end()].strip()
        if sentence:
            yield sentence
        start = match.end()
    if tail := text[start:].strip():
        yield tail


def split_chunks(text):
    """Pack up to two sentences and split oversized sentences at word boundaries."""
    maximum = MAX_CHUNK_SEC * CHARS_PER_SECOND
    chunks, pending = [], []
    for sentence in _sentences(" ".join(text.split())):
        if len(sentence) > maximum:
            if pending:
                chunks.append(" ".join(pending))
                pending = []
            part = []
            for word in sentence.split():
                if len(word) > maximum:
                    raise ValueError("Uma palavra excede o limite de caracteres do Chatterbox.")
                if part and len(" ".join((*part, word))) > maximum:
                    chunks.append(" ".join(part))
                    part = []
                part.append(word)
            chunks.append(" ".join(part))
            continue
        if len(pending) == 2 or len(" ".join((*pending, sentence))) > maximum:
            chunks.append(" ".join(pending))
            pending = []
        pending.append(sentence)
    if len(pending) == 1 and chunks:
        previous = list(_sentences(chunks[-1]))
        if len(previous) == 2 and len(" ".join((previous[1], pending[0]))) <= maximum:
            chunks[-1] = previous[0]
            pending.insert(0, previous[1])
    if pending:
        chunks.append(" ".join(pending))
    if not chunks:
        raise ValueError("O roteiro Chatterbox PT-BR está vazio.")
    return tuple(chunks)


def prosody_for_tone(tone):
    return (0.65, 0.38) if tone in _DRAMATIC_TONES else (0.5, 0.5)


def _matches_spoken_text(text, transcribed):
    """Catch inserted phrases without requiring perfect ASR on historical names."""
    reference, observed = [
        [key for token in passage.split() if (key := word_key(token))]
        for passage in (text, transcribed)
    ]
    matcher = SequenceMatcher(a=reference, b=observed, autojunk=False)
    edits = matcher.get_opcodes()
    if edits and edits[0][0] == "insert":
        return False
    return matcher.ratio() >= .67 and all(
        right - left - (end - start) <= 2 or any(word.isdecimal() for word in reference[start:end])
        for tag, start, end, left, right in edits if tag in {"replace", "insert"}
    )


def validated_chunk(generate, text, sample_rate, *, verify=None):
    """Retry a chunk if its duration or transcribed speech diverges from the script."""
    expected = expected_duration_sec(text)
    for attempt in range(MAX_RETRIES + 1):
        samples = np.asarray(generate(attempt), dtype=np.float32).squeeze()
        if samples.ndim != 1:
            continue
        duration = samples.size / sample_rate
        if (0.45 * expected <= duration <= 2.2 * expected and np.isfinite(samples).all()
                and np.max(np.abs(samples)) > 0.0001 and (verify is None or verify(samples))):
            return samples
    raise WorkerError(SYNTHESIS_FAILED)


def stitch_chunks(chunks, pause_after_sec=None):
    """Join 48 kHz chunks with a micro-crossfade and preserve authored pauses."""
    if not chunks:
        raise ValueError("Nenhum bloco de áudio para costurar.")
    pauses = [0] * len(chunks) if pause_after_sec is None else [
        round(value * SAMPLE_RATE) if math.isfinite(value) and value >= 0 else -1
        for value in pause_after_sec
    ]
    if len(pauses) != len(chunks) or any(value < 0 for value in pauses):
        raise ValueError("As pausas precisam corresponder aos blocos de áudio.")

    pieces = []
    tail = chunks[0]
    for pause, incoming in zip(pauses[:-1], chunks[1:], strict=True):
        overlap = min(round(CROSSFADE_SEC * SAMPLE_RATE), tail.size // 2, incoming.size // 2)
        if not overlap:
            pieces.extend((tail, np.zeros(pause, dtype=np.float32)))
            tail = incoming
            continue
        fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
        outgoing = tail[-overlap:] * (1.0 - fade_in)
        beginning = incoming[:overlap] * fade_in
        pieces.append(tail[:-overlap])
        if pause:
            pieces.extend((outgoing, np.zeros(pause, dtype=np.float32), beginning))
        else:
            pieces.append(outgoing + beginning)
        tail = incoming[overlap:]
    if pauses[-1]:
        fade_length = min(round(CROSSFADE_SEC * SAMPLE_RATE), tail.size)
        if fade_length:
            tail = tail.copy()
            tail[-fade_length:] *= np.linspace(1.0, 0.0, fade_length, dtype=np.float32)
        pieces.extend((tail, np.zeros(pauses[-1], dtype=np.float32)))
    else:
        pieces.append(tail)
    return np.concatenate(pieces)


def _on_colab():
    return bool(os.environ.get("FRIMERISE_COLAB") == "1" or os.environ.get("COLAB_RELEASE_TAG"))


def _reference_path(voice_ref):
    if voice_ref is None:
        return None
    path = Path(voice_ref).expanduser().resolve()
    try:
        with wave.open(str(path), "rb") as audio:
            if not audio.getnframes():
                raise ValueError("A referência está vazia.")
    except (OSError, EOFError, wave.Error, ValueError):
        raise TTSUnavailable("Forneça um WAV PCM válido em --voice-ref.") from None
    return path


def _load_model(device):
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.t3 import T3
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.tokenizers import MTLTokenizer
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    def dedicated(filename):
        return hf_hub_download(MODEL, filename, revision=MODEL_REVISION, token=False)

    def shared(filename):
        return hf_hub_download(BASE_MODEL, filename, revision=BASE_REVISION, token=False)

    voice_encoder = VoiceEncoder()
    voice_encoder.load_state_dict(load_file(shared("ve.safetensors")))
    voice_encoder.to(device).eval()

    config = T3Config.multilingual()
    if (config.text_tokens_dict_size, config.speech_tokens_dict_size) != (2454, 8194):
        raise ValueError("Vocabulário T3 incompatível com o checkpoint PT-BR.")
    t3 = T3(config)
    t3.load_state_dict(load_file(dedicated("t3_pt_br.safetensors")))
    t3.to(device).eval()

    decoder = S3Gen()
    weights = decoder.load_state_dict(load_file(dedicated("s3gen_v3.safetensors")), strict=False)
    # The package derives these frontend buffers from the tokenizer configuration.
    frontend_buffers = {"tokenizer._mel_filters", "tokenizer.window"}
    if weights.unexpected_keys or set(weights.missing_keys) - frontend_buffers:
        raise ValueError("Decodificador S3Gen v3 incompatível com os pesos PT-BR.")
    decoder.to(device).eval()

    tokenizer = MTLTokenizer(dedicated("grapheme_mtl_merged_expanded_v1.json"))
    voice = Conditionals.load(Path(shared("conds.pt")), map_location="cpu").to(device)
    return ChatterboxMultilingualTTS(t3, decoder, voice_encoder, tokenizer, device, conds=voice)


def render_wav(text, output, tone="neutral", voice_ref=None, pause_after_sec=None):
    """Execute inside the Colab subprocess so CUDA memory is released after synthesis."""
    if not _on_colab():
        raise WorkerError(NO_L4)
    try:
        chunks = split_chunks(text)
        reference = _reference_path(voice_ref)
    except (TypeError, ValueError, TTSUnavailable):
        raise WorkerError(INVALID_REQUEST) from None

    try:
        import torch
    except (ImportError, OSError):
        raise WorkerError(MODEL_FAILED) from None
    if not torch.cuda.is_available() or not re.search(r"\bL4\b", torch.cuda.get_device_name(0), re.I):
        raise WorkerError(NO_L4)

    try:
        from scipy.signal import resample_poly

        from core.audio_aligner import _whisper_model

        model = _load_model("cuda")
        reviewer = _whisper_model(os.environ.get("FRIMERISE_WHISPER_MODEL", "small"), "cpu")
        exaggeration, cfg_weight = prosody_for_tone(tone)
        if reference is not None:
            model.prepare_conditionals(str(reference), exaggeration=exaggeration)
    except Exception:
        raise WorkerError(MODEL_FAILED) from None

    try:
        common = math.gcd(model.sr, SAMPLE_RATE)
        verifier_rate = 16_000
        verifier_ratio = math.gcd(model.sr, verifier_rate)
        rendered = []
        for index, chunk in enumerate(chunks):
            seed = int.from_bytes(hashlib.sha256(chunk.encode("utf-8")).digest()[:4], "big") + index

            def generate(attempt):
                torch.manual_seed(seed + attempt)
                wav = model.generate(chunk, language_id="pt", exaggeration=exaggeration, cfg_weight=cfg_weight)
                return wav.detach().cpu().numpy()

            def verify(samples):
                pcm = resample_poly(samples, verifier_rate // verifier_ratio, model.sr // verifier_ratio)
                segments, _ = reviewer.transcribe(pcm, language="pt", beam_size=3, temperature=0.0,
                                                  condition_on_previous_text=False)
                return _matches_spoken_text(chunk, " ".join(segment.text for segment in segments))

            samples = validated_chunk(generate, chunk, model.sr, verify=verify)
            rendered.append(resample_poly(samples, SAMPLE_RATE // common, model.sr // common))
        signal = stitch_chunks(rendered, pause_after_sec)
        pcm = np.rint(np.clip(signal, -1.0, 32767 / 32768) * 32768).astype("<i2")
        output = Path(output)
        with TemporaryDirectory(prefix=".chatterbox-", dir=output.parent) as directory:
            staged = Path(directory) / "voice.wav"
            with wave.open(str(staged), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(pcm.tobytes())
            staged.replace(output)
    except Exception:
        raise WorkerError(SYNTHESIS_FAILED) from None


def synthesize(text, *, voice_id, speech_style, output, locale=None, continuous=False, voice_ref=None,
               tone="neutral"):
    """Receive already-phoneticized input from the core TTS dispatcher."""
    if locale not in (None, "pt-BR"):
        raise TTSUnavailable("O checkpoint Chatterbox PT-BR sintetiza apenas português brasileiro.")
    if not _on_colab():
        raise TTSUnavailable(_WORKER_ERRORS[NO_L4])
    reference = _reference_path(voice_ref)
    path = Path(output).with_suffix(".wav").resolve()
    request = {"text": text, "output": str(path), "voice_ref": str(reference) if reference else None, "tone": tone}
    executable = os.environ.get("FRIMERISE_CHATTERBOX_PYTHON") or sys.executable
    try:
        result = subprocess.run([executable, "-m", "scripts.chatterbox_colab"], cwd=_ROOT,
                                input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        raise TTSUnavailable(_WORKER_ERRORS[MODEL_FAILED]) from None
    if result.returncode or not path.is_file():
        error = _WORKER_ERRORS.get(result.returncode, _WORKER_ERRORS[SYNTHESIS_FAILED])
        raise TTSUnavailable(error)
    return Speech(path, locale="pt-BR", voice_id=MODEL)

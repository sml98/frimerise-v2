"""Map a continuous narration to frame-safe scenes and verbatim caption blocks."""

import logging
import os
import wave
from bisect import bisect_left
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from math import ceil, floor, isfinite
from pathlib import Path

from .encoder import run_ffmpeg
from .schema import SAMPLE_RATE, AudioTrackSpec, CaptionBlock, SceneTiming, WordTiming
from .text_utils import word_key

DANGLING = frozenset("a o as os de do da dos das em e um uma uns umas no na nos nas por para com ao aos que mas".split())
SENTENCE_END = ".!?…"


@lru_cache(maxsize=2)
def _whisper_model(name, device):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8",
                        cpu_threads=min(8, os.cpu_count() or 1) if device == "cpu" else 0)


def transcribe_words(path, locale=None, *, initial_prompt=None):
    import ctranslate2

    name = os.environ.get("FRIMERISE_WHISPER_MODEL", "small")
    language = locale.split("-", 1)[0].lower() if locale else None

    def read(device):
        segments, _ = _whisper_model(name, device).transcribe(
            str(path), language=language, word_timestamps=True, vad_filter=True, initial_prompt=initial_prompt,
        )
        # Iteration is part of the CUDA attempt. The generator loads libraries lazily.
        return tuple(WordTiming(word.word.strip(), word.start, word.end)
                     for segment in segments for word in segment.words or () if word.word.strip())

    try:
        cuda = ctranslate2.get_cuda_device_count() > 0
    except (RuntimeError, OSError):
        cuda = False
    if cuda:
        try:
            return read("cuda")
        except (RuntimeError, OSError, ValueError) as error:
            logging.warning("Whisper CUDA indisponível. Usando CPU/int8: %s", error)
            _whisper_model.cache_clear()
    return read("cpu")


@dataclass(frozen=True, slots=True)
class Boundary:
    """A presentation frame carrying an inter-word cut."""

    frame: int
    word_index: int
    pause: float = 0.0
    sentence: bool = False


def _sanitize(words, duration):
    clean = []
    for word in words:
        if word.text.strip() and isfinite(word.start_sec) and isfinite(word.end_sec):
            start = min(duration, max(0.0, word.start_sec))
            end = min(duration, word.end_sec)
            if end > start:
                clean.append(WordTiming(word.text.strip(), start, end))
    return tuple(sorted(clean, key=lambda word: word.start_sec))


def _verified_words(words, text, duration):
    """Project the official tokens onto the monotonically ordered Whisper timestamps."""
    expected = text.split()
    if not words:
        raise ValueError("O roteiro foi fornecido, mas o Whisper não encontrou fala na faixa.")
    keys = [word_key(token) for token in expected]
    matcher = SequenceMatcher(a=keys, b=[word_key(word.text) for word in words], autojunk=False)
    if matcher.ratio() < .45:
        raise ValueError("A transcrição diverge demais do roteiro fornecido para alinhar as legendas com segurança.")
    output = []
    for _, a, b, left, right in matcher.get_opcodes():
        if a == b:
            continue
        if b - a == right - left:
            output.extend(WordTiming(text, word.start_sec, word.end_sec)
                          for text, word in zip(expected[a:b], words[left:right], strict=True))
            continue
        if left < right:
            start, end = words[left].start_sec, max(word.end_sec for word in words[left:right])
        else:
            # Bound an omitted token to its neighboring word timings. Borrow a short
            # span from adjacent words when Whisper reported no inter-word pause.
            left_end = words[left - 1].end_sec if left else words[0].start_sec
            right_start = words[left].start_sec if left < len(words) else words[-1].end_sec
            center = (left_end + right_start) / 2
            radius = min(.11 * (b - a), max(.025, (right_start - left_end) / 2))
            start, end = max(0.0, center - radius), min(duration, center + radius)
        weights = [max(1, len(keys[i])) for i in range(a, b)]
        total = sum(weights)
        offset = 0
        for index, weight in zip(range(a, b), weights, strict=True):
            t0 = start + (end - start) * offset / total
            offset += weight
            t1 = start + (end - start) * offset / total
            output.append(WordTiming(expected[index], t0, t1))

    # Timestamp overlaps can occur in Whisper output, especially around silence.
    # Keep exact endpoints otherwise, and put neighboring tokens on the same boundary.
    for index in range(1, len(output)):
        previous, current = output[index - 1:index + 1]
        if previous.end_sec > current.start_sec:
            time = max(previous.start_sec, min(current.end_sec, (previous.end_sec + current.start_sec) / 2))
            output[index - 1] = replace(previous, end_sec=time)
            output[index] = replace(current, start_sec=time, end_sec=max(time, current.end_sec))
    return tuple(output)


def _boundaries(words, fps, frame_count):
    candidates = []
    prior_end = words[0].end_sec if words else 0.0
    for index in range(1, len(words)):
        previous, following = words[index - 1], words[index]
        prior_end = max(prior_end, previous.end_sec)
        first = max(1, ceil(prior_end * fps - 1e-8))
        last = min(frame_count - 1, floor(following.start_sec * fps + 1e-8))
        if first <= last:
            frame = (first + last) // 2
            candidate = Boundary(frame, index, following.start_sec - prior_end,
                                 previous.text.rstrip('”’"\')]').endswith(tuple(SENTENCE_END)))
            if candidates and frame == candidates[-1].frame:
                candidates[-1] = candidate
            else:
                candidates.append(candidate)
    return candidates


def _script_targets(scenes, words):
    authored, ends = [], []
    for scene in scenes:
        authored.extend(key for token in scene.narration.split() if (key := word_key(token)))
        ends.append(len(authored))
    heard = [word_key(word.text) for word in words]
    matches = SequenceMatcher(a=authored, b=heard, autojunk=False).get_matching_blocks()
    # Interpolate through ASR errors or numerals using the closest sequential matches on both sides.
    anchors = {-1: -1}
    for left, right, count in matches:
        anchors.update((left + i, right + i) for i in range(count))
    anchors[len(authored)] = len(heard)
    known = sorted(anchors)
    for end in ends[:-1]:
        index = bisect_left(known, end)
        a, b = known[index - 1:index + 1]
        yield anchors[a] + (anchors[b] - anchors[a]) * (end - a) / (b - a)


def _script_cuts(scenes, words, candidates, frame_count):
    count = len(scenes) - 1
    if not words:
        if frame_count < len(scenes):
            raise ValueError("A faixa é curta demais para o número de cenas.")
        return [Boundary(i * frame_count // len(scenes), 0) for i in range(1, len(scenes))]
    if len(candidates) < count:
        raise ValueError("A faixa não tem fronteiras seguras suficientes para as cenas do roteiro.")
    cuts = []
    start = 0
    for index, target in enumerate(_script_targets(scenes, words)):
        stop = len(candidates) - (count - index - 1)

        def cost(candidate):
            return (abs(candidate.word_index - target) - min(.40, candidate.pause)
                    - (.18 if candidate.sentence else 0))

        position = min(range(start, stop), key=lambda i: cost(candidates[i]))
        cuts.append(candidates[position])
        start = position + 1
    return cuts


def _automatic_cuts(candidates, frame_count, fps, has_words):
    if not has_words:
        count = ceil(frame_count / (8 * fps))
        return [Boundary(i * frame_count // count, 0) for i in range(1, count)]
    cuts = []
    start = 0
    while frame_count - start > 9 * fps:
        # Protect the next scene from being reduced to a last fragment when possible.
        usable = [cut for cut in candidates if start < cut.frame <= frame_count - 4 * fps]
        normal = [cut for cut in usable if 5 * fps <= cut.frame - start <= 9 * fps
                  and frame_count - cut.frame >= 5 * fps]
        if not usable:
            break
        natural = [cut for cut in usable if 3.8 * fps <= cut.frame - start <= 10 * fps
                   and (cut.sentence or cut.pause >= .21)]
        if frame_count - start <= 10.5 * fps and not (normal or natural):
            break
        cut = min(normal or natural or usable, key=lambda item: (
            abs((item.frame - start) / fps - 7)
            - min(1.0, 2 * item.pause) - (.65 if item.sentence else 0)
        ))
        cuts.append(cut)
        start = cut.frame
    return cuts


def caption_blocks(words):
    """Keep the aligned tokens and avoid splitting immediately after a connector."""
    blocks = []
    start = 0
    while start < len(words):
        end = start + 1
        last_break = None
        length = 0
        while end <= len(words):
            word = words[end - 1]
            length += len(word.text) + (end > start + 1)
            if last_break is not None and (length > 44 or end - start > 8):
                break
            clear = end == len(words) or words[end].start_sec >= word.end_sec - 1e-8
            if clear and word_key(word.text) not in DANGLING:
                last_break = end
                if end == len(words) or word.text.endswith(tuple(SENTENCE_END)) or (
                    end < len(words) and words[end].start_sec - word.end_sec >= .55
                ):
                    break
            end += 1
        end = last_break or min(end, len(words))
        if (len(words) - end <= 2 and end < len(words)
                and words[end].start_sec - words[end - 1].end_sec < .32):
            end = len(words)
        blocks.append(CaptionBlock(tuple(words[start:end])))
        start = end
    return tuple(blocks)


def align_audio(spec, source: Path, work: Path, *, auto_scenes=False, reference_text=None):
    """Decode once and give all scenes views over one PCM file, without joining speech clips."""
    wav = work / "continuous.wav"
    run_ffmpeg("-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "2",
               "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(wav))
    with wave.open(str(wav)) as audio:
        samples = audio.getnframes()
    if samples == 0:
        raise ValueError("A faixa contínua está vazia.")
    duration = samples / SAMPLE_RATE
    if reference_text is None:
        reference_text = " ".join(scene.narration for scene in spec.scenes)
    words = _sanitize(transcribe_words(wav, spec.tts_locale, initial_prompt=reference_text or None), duration)
    if reference_text:
        words = _verified_words(words, reference_text, duration)
    frame_count = (samples * spec.fps + SAMPLE_RATE - 1) // SAMPLE_RATE
    candidates = _boundaries(words, spec.fps, frame_count)
    caption_safe = [cut for cut in candidates if word_key(words[cut.word_index - 1].text) not in DANGLING]
    if len(caption_safe) >= (1 if auto_scenes else len(spec.scenes) - 1):
        candidates = caption_safe
    cuts = (_automatic_cuts(candidates, frame_count, spec.fps, bool(words)) if auto_scenes else
            _script_cuts(spec.scenes, words, candidates, frame_count))
    edges = [Boundary(0, 0), *cuts, Boundary(frame_count, len(words))]
    scenes = []
    timeline = []
    for index, (start, end) in enumerate(zip(edges, edges[1:])):
        begin = start.frame / spec.fps
        first_sample = min(samples, round(start.frame * SAMPLE_RATE / spec.fps))
        last_sample = min(samples, round(end.frame * SAMPLE_RATE / spec.fps))
        local = tuple(WordTiming(word.text, max(0.0, word.start_sec - begin), word.end_sec - begin)
                      for word in words[start.word_index:end.word_index])
        audio = AudioTrackSpec(wav, local, samples=last_sample - first_sample, start_sample=first_sample,
                               locale=spec.tts_locale)
        timeline.append(SceneTiming(audio, start.frame, end.frame - start.frame, spec.fps,
                                    caption_blocks(local), True))
        if auto_scenes:
            seed = spec.scenes[0]
            text = " ".join(word.text for word in local) or seed.narration
            scenes.append(replace(seed, id=f"s{index + 1:02d}", narration=text, pause_after_sec=0.0))
    return (replace(spec, scenes=scenes) if auto_scenes else spec), timeline

"""Video cuts and graphics cues on the measured narration clock."""

from dataclasses import replace
from math import ceil, floor
from typing import NamedTuple

from PIL import Image, ImageDraw

from .audio_aligner import caption_blocks
from .motion import ease_in_out_cubic
from .schema import SAMPLE_RATE
from .text_utils import word_key


class Cut(NamedTuple):
    frame: int
    on_beat: bool = False


def phrase_start(phrase, words):
    wanted = tuple(key for token in phrase.split() if (key := word_key(token)))
    found = [(key, word.start_sec) for word in words if (key := word_key(word.text))]
    for index in range(len(found) - len(wanted) + 1):
        if wanted and tuple(key for key, _ in found[index:index + len(wanted)]) == wanted:
            return float(found[index][1])
    return None


def attach_visual_cues(scenes, timeline):
    result = []
    for scene, timing in zip(scenes, timeline, strict=True):
        if not scene.visual_anchor:
            result.append(timing)
            continue
        at = phrase_start(scene.visual_anchor, timing.audio.words)
        if at is None:
            raise ValueError(f"Cena {scene.id}: a âncora visual não foi encontrada na fala alinhada.")
        frame = min(timing.frame_count - 1, round(at * timing.fps))
        result.append(replace(timing, visual_cue_at=frame / timing.fps))
    return result


def beat_cuts(scenes, timeline, music):
    """Move only continuous cuts with a beat and a speech-free frame nearby."""
    if not music or not all(timing.continuous for timing in timeline):
        return timeline, set()
    fps = timeline[0].fps
    beats = tuple(music.beats_until(timeline[-1].end_sec))
    cuts = [Cut(timeline[0].start_frame)]
    aligned = set()
    for index, (left, right) in enumerate(zip(timeline, timeline[1:]), 1):
        boundary = right.start_frame
        cut = Cut(boundary)
        if scenes[index].transition == "cut_on_beat":
            last = max((word.end_sec for word in left.audio.words), default=left.duration_sec) + left.start_sec
            first = min((word.start_sec for word in right.audio.words), default=0) + right.start_sec
            lo = max(cuts[-1].frame + 1, ceil(last * fps - 1e-7))
            hi = min(right.start_frame + right.frame_count - 1, floor(first * fps + 1e-7))
            candidates = [beat for beat in beats if lo <= round(beat * fps) <= hi and abs(beat - right.start_sec) <= .15]
            if candidates:
                cut = Cut(round(min(candidates, key=lambda beat: abs(beat - right.start_sec)) * fps), True)
                aligned.add(scenes[index].id)
        cuts.append(cut)
    if not aligned:
        return timeline, aligned
    cuts.append(Cut(timeline[-1].start_frame + timeline[-1].frame_count))
    source_end = timeline[-1].audio.start_sample + timeline[-1].audio.samples
    result = []
    for timing, start, end in zip(timeline, cuts[:-1], cuts[1:], strict=True):
        shift = (timing.start_frame - start.frame) / fps
        words = tuple(replace(word, start_sec=max(0, word.start_sec + shift), end_sec=max(0, word.end_sec + shift))
                      for word in timing.audio.words)
        first_sample = round(start.frame * SAMPLE_RATE / fps)
        last_sample = min(source_end, round(end.frame * SAMPLE_RATE / fps))
        result.append(replace(timing, start_frame=start.frame, frame_count=end.frame - start.frame, beat_cut=start.on_beat,
                              caption_blocks=caption_blocks(words),
                              audio=replace(timing.audio, words=words, start_sample=first_sample,
                                            samples=last_sample - first_sample)))
    return result, aligned


def raster_transition(frames, size, fps, transition):
    """Apply the same short incoming cues to legacy RGB24 cartridge frames."""
    for index, frame in enumerate(frames):
        phase = min(index / fps / .30, 1)
        if phase >= 1 or transition == "cut_on_beat":
            yield frame
            continue
        amount = 1 - ease_in_out_cubic(phase)
        image = Image.frombytes("RGB", size, frame)
        width, height = size
        if transition in {"zoom_punch", "whip_pan"}:
            zoom = 1 + .14 * amount
            half_w, half_h = width / zoom / 2, height / zoom / 2
            offset = (width / 2 - half_w) * amount if transition == "whip_pan" else 0
            cx, cy = width / 2 + offset, height / 2
            image = image.resize(size, Image.Resampling.BICUBIC, (cx - half_w, cy - half_h, cx + half_w, cy + half_h))
        elif transition == "flash_cut":
            image = Image.blend(image, Image.new("RGB", size, "#f7cf8b"), amount * .34)
        elif transition == "paper_rip":
            edge = width * (.31 * amount - .03)
            ImageDraw.Draw(image).polygon([(0, 0), (edge, 0), (edge + width * .02, height * .32),
                                           (edge - width * .009, height * .67), (edge, height), (0, height)],
                                          fill="#cbb48a")
        yield image.tobytes()

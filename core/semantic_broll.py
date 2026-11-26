"""Rank footage against the original PT-BR narration with EmbeddingGemma 2."""

import fcntl
import gc
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from .broll_selector import MAX_CANDIDATES, MIN_SHOT_SEC, PROXY_FPS, _ffmpeg, _fingerprint, _quality, _read

MODEL = "google/embeddinggemma-2"
MODEL_REVISION = "f9972ac22e696a105c482202646979e33e4b5a1e"
MODEL_LICENSE = "Apache-2.0"
METHOD = "embeddinggemma2"
INDEX_VERSION = 1
MRL_DIMENSION = 256
REPETITION_PENALTY = .15
KEYFRAME_FRACTIONS = (.25, .50, .75)


class SemanticBrollError(RuntimeError):
    """A failure in the explicitly enabled semantic pipeline."""


@dataclass(frozen=True)
class BrollCandidate:
    id: str
    start_sec: float
    end_sec: float
    keyframes_sec: tuple[float, ...]
    source: str
    license: str
    duration: float
    aspect_ratio: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class RankedMedia:
    candidate: BrollCandidate
    score: float
    rank_score: float


def _normalized(vector, dimension=MRL_DIMENSION):
    vector = np.asarray(vector, dtype=np.float32)[:dimension]
    if vector.shape != (dimension,) or not np.isfinite(vector).all():
        raise SemanticBrollError("EmbeddingGemma 2 retornou um vetor inválido.")
    norm = float(np.linalg.norm(vector))
    if not isfinite(norm) or norm <= 0:
        raise SemanticBrollError("EmbeddingGemma 2 retornou um vetor sem norma válida.")
    return vector / norm


def _precision(device, dtype=None):
    if device not in {"cpu", "cuda"}:
        raise ValueError("O dispositivo de B-roll semântico precisa ser cpu ou cuda.")
    required = "bfloat16" if device == "cuda" else "float32"
    if dtype is not None and dtype != required:
        raise ValueError(f"Use {required} para EmbeddingGemma 2 em {device}.")
    return required


def _is_l4(torch):
    return (torch.cuda.is_available() and re.search(r"\bL4\b", torch.cuda.get_device_name(0))
            and torch.cuda.is_bf16_supported())


@contextmanager
def _embedding_session(device="auto"):
    try:
        import torch
        from transformers import AutoModel, AutoProcessor, AutoTokenizer
    except (ImportError, OSError):
        raise SemanticBrollError("Instale Torch e Transformers 5.24 para selecionar o B-roll semântico.") from None

    if device == "auto":
        device = "cuda" if _is_l4(torch) else "cpu"
    dtype = _precision(device)
    if device == "cuda" and not _is_l4(torch):
        raise SemanticBrollError("EmbeddingGemma 2 em bfloat16 requer uma NVIDIA L4 disponível.")

    revision = {"revision": MODEL_REVISION, "token": False, "trust_remote_code": False}
    model = None
    try:
        model = AutoModel.from_pretrained(
            MODEL, **revision, audio_config=None, dtype=getattr(torch, dtype),
            device_map={"": device}, use_safetensors=True,
        ).eval()
        processor = AutoProcessor.from_pretrained(MODEL, **revision)
        tokenizer = AutoTokenizer.from_pretrained(MODEL, **revision)

        def vectors(inputs):
            inputs = inputs.to(model.device)
            if "pixel_values" in inputs:
                inputs["pixel_values"] = inputs["pixel_values"].to(model.dtype)
            with torch.inference_mode():
                tokens = model(**inputs).last_hidden_state.float()
                mask = inputs["attention_mask"].unsqueeze(-1)
                pooled = (tokens * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
            return [_normalized(vector) for vector in pooled.cpu().numpy()]

        def encode_query(text):
            prompt = f"task: search result | query: {text}"
            return vectors(tokenizer([prompt], return_tensors="pt"))[0]

        def encode_images(images):
            conversations = [[{"role": "user", "content": [{"type": "image", "image": image}]}]
                             for image in images]
            return vectors(processor.apply_chat_template(
                conversations, tokenize=True, return_dict=True, return_tensors="pt",
            ))

        yield device, dtype, encode_query, encode_images
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        if isinstance(error, SemanticBrollError):
            raise
        raise SemanticBrollError(f"EmbeddingGemma 2 não concluiu a inferência ({type(error).__name__}).") from None
    finally:
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def rank_media_for_sentence(narration_ptbr, candidates, used_ids, *, encode_query=None):
    """Return candidates ordered by cosine similarity adjusted for reuse."""
    sentence = narration_ptbr.strip()
    if not sentence:
        raise ValueError("Informe a fala em português para selecionar o B-roll.")
    if not candidates:
        return []
    if encode_query is None:
        with _embedding_session() as (_, _, encode_query, _):
            return rank_media_for_sentence(sentence, candidates, used_ids, encode_query=encode_query)

    query = _normalized(encode_query(sentence))
    used = set(used_ids)
    ranked = []
    for candidate in candidates:
        score = float(np.clip(query @ _normalized(candidate.vector), -1, 1))
        ranked.append(RankedMedia(candidate, score, score - REPETITION_PENALTY * (candidate.id in used)))
    return sorted(ranked, key=lambda item: (-item.rank_score, item.candidate.start_sec, item.candidate.id))


def _shot_frames(source, directory):
    """Return moving shots and three equally phased images per shot."""
    import cv2
    from scenedetect import AdaptiveDetector, SceneManager, open_video

    cv2.setNumThreads(1)
    proxy = directory / "analysis.mp4"
    _ffmpeg("-threads", "2", "-i", source, "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", f"fps={PROXY_FPS},scale=384:384:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(PROXY_FPS), "-threads", "2", "-y", proxy)
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=3, min_content_val=15))
    manager.detect_scenes(video=open_video(str(proxy)))
    captures = cv2.VideoCapture(str(proxy))
    try:
        eligible = []
        for a, b in manager.get_scene_list(start_in_scene=True):
            start, end = a.frame_num, b.frame_num
            if end - start < MIN_SHOT_SEC * PROXY_FPS:
                continue
            quality = _quality(captures, (start + end) // 2, start, end, cv2)
            if quality is not None:
                eligible.append((start, end, quality))

        if len(eligible) > MAX_CANDIDATES:
            count = len(eligible)
            eligible = [max(eligible[i * count // MAX_CANDIDATES:(i + 1) * count // MAX_CANDIDATES],
                            key=lambda shot: shot[2]) for i in range(MAX_CANDIDATES)]

        for start, end, _ in eligible:
            times = tuple(min(end - 1, start + round((end - start) * part)) for part in KEYFRAME_FRACTIONS)
            images = [_read(captures, index, cv2) for index in times]
            if all(image is not None for image in images):
                yield start / PROXY_FPS, end / PROXY_FPS, tuple(index / PROXY_FPS for index in times), [
                    Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)) for image in images
                ]
    finally:
        captures.release()


def _lance_table(root):
    try:
        import lancedb
        import pyarrow as pa
    except ImportError:
        raise SemanticBrollError("Instale LanceDB e PyArrow para manter o índice de B-roll semântico.") from None
    database = lancedb.connect(str(root))
    schema = pa.schema([
        ("id", pa.string()), ("cache_key", pa.string()), ("start_sec", pa.float64()), ("end_sec", pa.float64()),
        ("keyframes_sec", pa.list_(pa.float64(), len(KEYFRAME_FRACTIONS))), ("source", pa.string()),
        ("license", pa.string()), ("duration", pa.float64()), ("aspect_ratio", pa.string()),
        ("vector", pa.list_(pa.float32(), MRL_DIMENSION)),
    ])
    return database.create_table("broll_index", schema=schema, exist_ok=True)


def _indexed_candidates(video, fingerprint, metadata, encode_images, dtype):
    root = Path(os.environ.get("FRIMERISE_BROLL_INDEX", ".cache/broll_index")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([fingerprint, INDEX_VERSION, MODEL_REVISION, dtype, metadata]).encode()).hexdigest()
    with (root / "semantic-broll.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        table = _lance_table(root)
        rows = table.search().where(f"cache_key = '{key}'").limit(MAX_CANDIDATES).to_list()
        if rows:
            return [BrollCandidate(**{field: value for field, value in row.items() if field != "cache_key"})
                    for row in rows]
        candidates = []
        with TemporaryDirectory(dir=root) as directory:
            for start, end, keyframes, images in _shot_frames(video, Path(directory)):
                frames = np.asarray(encode_images(images), dtype=np.float32)
                if frames.shape != (len(KEYFRAME_FRACTIONS), MRL_DIMENSION):
                    raise SemanticBrollError("O modelo não calculou um vetor para cada keyframe do plano.")
                vector = _normalized(frames.mean(axis=0)).tolist()
                shot_id = f"{fingerprint}:{start:.3f}:{end:.3f}"
                candidates.append(BrollCandidate(
                    shot_id, start, end, keyframes, metadata["source"], metadata["license"],
                    end - start, metadata["aspect_ratio"], vector,
                ))
        if candidates:
            table.add([{**asdict(candidate), "cache_key": key} for candidate in candidates])
        return candidates


def select_shot(video, visual="", narration="", *, duration_sec=12, used_ids=(), credit=None, video_info):
    """Select a timed insert wholly contained in one detected shot."""
    if not isfinite(duration_sec) or duration_sec <= 0:
        raise ValueError("Duração inválida para seleção de B-roll.")
    video = Path(video).resolve(strict=True)
    stat = video.stat()
    fingerprint = _fingerprint(video, stat.st_size, stat.st_mtime_ns)
    width, height, _ = video_info
    divisor = int(np.gcd(width, height))
    credit = credit or {}
    metadata = {"source": credit.get("url") or credit.get("source") or video.name,
                "license": credit.get("license") or "não declarada",
                "aspect_ratio": f"{width // divisor}:{height // divisor}"}
    with _embedding_session() as (device, dtype, encode_query, encode_images):
        candidates = _indexed_candidates(video, fingerprint, metadata, encode_images, dtype)
        sentence = " ".join(filter(None, (narration.strip(), visual.strip())))
        ranked = rank_media_for_sentence(sentence, candidates, used_ids, encode_query=encode_query)

    if not ranked:
        raise SemanticBrollError("Nenhum plano legível com movimento foi detectado no B-roll.")
    selected = ranked[0]
    shot = selected.candidate
    center = shot.keyframes_sec[1]
    length = min(duration_sec, shot.duration)
    start = max(shot.start_sec, min(center - length / 2, shot.end_sec - length))
    end = start + length
    return {
        "start_sec": start, "end_sec": end, "score": selected.score, "rank_score": selected.rank_score,
        "shot_id": shot.id, "shot_start_sec": shot.start_sec, "shot_end_sec": shot.end_sec,
        "frame_sec": center, "keyframes_sec": shot.keyframes_sec, "candidates": len(candidates),
        "aspect_ratio": shot.aspect_ratio, "media_license": shot.license,
        "method": METHOD, "detector": "AdaptiveDetector", "device": device, "dtype": dtype,
        "model": MODEL, "model_revision": MODEL_REVISION, "model_license": MODEL_LICENSE,
    }

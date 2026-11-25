"""Index coherent shots and rank their representative frames for a scene."""

import fcntl
import importlib.util
import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from hashlib import file_digest
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

MODEL = "timm/ViT-B-16-SigLIP2-256"
MODEL_REVISION = "fb14461786ff5cd7b18210817522546f28c7143a"
INDEX_VERSION = 1
PROXY_FPS = 10
MIN_SHOT_SEC = 2.0
MAX_CANDIDATES = 24


@dataclass(frozen=True)
class Shot:
    start_sec: float
    end_sec: float
    frame: str
    quality: float


@dataclass(frozen=True)
class Selection:
    start_sec: float
    end_sec: float
    score: float
    shot_start_sec: float
    shot_end_sec: float
    frame_sec: float
    candidates: int
    method: str
    device: str
    model: str | None = None
    model_revision: str | None = None

    def __iter__(self):
        return iter((self.start_sec, self.end_sec, self.score))


def _ffmpeg(*arguments):
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *map(str, arguments)],
                            check=False, capture_output=True, timeout=360)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())


@lru_cache(maxsize=64)
def _fingerprint(path, size, modified):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _read(cap, index, cv2):
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    return frame if ok else None


def _quality(cap, middle, start, end, cv2):
    center = _read(cap, middle, cv2)
    if center is None:
        return None
    gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    if brightness < 24 or brightness > 244 or contrast < 10 or sharpness < 8:
        return None

    def reduced(image):
        mono = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(cv2.resize(mono, (96, 64), interpolation=cv2.INTER_AREA), (5, 5), 0)
        return blurred.astype(np.float32) - float(blurred.mean())

    base = reduced(center)
    motion = 0.0
    for index in (max(start, middle - 5), min(end - 1, middle + 5)):
        neighbor = _read(cap, index, cv2)
        if neighbor is not None:
            motion = max(motion, float(np.mean(np.abs(base - reduced(neighbor)) > 7)))
    if motion < .035:
        return None
    exposure = max(0, 1 - abs(brightness - 119) / 119)
    return .35 * exposure + .25 * min(1, sharpness / 185) + .40 * min(1, motion / .35)


def _index(source, directory):
    import cv2
    from scenedetect import ContentDetector, SceneManager, open_video

    cv2.setNumThreads(1)
    proxy = directory / "analysis.mp4"
    _ffmpeg("-threads", "2", "-i", source, "-an", "-sn", "-dn", "-filter_threads", "1", "-vf",
            f"fps={PROXY_FPS},scale=384:384:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(PROXY_FPS), "-threads", "2", "-y", proxy)
    stream = open_video(str(proxy))
    detector = SceneManager()
    detector.add_detector(ContentDetector(threshold=24, min_scene_len=3))
    detector.detect_scenes(video=stream)
    ranges = detector.get_scene_list(start_in_scene=True)
    cap = cv2.VideoCapture(str(proxy))
    eligible = []
    try:
        for a, b in ranges:
            start, end = a.frame_num, b.frame_num
            if end - start < MIN_SHOT_SEC * PROXY_FPS:
                continue
            middle = (start + end) // 2
            quality = _quality(cap, middle, start, end, cv2)
            if quality is not None:
                eligible.append((start, end, middle, quality))
        if len(eligible) > MAX_CANDIDATES:
            # Represent the entire reel, taking the strongest image within each portion.
            length = len(eligible)
            eligible = [max(eligible[i * length // MAX_CANDIDATES:(i + 1) * length // MAX_CANDIDATES],
                            key=lambda shot: shot[3]) for i in range(MAX_CANDIDATES)]
        shots = []
        for index, (start, end, middle, quality) in enumerate(eligible):
            frame = _read(cap, middle, cv2)
            filename = f"{index:02d}.jpg"
            if frame is not None and cv2.imwrite(str(directory / filename), frame):
                shots.append(Shot(start / PROXY_FPS, end / PROXY_FPS, filename, quality))
    finally:
        cap.release()
        proxy.unlink(missing_ok=True)
    return shots


def index_shots(video):
    source = Path(video).resolve(strict=True)
    stat = source.stat()
    key = _fingerprint(source, stat.st_size, stat.st_mtime_ns)
    root = Path(os.environ.get("FRIMERISE_BROLL_INDEX", ".cache/broll_index")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest, frames = root / f"{key}.json", root / key
    with manifest.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            shots = tuple(Shot(**item) for item in data["shots"])
            if data["version"] == INDEX_VERSION and all((frames / shot.frame).is_file() for shot in shots):
                return frames, shots
        except (OSError, ValueError, KeyError, TypeError):
            pass
        frames.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=root) as temp:
            stage = Path(temp)
            shots = tuple(_index(source, stage))
            for shot in shots:
                (stage / shot.frame).replace(frames / shot.frame)
            pending = stage / "index.json"
            pending.write_text(json.dumps({"version": INDEX_VERSION, "shots": [asdict(shot) for shot in shots]}),
                               encoding="utf-8")
            pending.replace(manifest)
    return frames, shots


def scoring_device(semantic_cpu=False):
    if importlib.util.find_spec("open_clip") is not None:
        try:
            import torch
        except (ImportError, OSError):
            pass
        else:
            if torch.cuda.is_available():
                return "cuda"
            if semantic_cpu:
                return "cpu"
    if semantic_cpu:
        logging.warning("SigLIP 2 indisponível. Usando avaliação visual de CPU para o B-roll.")
    return "visual"


@lru_cache(maxsize=2)
def _load_model(device):
    import open_clip
    from huggingface_hub import snapshot_download

    if device == "cpu":
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            available = next(int(line.split()[1]) for line in meminfo.read_text().splitlines()
                             if line.startswith("MemAvailable:"))
            if available < 3_350_000:
                raise RuntimeError("Memória livre insuficiente para SigLIP 2 em CPU")
    folder = snapshot_download(MODEL, revision=MODEL_REVISION, token=False, allow_patterns=(
        "open_clip_config.json", "open_clip_model.safetensors", "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json",
    ))
    location = f"local-dir:{folder}"
    model, preprocess = open_clip.create_model_from_pretrained(location, device=device)
    return model.eval(), preprocess, open_clip.get_tokenizer(location)


def _semantic_scores(frames, shots, visual, narration, device):
    from contextlib import nullcontext

    import torch

    threads = torch.get_num_threads()
    if device == "cpu":
        torch.set_num_threads(min(2, threads))
    try:
        model, preprocess, tokenizer = _load_model(device)
        texts = [visual or "Filmagem em movimento", narration or visual or "Cena de arquivo em movimento"]
        prompts = tokenizer(texts, context_length=model.context_length).to(device)
        autocast = torch.autocast("cuda", dtype=torch.float16) if device == "cuda" else nullcontext()
        scores = []
        with torch.inference_mode(), autocast:
            text = model.encode_text(prompts, normalize=True).float()
            weights = text.new_tensor([.70, .30])
            for offset in range(0, len(shots), 4 if device == "cuda" else 1):
                batch = []
                size = 4 if device == "cuda" else 1
                for shot in shots[offset:offset + size]:
                    with Image.open(frames / shot.frame) as image:
                        batch.append(preprocess(image.convert("RGB")))
                visual = model.encode_image(torch.stack(batch).to(device), normalize=True).float()
                logits = (visual @ text.T) * model.logit_scale.float().exp()
                if model.logit_bias is not None:
                    logits = logits + model.logit_bias.float()
                scores.extend((logits @ weights).sigmoid().cpu().tolist())
        if not all(isfinite(score) for score in scores):
            raise RuntimeError("SigLIP 2 devolveu scores inválidos")
        return scores
    finally:
        if device == "cpu":
            torch.set_num_threads(threads)


def select_shot(video, visual="", narration="", *, duration_sec=12, device="auto"):
    """Select one shot without crossing a cut, trimming around its midpoint when necessary."""
    if not isfinite(duration_sec) or duration_sec <= 0 or device not in {"auto", "visual", "cpu", "cuda"}:
        raise ValueError("Duração ou dispositivo inválido para seleção de B-roll")
    frames, shots = index_shots(video)
    if not shots:
        return None
    device = scoring_device() if device == "auto" else device
    method, model, revision = "visual_cpu", None, None
    scores = [shot.quality for shot in shots]
    if device in ("cpu", "cuda"):
        try:
            scores = _semantic_scores(frames, shots, visual, narration, device)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            logging.warning("SigLIP 2 indisponível (%s). Usando avaliação visual de CPU para o B-roll.", type(error).__name__)
            device = "visual"
        else:
            method, model, revision = "siglip2", MODEL, MODEL_REVISION
    index = max(range(len(shots)), key=lambda i: (scores[i], shots[i].quality))
    shot = shots[index]
    middle = (shot.start_sec + shot.end_sec) / 2
    start = max(shot.start_sec, middle - duration_sec / 2)
    end = min(shot.end_sec, middle + duration_sec / 2)
    return Selection(start, end, scores[index], shot.start_sec, shot.end_sec, middle, len(shots), method,
                     "cpu" if device == "visual" else device, model, revision)

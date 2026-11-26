"""Build a depth plate and a disocclusion background from archival artwork.

The only depth weights used here are the Apache-2.0 entries in the official
DA3 model table. The CPU GGUF conversions are published by depth-anything.cpp.
MI-GAN's upstream README links the MIT Places2 ONNX pipeline used below.
"""

import fcntl
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, get_args

import numpy as np
from PIL import Image, ImageOps

DepthModel = Literal["DA3-SMALL", "DA3-BASE", "DA3MONO-LARGE"]
DepthDevice = Literal["auto", "cpu", "cuda"]
DEFAULT_DEPTH_MODEL = "DA3-BASE"
DEPTH_LICENSE = "Apache-2.0"
DA3_REVISION = {
    "DA3-SMALL": "e08cab65ca0ec38e7826075418411ab90cab4da3",
    "DA3-BASE": "f4a6c9b3c95e41c82048423d3493a81ec3fa810e",
    "DA3MONO-LARGE": "f465978e618db8cc79c83b8bbf24964857db1875",
}
GGUF_REPO = "mudler/depth-anything.cpp-gguf"
GGUF_REVISION = "c6777c9f402fc5e2b840c0e5ac42ef1286763547"
GGUF_WEIGHTS = {
    "DA3-SMALL": ("depth-anything-small-f32.gguf", "eab5597e01dedde1a20c038590ae8c887b85ec35b882581138c08308e92c41e5"),
    "DA3-BASE": ("depth-anything-base-q4_k.gguf", "43cd45d00f9024f4319f4beabd73155db5132e4b575bc52eff4131262c9d78f1"),
    "DA3MONO-LARGE": ("depth-anything-mono-large-f32.gguf", "291b1a554af907c3f79986ee225da8933be5f7a31d73c81d06784cda284535de"),
}
MIGAN_REPO = "andraniksargsyan/migan"
MIGAN_REVISION = "406830d0fa60666da0071c342ad2fbc8f30c5c64"
MIGAN_FILENAME = "migan_pipeline_v2.onnx"
MIGAN_SHA256 = "6f1f3530a1a2324b19752018ce756088b07973cda8d7d890034ace5c8a48c40b"
MIGAN_LICENSE = "MIT"
_CACHE_FORMAT = "depth-25d-v1"


@dataclass(frozen=True)
class DepthPlate:
    source: Path
    depth: Path
    background: Path
    vertex_depth: Path
    model: DepthModel
    backend: str
    grid_size: tuple[int, int]
    mask_fraction: float


def _verified_weights(repo, revision, name, expected):
    from huggingface_hub import hf_hub_download

    path = Path(hf_hub_download(repo, name, revision=revision, token=False))
    with path.open("rb") as weights:
        if file_digest(weights, "sha256").hexdigest() != expected:
            raise ValueError(f"Os pesos de {name} não correspondem ao SHA-256 aprovado.")
    return path


def _device_for(device):
    if device not in get_args(DepthDevice):
        raise ValueError("depth_device precisa ser auto, cpu ou cuda.")
    if device != "cpu":
        try:
            import torch
        except ImportError:
            if device == "cuda":
                raise RuntimeError("depth_device=cuda requer PyTorch com CUDA.") from None
        else:
            if torch.cuda.is_available():
                return "cuda"
        if device == "cuda":
            raise RuntimeError("depth_device=cuda foi solicitado, mas CUDA não está disponível.")
    return "cpu"


def _depth_cuda(rgb, model):
    import torch

    try:
        from depth_anything_3.api import DepthAnything3
    except ImportError as error:
        raise RuntimeError("Instale Depth Anything 3 e as dependências de inferência para a GPU.") from error

    network = DepthAnything3.from_pretrained(
        f"depth-anything/{model}", revision=DA3_REVISION[model], token=False
    ).to(device="cuda").eval()
    try:
        with torch.inference_mode():
            prediction = network.inference([rgb], process_res=504)
        return prediction.depth[0]
    finally:
        # The editorial VLM can use the same L4 later in the render.
        del network
        torch.cuda.empty_cache()


def _read_pfm(path):
    with path.open("rb") as source:
        if source.readline().strip() != b"Pf":
            raise ValueError("da3-cli não retornou um mapa PFM monocromático.")
        width, height = map(int, source.readline().split())
        scale = float(source.readline())
        if not (0 < width <= 8192 and 0 < height <= 8192 and np.isfinite(scale) and scale != 0):
            raise ValueError("Dimensões ou escala inválidas no mapa de profundidade PFM.")
        pixels = np.frombuffer(source.read(), dtype="<f4" if scale < 0 else ">f4")
    if pixels.size != width * height:
        raise ValueError("O mapa de profundidade PFM está truncado.")
    return np.flipud(pixels.reshape(height, width)) * abs(scale)


def _depth_cpp(rgb, model):
    cli = os.environ.get("FRIMERISE_DA3_CLI") or shutil.which("da3-cli")
    if not cli:
        raise RuntimeError("Instale depth-anything.cpp ou indique o binário em FRIMERISE_DA3_CLI.")
    filename, expected = GGUF_WEIGHTS[model]
    weights = _verified_weights(GGUF_REPO, GGUF_REVISION, filename, expected)
    with TemporaryDirectory() as work:
        image = Path(work) / "source.png"
        pfm = Path(work) / "depth.pfm"
        Image.fromarray(rgb).save(image)
        result = subprocess.run(
            [cli, "depth", "--model", str(weights), "--input", str(image), "--pfm", str(pfm)],
            env={**os.environ, "DA_DEVICE": "cpu"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=300, check=False,
        )
        if result.returncode or not pfm.is_file():
            raise RuntimeError(f"depth-anything.cpp falhou ao gerar profundidade de {model} na CPU.")
        return _read_pfm(pfm)


def normalize_depth(prediction, size):
    """Use robust percentiles to encode 0=far and 65535=near in a grayscale PNG."""
    raw = np.asarray(prediction, dtype=np.float32)
    if raw.ndim != 2 or not raw.size:
        raise ValueError("A profundidade DA3 precisa ser uma imagem 2D.")
    valid = np.isfinite(raw) & (raw > 0)
    if not valid.any():
        raise ValueError("A profundidade DA3 não contém valores positivos finitos.")
    lo, hi = np.percentile(raw[valid], (2, 98))
    if hi - lo <= 1e-6 * max(1, abs(float(hi))):
        near = np.full(raw.shape, .5, dtype=np.float32)
    else:
        near = np.where(valid, np.clip((hi - raw) / (hi - lo), 0, 1), .5).astype(np.float32)
    scaled = Image.fromarray(near).resize(size, Image.Resampling.BILINEAR)
    return np.rint(np.asarray(scaled) * 65535).astype(np.uint16)


def disocclusion_mask(depth):
    """Dilate depth discontinuities and return True where the background needs filling."""
    from scipy.ndimage import maximum_filter

    normalized = np.asarray(depth, dtype=np.float32) / 65535
    dy, dx = np.gradient(normalized)
    gradient = np.hypot(dx, dy)
    edges = gradient >= max(.024, float(np.quantile(gradient, .96)))
    radius = max(3, round(min(normalized.shape) * .027))
    return maximum_filter(edges, size=2 * radius + 1, mode="nearest")


@lru_cache(maxsize=1)
def _migan_session():
    import onnxruntime as ort

    path = _verified_weights(MIGAN_REPO, MIGAN_REVISION, MIGAN_FILENAME, MIGAN_SHA256)
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def _inpaint_migan(rgb, holes):
    """Use the official uint8 NCHW pipeline. Its mask is 255 where pixels are known."""
    image = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
    known = np.ascontiguousarray((~holes)[None, None].astype(np.uint8) * 255)
    (result,) = _migan_session().run(["result"], {"image": image, "mask": known})
    if result.shape != image.shape or result.dtype != np.uint8:
        raise RuntimeError("MI-GAN retornou pixels com formato incompatível.")
    painted = result[0].transpose(1, 2, 0)
    return np.where(holes[:, :, None], painted, rgb)


def prepare_depth_plate(source, *, model=DEFAULT_DEPTH_MODEL, device="auto"):
    """Generate the registered image, 16-bit depth and MI-GAN plate once per input."""
    if model not in DA3_REVISION:
        raise ValueError("Depth Anything 3 exige DA3-SMALL, DA3-BASE ou DA3MONO-LARGE (Apache-2.0).")
    selected = _device_for(device)
    backend = "pytorch_cuda" if selected == "cuda" else "depth-anything.cpp"
    with Path(source).open("rb") as data:
        image_hash = file_digest(data, "sha256").hexdigest()
    revision = DA3_REVISION[model] if selected == "cuda" else GGUF_REVISION
    key = sha256(f"{_CACHE_FORMAT}|{image_hash}|{model}|{revision}|{backend}|{MIGAN_SHA256}".encode()).hexdigest()
    cache = Path(os.environ.get("FRIMERISE_DEPTH_CACHE", ".cache/depth_25d")).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    folder = cache / key
    with (cache / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not folder.is_dir():
            with TemporaryDirectory(dir=cache) as work:
                staging = Path(work) / "plate"
                staging.mkdir()
                with Image.open(source) as image:
                    rgb_image = ImageOps.exif_transpose(image).convert("RGB")
                rgb_image.thumbnail((1120, 1120), Image.Resampling.LANCZOS)
                if min(rgb_image.size) < 16:
                    raise ValueError("photo_25d_parallax precisa de uma imagem de pelo menos 16×16.")
                rgb = np.asarray(rgb_image)
                prediction = _depth_cuda(rgb, model) if selected == "cuda" else _depth_cpp(rgb, model)
                depth = normalize_depth(prediction, rgb_image.size)
                holes = disocclusion_mask(depth)
                background = _inpaint_migan(rgb, holes) if holes.any() else rgb
                rgb_image.save(staging / "source.png")
                Image.fromarray(depth).save(staging / "depth.png")
                Image.fromarray(background).save(staging / "bg_inpainted.png")
                width, height = rgb_image.size
                scale = 144 / max(width, height)
                grid_size = (max(2, round(width * scale)), max(2, round(height * scale)))
                grid = Image.fromarray(depth.astype(np.float32)).resize(grid_size, Image.Resampling.BILINEAR)
                np.rint(np.asarray(grid)).astype("<u2").tofile(staging / "depth-grid.bin")
                (staging / "metadata.json").write_text(
                    json.dumps({"grid_size": grid_size, "mask_fraction": float(np.mean(holes))}), encoding="utf-8"
                )
                staging.rename(folder)
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        grid_size = tuple(metadata["grid_size"])
        return DepthPlate(folder / "source.png", folder / "depth.png", folder / "bg_inpainted.png",
                          folder / "depth-grid.bin", model, backend, grid_size, metadata["mask_fraction"])

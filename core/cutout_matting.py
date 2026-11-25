"""Remove photo backgrounds with the two approved MIT BiRefNet checkpoints."""

import fcntl
import os
from functools import cache
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageOps, PngImagePlugin

CPU_MODEL = "birefnet-general-lite"
CPU_WEIGHTS = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
CPU_URL = ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
           "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx")
GPU_MODEL = "ZhengPeng7/BiRefNet"
GPU_REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"
MODEL_LICENSES = {CPU_MODEL: "MIT", GPU_MODEL: "MIT"}
APPROVED_MODELS = frozenset(MODEL_LICENSES)


def _device(requested):
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("device precisa ser auto, cpu ou cuda")
    if requested != "cpu":
        try:
            import torch
        except (ImportError, OSError):
            pass
        else:
            if torch.cuda.is_available():
                return "cuda"
    if requested == "cuda":
        raise RuntimeError("BiRefNet em FP16 requer PyTorch com uma GPU CUDA disponível")
    return "cpu"


@cache
def _cpu_session():
    try:
        import onnxruntime as ort
        import pooch
        from rembg.sessions.birefnet_general_lite import BiRefNetSessionGeneralLite
    except (ImportError, OSError) as error:
        raise RuntimeError("Para recortes no CPU, instale requirements-matting-cpu.txt") from error

    class VerifiedLite(BiRefNetSessionGeneralLite):
        @classmethod
        def download_models(cls, **_):
            model_dir = Path(os.environ.get("FRIMERISE_MODEL_CACHE", ".cache/models"))
            return pooch.retrieve(CPU_URL, f"sha256:{CPU_WEIGHTS}", path=model_dir, fname=f"{CPU_MODEL}.onnx")

    # Two threads and a bounded arena keep ONNX inference usable on an 8 GB machine.
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    return VerifiedLite(CPU_MODEL, options, providers=["CPUExecutionProvider"])


@cache
def _gpu_session():
    try:
        import torch
        from transformers import AutoModelForImageSegmentation
    except (ImportError, OSError) as error:
        raise RuntimeError("Para recortes na L4, instale requirements-matting-colab.txt") from error

    # The model defines its Transformers architecture in its pinned, MIT model repository.
    return AutoModelForImageSegmentation.from_pretrained(
        GPU_MODEL, revision=GPU_REVISION, code_revision=GPU_REVISION, trust_remote_code=True,
        use_safetensors=True, torch_dtype=torch.float16,
    ).eval().to(device="cuda", dtype=torch.float16)


def _predict(image, device):
    if device == "cpu":
        return _cpu_session().predict(image)[0]

    import torch

    pixels = np.asarray(image.resize((1024, 1024), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    rgb = (pixels - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
    batch = torch.from_numpy(rgb.transpose(2, 0, 1)[None]).to(device="cuda", dtype=torch.float16)
    with torch.inference_mode():
        alpha = _gpu_session()(batch)[-1].float().sigmoid()[0, 0].cpu().numpy()
    mask = Image.fromarray(np.rint(alpha * 255).astype(np.uint8))
    return mask.resize(image.size, Image.Resampling.LANCZOS)


def _decontaminate(image, mask):
    from scipy.ndimage import distance_transform_edt

    matte = np.asarray(mask, dtype=np.float32) / 255
    matte = np.clip((matte - .025) / .96, 0, 1)
    original = np.asarray(image.getchannel("A"), dtype=np.float32) / 255
    alpha = matte * original
    pixels = np.array(image)
    solid = alpha >= .96
    edge = (alpha > 0) & ~solid
    if solid.any() and edge.any():
        # In translucent edge pixels, replace spill with the nearest opaque subject color.
        yy, xx = distance_transform_edt(~solid, return_distances=False, return_indices=True)
        weight = matte[edge, None] ** 2
        inner = pixels[yy[edge], xx[edge], :3]
        pixels[edge, :3] = np.rint(weight * pixels[edge, :3] + (1 - weight) * inner)
    pixels[alpha == 0, :3] = 0
    pixels[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    return Image.fromarray(pixels)


def matting_model(path):
    """Read the approved model recorded in a generated RGBA PNG."""
    with Image.open(path) as image:
        name = image.info.get("matting_model")
        license_name = image.info.get("matting_license")
    if name not in APPROVED_MODELS or license_name != MODEL_LICENSES[name]:
        raise ValueError("O PNG não identifica um modelo BiRefNet aprovado")
    return name


def remove_background(image_path, device="auto") -> Path:
    """Return a cached, straight-alpha PNG using only a pinned MIT checkpoint."""
    device = _device(device)
    model, revision = (GPU_MODEL, GPU_REVISION) if device == "cuda" else (CPU_MODEL, CPU_WEIGHTS)
    with Path(image_path).open("rb") as source:
        digest = file_digest(source, "sha256").hexdigest()
    key = sha256(f"{digest}:{model}:{revision}:edge-v1".encode()).hexdigest()
    root = Path(os.environ.get("FRIMERISE_CUTOUT_CACHE", ".cache/cutouts")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"{key}.png"
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.is_file():
            return output
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
        subject = _decontaminate(image, _predict(image.convert("RGB"), device))
        if subject.getchannel("A").getbbox() is None:
            raise ValueError("BiRefNet não encontrou um sujeito visível na imagem")
        info = PngImagePlugin.PngInfo()
        info.add_text("matting_model", model)
        info.add_text("matting_license", MODEL_LICENSES[model])
        with TemporaryDirectory(dir=root) as directory:
            stage = Path(directory) / output.name
            subject.save(stage, pnginfo=info)
            stage.replace(output)
    return output

"""Prepare stills and FFmpeg filter graphs with the bundled RGB 3D LUTs."""

import fcntl
import os
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from PIL import Image

LutName = Literal["editorial_teal_amber", "vintage_paper_warm", "doc_clean_neutral"]
LUT_DIR = Path(__file__).resolve().parents[1] / "assets" / "luts"
PROFILES = {
    # contrast, saturation, black lift, shadow tint, highlight tint
    "editorial_teal_amber": (1.13, .87, .012, (-.045, .018, .032), (.055, .014, -.047)),
    "vintage_paper_warm": (.97, .70, .031, (.038, .016, -.024), (.040, .016, -.046)),
    "doc_clean_neutral": (1.04, .96, .010, (-.003, .004, .008), (.010, .005, -.006)),
}


def lut_name(value):
    if not isinstance(value, str):
        raise ValueError("O campo lut precisa ser o nome de um preset")
    name = value.removesuffix(".cube")
    if name not in PROFILES:
        raise ValueError(f"LUT desconhecida {value!r}. Escolha uma das LUTs em assets/luts/")
    return name


def _escape(text, characters):
    return "".join(("\\" if character in characters else "") + character for character in text)


def lut_filter(value):
    path = LUT_DIR / f"{lut_name(value)}.cube"
    # FFmpeg parses the option value and the filter graph at separate levels.
    filename = _escape(_escape(str(path), "\\':"), "\\'[],;")
    return f"format=gbrp,lut3d=file={filename}:interp=tetrahedral,noise=alls=3:allf=t"


def grade_photo(image_path, lut) -> Path:
    """Apply FFmpeg grading once per source+LUT, preserving a cutout's exact alpha."""
    from .encoder import run_ffmpeg

    image_path = Path(image_path)
    name = lut_name(lut)
    with image_path.open("rb") as source, (LUT_DIR / f"{name}.cube").open("rb") as table:
        key = sha256((file_digest(source, "sha256").hexdigest()
                      + file_digest(table, "sha256").hexdigest() + "still-v1").encode()).hexdigest()
    root = Path(os.environ.get("FRIMERISE_GRADE_CACHE", ".cache/grades")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"{key}.png"
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.is_file():
            return output
        with TemporaryDirectory(dir=root) as directory:
            staged = Path(directory) / output.name
            run_ffmpeg("-v", "error", "-filter_threads", "1", "-i", str(image_path),
                       "-vf", lut_filter(name), "-frames:v", "1", "-threads", "1", "-pix_fmt", "rgb24", str(staged))
            with Image.open(image_path) as original:
                if "A" in original.getbands() or "transparency" in original.info:
                    with Image.open(staged) as graded:
                        rgba = graded.convert("RGBA")
                    rgba.putalpha(original.convert("RGBA").getchannel("A"))
                    rgba.save(staged)
            staged.replace(output)
    return output


def _grade(rgb, profile):
    contrast, saturation, lift, shadow, light = profile
    luma = sum(value * weight for value, weight in zip(rgb, (.2126, .7152, .0722), strict=True))
    mid = (luma - .5) * contrast + .5
    result = []
    for value, dark_tint, light_tint in zip(rgb, shadow, light, strict=True):
        component = mid + (value - luma) * saturation + lift
        component += dark_tint * (1 - luma) ** 2 + light_tint * luma ** 2
        result.append(min(1, max(0, component)))
    return result


def generate_luts(directory=LUT_DIR, *, size=17):
    """Write valid Iridas .cube tables with the red channel changing fastest."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, profile in PROFILES.items():
        with (directory / f"{name}.cube").open("w", encoding="ascii") as output:
            output.write(f'TITLE "{name}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
            for blue in range(size):
                for green in range(size):
                    for red in range(size):
                        rgb = (red / (size - 1), green / (size - 1), blue / (size - 1))
                        output.write(" ".join(f"{value:.6f}" for value in _grade(rgb, profile)) + "\n")


if __name__ == "__main__":
    generate_luts()

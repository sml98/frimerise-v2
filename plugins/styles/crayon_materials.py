"""Pressure, paper tooth and wax-resist wash from Crayon Book's gl.js (MIT)."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageColor, ImageFilter

PAPER = "#f4efe3"
INDIGO = "#2d3263"
BROWN = "#694632"
WHITE_WAX = "#fbfaf4"


def smoothstep(low, high, value):
    fraction = np.clip((value - low) / (high - low), 0, 1)
    return fraction * fraction * (3 - 2 * fraction)


def _hash(x, y):
    x = np.remainder(x * 123.34, 1)
    y = np.remainder(y * 456.21, 1)
    dot = x * (x + 45.32) + y * (y + 45.32)
    return np.remainder((x + dot) * (y + dot), 1)


def _noise(x, y):
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    top = _hash(ix, iy) * (1 - fx) + _hash(ix + 1, iy) * fx
    bottom = _hash(ix, iy + 1) * (1 - fx) + _hash(ix + 1, iy + 1) * fx
    return top * (1 - fy) + bottom * fy


@dataclass(frozen=True)
class PaperStock:
    tooth: np.ndarray
    paper: np.ndarray
    damp: np.ndarray
    hatching: np.ndarray


@lru_cache(maxsize=4)
def stock(size):
    width, height = size
    y, x = np.ogrid[:height, :width]
    x, y = x.astype(np.float32), y.astype(np.float32)
    raw = (.34 * _noise(x / 1.35, y / 1.35) + .33 * _noise(x / 2.9 + 17, y / 2.9 + 17)
           + .21 * _noise(x / 6.3 + 41, y / 6.3 + 41)
           + .22 * (_noise(x / 10 + 7, y / 1.9 + 7) - .5)
           + .08 * (_noise(x / 2.1 + 71, y / 13 + 71) - .5))
    tooth = smoothstep(.16, .86, raw + .06)
    mottling = .6 * _noise(x / 180, y / 180) + .4 * _noise(x / 60 + 9, y / 60 + 9)
    # Light grazing over the fibers is strongest in exposed paper and shallow wax.
    relief = np.diff(tooth, axis=1, prepend=tooth[:, :1]) + .45 * np.diff(tooth, axis=0, prepend=tooth[:1, :])
    light = .975 + .035 * mottling + .13 * relief
    paper = light[..., None] * np.asarray(ImageColor.getrgb(PAPER), dtype=np.float32)
    damp = (.72 + .42 * _noise(x / 46 + 23, y / 46 + 23)) * (1 + .32 * (1 - tooth) ** 2)

    # crayon.js rotates the hatch rows and varies their spacing and pressure.
    across, along = (-.58 * x + .815 * y), (.815 * x + .58 * y)
    rows = across / 5.6 + .7 * _noise(x / 37 + 8, y / 37 + 8)
    row_index = np.floor(rows)
    distance = abs(rows - row_index - .48 - .16 * (_hash(row_index, 13) - .5))
    ribbon = 1 - smoothstep(.12, .36, distance)
    hatching = ribbon * (.68 + .32 * _noise(along / 17, row_index + 7))
    return PaperStock(tooth, paper, damp, hatching)


class Sheet:
    def __init__(self, size):
        self.size = size
        self.stock = stock(size)
        self.rgb = self.stock.paper.copy()
        self.wax = np.zeros(self.stock.tooth.shape, dtype=np.float32)

    def copy(self):
        sheet = object.__new__(Sheet)
        sheet.size, sheet.stock = self.size, self.stock
        sheet.rgb, sheet.wax = self.rgb.copy(), self.wax.copy()
        return sheet

    def paint(self, mask, color, *, strength=.74, hatch=.13, step=0, origin=(0, 0), knock=False):
        """Lay a stroke or sprite on the sheet. A front silhouette clears pigment underneath."""
        x, y = origin
        left, top = max(0, x), max(0, y)
        right, bottom = min(self.size[0], x + mask.width), min(self.size[1], y + mask.height)
        if left >= right or top >= bottom:
            return
        box = (left - x, top - y, right - x, bottom - y)
        shape = np.asarray(mask.crop(box), dtype=np.float32) / 255
        region = np.s_[top:bottom, left:right]
        paper, tooth = self.stock.paper[region], self.stock.tooth[region]
        rgb, wax = self.rgb[region], self.wax[region]
        if isinstance(color, Image.Image):
            pigment = np.asarray(color.crop(box), dtype=np.float32)
            # Thin outlines need more pressure than the broad fill.
            dark = np.max(pigment, axis=2) < 115
        else:
            pigment = np.asarray(ImageColor.getrgb(color), dtype=np.float32)
            dark = max(pigment) < 115
        if knock:
            rgb += (paper - rgb) * shape[..., None]
            wax *= 1 - shape
        phase = (step % 5) - 2
        phase = max(-left, min(phase, self.size[0] - right))
        hatch_rows = self.stock.hatching[top:bottom, left + phase:right + phase]
        pressure = np.clip(shape * (strength + hatch * hatch_rows + .12 * dark), 0, .99)
        threshold = 1 - 1.22 * pressure
        coverage = smoothstep(threshold - .07, threshold + .07, tooth) * smoothstep(0, .18, pressure) * shape
        tint = 1 - .06 * pressure ** 2
        rgb += (pigment * tint[..., None] - rgb) * coverage[..., None]
        wax += (1 - wax) * coverage

    def image(self, wash=None):
        rgb = self.rgb
        if wash is not None:
            deposit, transmission = wash
            resist = 1 - .93 * smoothstep(.12, .55, self.wax)
            density = deposit * resist
            rgb = rgb * (1 - density[..., None] * (1 - transmission))
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def wash_pigment(mask, transmission):
    """Prepare mottling and two scales of pigment pooling along a wet boundary."""
    density = np.asarray(mask, dtype=np.float32) / 255
    fine = np.asarray(mask.filter(ImageFilter.GaussianBlur(2.4)), dtype=np.float32) / 255
    broad = np.asarray(mask.filter(ImageFilter.GaussianBlur(12)), dtype=np.float32) / 255
    edge = .45 * np.maximum(density - fine, 0) + .28 * np.maximum(density - broad, 0)
    deposit = np.clip(density * stock(mask.size).damp + edge, 0, 1.25)
    return deposit, np.asarray(transmission, dtype=np.float32)

"""Draw visible contour prefixes and zigzag hatch rows before a page comes to life."""

from dataclasses import dataclass
from math import ceil, hypot

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from plugins.styles.crayon_materials import Sheet

OPENING_SECONDS = .90


@dataclass(frozen=True)
class Stroke:
    points: tuple
    distances: np.ndarray

    @classmethod
    def from_points(cls, points):
        points = tuple(points)
        return cls(points, np.cumsum([0, *(hypot(b[0] - a[0], b[1] - a[1])
                                           for a, b in zip(points, points[1:]))]))

    @property
    def length(self):
        return float(self.distances[-1])

    def prefix(self, distance):
        if distance >= self.length:
            return self.points
        index = int(np.searchsorted(self.distances, distance, side="right"))
        a, b = self.points[index - 1:index + 1]
        u = (distance - self.distances[index - 1]) / (self.distances[index] - self.distances[index - 1])
        return (*self.points[:index], (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))


def _contours(mask, stride, position):
    """Walk the oriented grid edges of each visible component, including its holes."""
    padded = np.pad(mask, 1)
    outgoing = {}
    for neighbor, a, b in ((padded[:-2, 1:-1], (0, 0), (1, 0)),
                           (padded[1:-1, 2:], (1, 0), (1, 1)),
                           (padded[2:, 1:-1], (1, 1), (0, 1)),
                           (padded[1:-1, :-2], (0, 1), (0, 0))):
        for row, col in zip(*np.nonzero(mask & ~neighbor), strict=True):
            outgoing.setdefault((col + a[0], row + a[1]), []).append((col + b[0], row + b[1]))
    paths = []
    while outgoing:
        first = point = next(iter(outgoing))
        points = [first]
        while point in outgoing:
            ends = outgoing[point]
            end = ends.pop()
            if not ends:
                del outgoing[point]
            points.append(end)
            if end == first:
                break
            point = end
        if len(points) > 5:
            x, y = position
            paths.append(Stroke.from_points((x + col * stride, y + row * stride) for col, row in points))
    return sorted(paths, key=lambda path: path.length, reverse=True)


def sprite_strokes(image, position):
    stride = max(1, ceil(max(image.size) / 245))
    size = tuple(max(1, ceil(length / stride)) for length in image.size)
    data = np.asarray(image.resize(size, Image.Resampling.BOX))
    shape = data[:, :, 3] > 108
    ink = shape & (np.max(data[:, :, :3], axis=2) < 132)
    return _contours(shape, stride, position)[:5] + _contours(ink, stride, position)[:12]


def _hatch_order(size):
    """The rotated rows progress in alternating directions, as in crayon.js fill()."""
    width, height = size
    y, x = np.ogrid[:height, :width]
    across = -.58 * x + .815 * y
    along = (.815 * x + .58 * y) / (.815 * width + .58 * height)
    row = np.floor((across - across.min()) / max(3.2, min(size) / 91))
    return np.asarray((row + np.where(row % 2 == 0, along, 1 - along)) / (row.max() + 1), dtype=np.float32)


def _page_hatches(size, groups):
    # Each shape gets its own pass. A colored foreground sprite doesn't wait for
    # the same diagonal to reach the field or object drawn behind it.
    order = .18 + .82 * _hatch_order(size)
    width, height = size
    for index in reversed(range(len(groups))):
        sprite, (x, y) = groups[index]
        left, top = max(0, x), max(0, y)
        right, bottom = min(width, x + sprite.width), min(height, y + sprite.height)
        if left >= right or top >= bottom:
            continue
        box = (left - x, top - y, right - x, bottom - y)
        inside = np.asarray(sprite.getchannel("A").crop(box)) > 105
        rows = _hatch_order(sprite.size)[box[1]:box[3], box[0]:box[2]]
        start = min(.31, .08 * index)
        region = order[top:bottom, left:right]
        region[inside] = start + (.82 - start / 2) * rows[inside]
    return order


def _stroke_prefix(mask, strokes, progress, width):
    budget = sum(stroke.length for stroke in strokes) * min(1, progress)
    draw = ImageDraw.Draw(mask)
    tip = None
    for stroke in strokes:
        if budget <= 0:
            break
        points = stroke.prefix(budget)
        draw.line(points, fill=255, width=width, joint="curve")
        budget -= stroke.length
        if len(points) >= 2:
            tip = points[-2], points[-1]
    return tip


def _crayon_tip(image, tip, alpha):
    (ax, ay), (x, y) = tip
    length = hypot(x - ax, y - ay)
    if not length:
        return
    unit = max(1.0, min(image.size) / 385)
    # Tilt the visible barrel away from the paper, keeping its nib on the live point.
    vx, vy = (x - ax) / length, (y - ay) / length
    bx, by = -.64 * vx + .60 * vy, -.64 * vy - .60 * vx
    origin = round(x) - 22, round(y) - 22
    stamp = Image.new("RGBA", (45, 45))
    draw = ImageDraw.Draw(stamp)

    def point(back, side):
        return (x - origin[0] + unit * (back * bx - side * by),
                y - origin[1] + unit * (back * by + side * bx))

    draw.polygon([point(0, 0), point(4, -2), point(13, -2.3), point(13, 2.3), point(4, 2)],
                 fill=(65, 64, 103, round(alpha * 221)))
    draw.polygon([point(7, -2), point(12, -2.3), point(12, 2.3), point(7, 2)],
                 fill=(147, 140, 168, round(alpha * 220)))
    image.paste(stamp, origin, stamp)


class DrawOn:
    def __init__(self, size, groups, landscape):
        sheet = Sheet(size)
        self.paper = sheet.image()
        self.order = _page_hatches(size, groups)
        self.ribbon = sheet.stock.hatching > .17
        paths = [sprite_strokes(image, position) for image, position in groups]
        self.principal = tuple(paths[0][:1]) if paths else ()
        self.details = tuple(stroke for index, group in enumerate(paths)
                             for stroke in (group[1:] if index == 0 else group)) + tuple(landscape)

    def frame(self, image, at):
        if at <= 0:
            return self.paper
        if at >= OPENING_SECONDS:
            return image
        size = image.size
        outline = Image.new("L", size)
        width = max(3, round(min(size) * .012))
        _stroke_prefix(outline, self.details, max(0, (at - .17) / .44), width)
        tip = _stroke_prefix(outline, self.principal, at / .46, width)
        if at > .18:
            # Each row lays down a narrow crayon ribbon, then the overlapping
            # passes close the gaps behind it. The light of the paper stays between strokes.
            progress = (at - .18) / .72
            close = max(0, progress - .21 * min(1, (1 - progress) * 5))
            deposited = (self.order < close) | (self.ribbon & (self.order < progress))
            rows = Image.fromarray(np.asarray(deposited, dtype=np.uint8) * 255)
            outline.paste(255, (0, 0, *size), rows)
        image = Image.composite(image, self.paper, outline.filter(ImageFilter.GaussianBlur(.42)))
        if tip and at < .49:
            _crayon_tip(image, tip, min(1, at / .06, (.49 - at) / .07))
        return image

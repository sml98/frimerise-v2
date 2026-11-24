"""Hand-cut silhouettes for the editorial layouts and a loader for supplied PNG/SVG art."""

import json
from functools import lru_cache
from io import BytesIO
from math import cos, radians, sin, tau
from pathlib import Path
from random import Random

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .paper_fx import ASSETS, DISPLAY, INK, RED, TYPEWRITER, cutout, paper_card


def place(canvas, image, center):
    canvas.alpha_composite(image, (round(center[0] - image.width / 2), round(center[1] - image.height / 2)))


def _pivot(canvas, part, at, local, angle):
    """Rotate a piece and preserve the location of its hinge after expanding the bounds."""
    turn = part.rotate(-angle, Image.Resampling.BICUBIC, expand=True)
    x, y = local[0] - part.width / 2, local[1] - part.height / 2
    c, s = cos(radians(angle)), sin(radians(angle))
    offset = (x * c - y * s + turn.width / 2, x * s + y * c + turn.height / 2)
    canvas.alpha_composite(turn, (round(at[0] - offset[0]), round(at[1] - offset[1])))


def _piece(size):
    image = Image.new("RGBA", size)
    return image, ImageDraw.Draw(image)


def _tip(point, length, angle):
    return point[0] + length * sin(angle), point[1] + length * cos(angle)


def _emu_leg(draw, hip, stride, side):
    knee = _tip(hip, 43, .30 + .60 * stride)
    ankle = _tip(knee, 51, -.12 - (.85 if stride > 0 else .46) * stride)
    leg = "#88745c" if side else "#51483d"
    draw.line((hip, knee, ankle), fill="#322e28", width=10, joint="curve")
    draw.line((hip, knee, ankle), fill=leg, width=6, joint="curve")
    x, y = ankle
    draw.line((x - 7, y + 3, x, y, x + 20, y + 2), fill=leg, width=5, joint="curve")
    draw.line((x + 2, y, x + 16, y + 7), fill=leg, width=3)
    draw.line((x + 1, y, x + 13, y - 3), fill=leg, width=3)


@lru_cache(maxsize=8)
def _emu_body(tone):
    image, d = _piece((298, 286))
    d.polygon([(31, 124), (42, 116), (53, 118), (66, 105), (87, 99), (109, 91), (138, 93),
               (161, 100), (180, 114), (192, 135), (178, 154), (156, 168), (127, 171), (110, 166),
               (91, 169), (82, 162), (67, 162), (55, 152), (39, 148), (49, 141), (32, 138), (43, 132)],
              fill=tone, outline="#362e25", width=2)
    d.polygon([(66, 126), (95, 110), (122, 109), (158, 117), (168, 132), (149, 148), (113, 152), (84, 143)],
              fill="#887564", outline="#4e4035", width=2)
    rng = Random(1932)
    # Engraved, tapered barbs on the torso, clipped to the body silhouette.
    hatch, hd = _piece(image.size)
    for row in range(9):
        for i in range(19):
            x = 37 + 8 * i + rng.uniform(-4, 4)
            y = 101 + 8 * row + rng.uniform(-3, 3)
            length = rng.uniform(7, 22)
            hd.line((x, y, x - length * .55, y + length * .4, x - length, y + length * .63),
                    fill=rng.choice(("#b19a7b99", "#392e258e", "#ddc09965")), width=rng.choice((1, 2)))
    from PIL import ImageChops
    hatch.putalpha(ImageChops.multiply(hatch.getchannel("A"), image.getchannel("A")))
    image.alpha_composite(hatch)
    return image


@lru_cache(maxsize=1)
def _emu_neck():
    image, d = _piece((90, 116))
    points = [(21, 103), (30, 91), (44, 63), (46, 42), (42, 20), (50, 14), (61, 20),
              (65, 43), (62, 65), (55, 84), (50, 103), (40, 111)]
    d.polygon(points, fill="#788582", outline="#363a36", width=2)
    d.line([(37, 94), (50, 66), (54, 44), (49, 22)], fill="#a8a292", width=3)
    for k in range(16):
        x, y = 39 + k * .8, 92 - k * 4
        d.line((x, y, x + 8, y - 4), fill="#515b53", width=1)
    return image


@lru_cache(maxsize=4)
def _emu_head(blink):
    image, d = _piece((75, 46))
    d.polygon([(9, 21), (14, 9), (22, 8), (23, 3), (30, 7), (37, 5), (47, 11),
               (50, 20), (70, 25), (67, 30), (48, 30), (43, 37), (24, 39), (12, 31)],
              fill="#504a3a", outline=INK, width=2)
    d.polygon([(41, 19), (48, 20), (69, 25), (68, 30), (49, 30), (43, 28)], fill="#857f72")
    d.line((50, 27, 67, 28), fill=INK, width=1)
    if blink:
        d.line((33, 19, 42, 20), fill=INK, width=2)
    else:
        d.ellipse((33, 14, 43, 23), fill="#a76034")
        d.ellipse((36, 15, 42, 21), fill="#211e18")
        d.point((39, 16), fill="#fff2cd")
    return image


@lru_cache(maxsize=180)
def emu(width, phase, state="run", flip=False, variant=0):
    image, draw = _piece((298, 286))
    angle = phase * tau / 12
    stride = sin(angle) if state == "run" else 0
    _emu_leg(draw, (134, 151), -stride, False)
    _emu_leg(draw, (111, 153), stride, True)
    neck = (159, 132)
    turn, narrow = False, False
    close = phase == 9
    chest = 0
    head_tilt = 4 * cos(angle)
    tilt = (18 if state == "run" else -4) + 9 * sin(angle) + 3 * variant
    if state == "look":
        peck = {24: .18, 25: .54, 26: 1.0, 27: .90, 28: .50, 29: .21}.get(phase, 0)
        turn = 10 <= phase <= 19
        narrow = phase in (9, 10, 19, 20)
        close = phase in (12, 26, 36)
        tilt = -8 + 7 * sin(phase * tau / 42) + peck * 67 - (9 if turn else 0)
        head_tilt = -6 + peck * 31 + 5 * cos(phase * tau / 42)
        chest = 3 * peck
    elif state == "proud":
        beat = phase * tau / 24
        chest = -4 - 6 * sin(beat)
        neck = (159, 132 + 5 * sin(beat))
        tilt = -14 + 13 * sin(beat - .40)
        head_tilt = -7 + 8 * cos(beat)
        close = phase == 16
    _pivot(image, _emu_neck(), neck, (31, 102), tilt)
    body = _emu_body(("#5d5143", "#6e614d", "#514b42")[variant % 3])
    if chest:
        _pivot(image, body, (125, 153), (125, 153), chest)
    else:
        image.alpha_composite(body)
    c, s = cos(radians(tilt)), sin(radians(tilt))
    x, y = 21, -76
    head = neck[0] + x * c - y * s, neck[1] + x * s + y * c
    face = _emu_head(close)
    anchor = 20
    if turn:
        face = ImageOps.mirror(face)
        anchor = face.width - anchor
    if narrow:
        face = face.resize((round(face.width * .65), face.height), Image.Resampling.LANCZOS)
        anchor *= .65
    _pivot(image, face, head, (anchor, 30), tilt * .31 + head_tilt)
    if state in {"look", "proud"}:
        # The feet keep their place and scale as the neckline and beak move.
        image = image.crop((25, 7, 297, 260))
        if flip:
            image = ImageOps.mirror(image)
        return _finish(image, width, seed=18 + variant, crop=False)
    if flip:
        image = ImageOps.mirror(image)
    return _finish(image, width, seed=18 + variant)


@lru_cache(maxsize=2)
def _soldier_head(officer):
    image, d = _piece((76, 83))
    skin = "#b88360" if officer else "#bd9777"
    d.polygon([(22, 29), (52, 30), (59, 41), (55, 50), (59, 54), (52, 58), (48, 70), (29, 72),
               (22, 63), (21, 51), (17, 47)], fill=skin, outline=INK, width=2)
    d.line((44, 45, 51, 44), fill="#3f342c", width=2)
    d.line((47, 60, 53, 58), fill="#42322a", width=2)
    d.polygon([(17, 36), (19, 21), (28, 13), (49, 15), (56, 29), (67, 35), (64, 42), (16, 41),
               (8, 36), (10, 28)], fill="#716b49", outline=INK, width=2)
    d.line((19, 29, 55, 33), fill="#a49974", width=4)
    return image


@lru_cache(maxsize=64)
def soldier(width, phase, officer=False, flip=False):
    image, d = _piece((156, 244))
    stride = sin(phase * tau / 12)
    for x, swing, color in ((86, -stride, "#4e4c35"), (67, stride, "#676145")):
        ankle = (x + swing * 24, 210 + swing * 3)
        d.line(((x, 153), (x - 3 + swing * 13, 180), ankle), fill=color, width=18, joint="curve")
        d.line((ankle, (ankle[0] + 16, ankle[1] + 1)), fill="#393127", width=12)
    _pivot(image, _soldier_head(officer), (74, 82), (36, 67), 6 * cos(phase * tau / 12))
    d.polygon([(50, 87), (69, 76), (89, 79), (101, 93), (107, 151), (88, 161), (53, 155)],
              fill="#7a7954" if officer else "#8a8159", outline=INK, width=2)
    arm = (108 + stride * 13, 139 - abs(stride) * 13)
    d.line(((89, 92), (95 + stride * 9, 119), arm), fill="#56573d", width=18, joint="curve")
    d.ellipse((arm[0] - 5, arm[1] - 5, arm[0] + 7, arm[1] + 7), fill="#a47c57")
    d.line((52, 132, 101, 135), fill="#584736", width=7)
    d.rectangle((75, 128, 84, 136), outline="#c4a772", width=2)
    d.line((70, 84, 76, 123), fill="#b1a276", width=2)
    for i in range(5):
        d.line((57, 98 + i * 6, 64, 101 + i * 6), fill="#605940")
    if flip:
        image = ImageOps.mirror(image)
    return _finish(image, width, seed=31)


def _finish(image, width, seed, *, crop=True):
    width = max(8, round(width))
    source = image.crop(image.getbbox()) if crop else image
    scaled = source.resize((width, max(1, round(width * source.height / source.width))), Image.Resampling.LANCZOS)
    return cutout(scaled, border=3, seed=seed)


@lru_cache(maxsize=16)
def discharge(width, phase):
    """A small illustrated puff with a dry-printed onomatopoeia."""
    image, draw = _piece((170, 112))
    if phase < 2:
        draw.polygon(((12, 54), (47, 40), (28, 18), (63, 28), (86, 7), (80, 36),
                      (125, 31), (103, 56), (128, 71), (77, 67), (73, 100), (52, 73), (28, 84)),
                     fill="#e8b66a", outline="#755a43", width=3)
    else:
        tone = ("#bab4a0", "#c9c0a7", "#d2c8ae")[min(2, phase - 2)]
        for x, y, radius in ((42, 54, 22), (68, 35, 28), (92, 53, 29), (71, 69, 23), (115, 36, 16)):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=tone, outline="#8c816b", width=2)
    if phase <= 4:
        draw.text((70, 53), "PÁ!", fill=INK, font=ImageFont.truetype(str(DISPLAY), 37), anchor="mm")
    return _finish(image, width, seed=81)


@lru_cache(maxsize=40)
def prop(kind, width):
    image, d = _piece((360, 205))
    if kind == "fence":
        for y in (72, 118):
            d.polygon([(14, y), (337, y - 16), (343, y - 1), (16, y + 12)], fill="#996740", outline="#604331")
        for x in (46, 143, 236, 326):
            d.polygon([(x, 162), (x - 3, 49), (x + 9, 40), (x + 16, 52), (x + 16, 157)],
                      fill="#885738", outline="#523c2d")
            for y in (66, 111):
                d.ellipse((x + 4, y, x + 9, y + 5), fill="#3b3127")
    elif kind == "truck":
        d.polygon([(24, 117), (29, 90), (79, 88), (87, 57), (141, 54), (174, 78), (317, 79),
                   (346, 120), (344, 142), (26, 142)], fill="#63654a", outline=INK, width=4)
        d.polygon([(88, 96), (98, 68), (133, 67), (150, 91)], fill="#afaca0", outline=INK, width=2)
        d.rectangle((174, 79, 336, 114), fill="#817b5b", outline=INK, width=2)
        for x in range(182, 331, 20):
            d.line((x, 82, x + 1, 111), fill="#514e3b", width=2)
        d.rectangle((33, 102, 57, 106), fill="#cbba8d")
        for x in (86, 279):
            d.ellipse((x - 25, 121, x + 25, 168), fill="#403a32", outline=INK, width=3)
            d.ellipse((x - 10, 132, x + 9, 153), fill="#8a8068", outline=INK)
    elif kind == "box":
        d.polygon([(87, 68), (135, 49), (255, 54), (271, 77), (254, 147), (91, 145)],
                  fill="#a47e4d", outline="#60452e", width=3)
        d.polygon([(87, 68), (135, 49), (255, 54), (271, 77), (140, 76)], fill="#c9a96e", outline="#705b3a")
        d.polygon([(140, 76), (271, 77), (254, 147), (141, 146)], fill="#7b5a3a", outline="#60452e")
        for x in (99, 115, 155, 233):
            d.line((x, 78, x - 2, 141), fill="#6f5238", width=3)
        d.rectangle((166, 96, 225, 130), fill="#e7d5ab")
        d.text((196, 110), "LEWIS", fill=INK, font=ImageFont.truetype(str(TYPEWRITER), 17), anchor="mm")
    elif kind == "tripod":
        # A small, non-operating silhouette for the historical tableau.
        d.line(((172, 91), (125, 165)), fill="#464438", width=10)
        d.line(((172, 91), (220, 166)), fill="#464438", width=10)
        d.line(((172, 91), (151, 160)), fill="#797560", width=7)
        d.polygon([(91, 79), (145, 72), (151, 66), (234, 69), (241, 74), (303, 74), (312, 83),
                   (241, 86), (218, 94), (146, 93), (91, 92)], fill="#514d40", outline=INK, width=3)
        d.ellipse((158, 54, 215, 77), fill="#595647", outline=INK, width=2)
    elif kind == "wheat":
        rng = Random(42)
        for _ in range(32):
            x = rng.randrange(15, 345)
            top = rng.randrange(27, 101)
            lean = rng.randrange(-20, 21)
            color = rng.choice(("#9f692d", "#b97e36", "#d5a054", "#e1b66b"))
            d.line((x, 203, x + lean, top + 13), fill=color, width=3)
            for i in range(4):
                y = top + i * 8
                d.polygon([(x + lean, y + 11), (x + lean - 9, y - 6 + i), (x + lean - 2, y - 1)], fill=color)
                d.polygon([(x + lean, y + 11), (x + lean + 9, y - 6 + i), (x + lean + 2, y - 1)], fill=color)
            d.line((x, 155, x + lean - 18, 115), fill=color, width=3)
    else:
        raise ValueError(f"Unknown paper prop {kind!r}")
    return _finish(image, width, seed=23)


@lru_cache(maxsize=12)
def australia(width):
    height = round(width * .73)
    art = paper_card((width, height), "#d0ccac", 29)
    draw = ImageDraw.Draw(art)
    for x in range(8, width - 8, max(5, width // 12)):
        draw.line((x, 12, x, height - 12), fill="#b5b598")
    for y in range(13, height - 8, max(5, width // 12)):
        draw.line((10, y, width - 10, y), fill="#b5b598")
    coast = json.loads((ASSETS / "australia.json").read_text())

    def plot(x, y):
        return (.085 + .82 * (x - 112) / 42) * width, (.14 + .72 * (-y - 10) / 35) * height

    polygons = [[plot(x, y) for x, y in ring] for ring in coast]
    outline = Image.new("L", art.size)
    for ring in polygons:
        ImageDraw.Draw(outline).polygon(ring, fill=255)
    land = Image.new("RGBA", art.size, "#7c8b6e")
    ink = ImageDraw.Draw(land)
    divide = round(plot(129, -21)[0])
    ink.rectangle((0, 0, divide, height), fill="#b0784b")
    for i in range(-height, width + 10, max(5, width // 55)):
        ink.line((i, 0, i + height, height), fill="#35372b42")
    land.putalpha(outline)
    art.alpha_composite(land)
    draw = ImageDraw.Draw(art)
    for ring in polygons:
        draw.line(ring, fill="#62583d", width=max(1, width // 195), joint="curve")
    pin_x, pin_y = plot(118, -31)
    radius = max(2, width // 70)
    draw.ellipse((pin_x - radius, pin_y - radius, pin_x + radius, pin_y + radius), fill=RED, outline="#f3dfbd")
    font = ImageFont.truetype(str(TYPEWRITER), max(7, width // 22))
    draw.text((width * .08, height * .105), "AUSTRÁLIA", anchor="lm", font=font, fill=INK)
    draw.text((width * .90, height * .88), "N", anchor="mm", font=font, fill=INK)
    draw.polygon([(width * .90, height * .68), (width * .878, height * .79), (width * .92, height * .79)], fill=INK)
    return cutout(art, border=3, seed=29)


@lru_cache(maxsize=20)
def terrain(width, height, tone, seed):
    image, d = _piece((width, height))
    rng = Random(seed)
    ridge = [(x, height * rng.uniform(.11, .48)) for x in range(0, width + max(1, width // 10), max(1, width // 10))]
    d.polygon([*ridge, (width, height), (0, height)], fill=tone)
    for _ in range(max(25, width // 4)):
        x, y = rng.uniform(0, width), rng.uniform(height * .49, height)
        d.line((x, y, x + rng.uniform(3, 15), y - rng.uniform(0, 5)), fill="#655338")
    return cutout(image, border=3, seed=seed)


@lru_cache(maxsize=24)
def supplied(path: Path, width: int, modified: int):
    if path.suffix.lower() == ".svg":
        from cairosvg import svg2png

        stream = BytesIO(svg2png(bytestring=path.read_bytes(), output_width=width, unsafe=False))
    else:
        stream = path
    with Image.open(stream) as source:
        art = source.convert("RGBA")
    if art.getbbox() is None:
        raise ValueError(f"Empty cutout artwork: {path.name}")
    return _finish(art, width, seed=47)

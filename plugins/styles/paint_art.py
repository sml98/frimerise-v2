"""Original Comedy Paint artwork used by the asset bootstrap."""

from random import Random

from PIL import Image, ImageDraw

INK = "#29272e"
SKIN = "#f6b77d"
CREAM = "#ffefbf"
RED = "#d95b48"
JADE = "#3b9684"
TILE = (240, 300)

POSES = {
    "neutral": (((87, 162), (64, 195), (65, 223)), ((153, 161), (174, 193), (173, 219))),
    "pointing": (((86, 163), (64, 193), (69, 219)), ((154, 161), (177, 157), (212, 142))),
    "hands_on_head": (((87, 161), (46, 138), (60, 74)), ((153, 160), (193, 134), (178, 73))),
    "shrug": (((86, 164), (56, 184), (35, 158)), ((154, 163), (185, 181), (207, 151))),
    "cheering": (((85, 162), (52, 128), (40, 95)), ((153, 160), (184, 126), (198, 92))),
}
EXPRESSIONS = ("neutral", "happy", "shocked", "skeptical", "angry", "worried", "smug", "sad")
MOUTHS = ("closed", "half", "open")
ACCESSORIES = ("beard_robe", "worker_cap", "sailor", "crown", "traveler", "spectacles")
COLORS = {"clay": RED, "jade": JADE, "ochre": "#e9a33a", "indigo": "#697bb0"}
PROPS = ("ark", "animals", "planks", "cactus", "cloud", "mountain", "rainbow", "saw")


def _polygon(draw, points, fill, width=5):
    draw.polygon(points, fill=fill)
    draw.line([*points, points[0]], fill=INK, width=width, joint="curve")


def _stroke(draw, points, fill, width):
    draw.line(points, fill=INK, width=width + 7, joint="curve")
    draw.line(points, fill=fill, width=width, joint="curve")
    for x, y in (points[0], points[-1]):
        radius = width // 2
        draw.ellipse((x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3), fill=INK)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def body(pose, color, wrist_shift=0):
    image = Image.new("RGBA", TILE)
    draw = ImageDraw.Draw(image)
    _stroke(draw, ((92, 218), (85, 263)), "#754742", 15)
    _stroke(draw, ((145, 217), (151, 262)), "#754742", 15)
    _stroke(draw, ((84, 266), (64, 267)), "#523c3a", 15)
    _stroke(draw, ((150, 265), (169, 267)), "#523c3a", 15)
    left, (shoulder, elbow, hand) = POSES[pose]
    right = (shoulder, (elbow[0] + wrist_shift // 2, elbow[1]), (hand[0] + wrist_shift, hand[1]))
    for arm in (left, right):
        _stroke(draw, arm, COLORS[color], 17)
        hand_x, hand_y = arm[-1]
        draw.ellipse((hand_x - 10, hand_y - 10, hand_x + 10, hand_y + 10), fill=SKIN, outline=INK, width=4)
    coat = [(84, 152), (114, 146), (153, 153), (156, 185), (175, 228), (142, 237), (69, 233), (79, 195)]
    _polygon(draw, coat, COLORS[color])
    draw.line(((80, 210), (116, 207), (165, 207)), fill=INK, width=6, joint="curve")
    draw.rounded_rectangle((112, 200, 128, 215), radius=3, fill="#f6cd67", outline=INK, width=3)
    draw.line(((151, 220), (156, 228), (170, 225)), fill="#5b3539", width=3)
    draw.ellipse((54, 86, 83, 114), fill=SKIN, outline=INK, width=4)
    draw.ellipse((156, 85, 184, 112), fill=SKIN, outline=INK, width=4)
    head = [(69, 79), (76, 63), (96, 53), (134, 51), (161, 63), (173, 84), (168, 112),
            (153, 137), (127, 151), (101, 146), (80, 130), (68, 105)]
    _polygon(draw, head, SKIN)
    draw.line(((75, 79), (89, 65), (107, 61)), fill="#ffdc9d", width=4, joint="curve")
    draw.arc((108, 98, 130, 116), 20, 139, fill="#875243", width=3)
    draw.line(((82, 110), (88, 113), (94, 112)), fill="#d47e65", width=3)
    draw.line(((144, 111), (150, 113), (157, 109)), fill="#d47e65", width=3)
    return image


def expression(name):
    image = Image.new("RGBA", TILE)
    draw = ImageDraw.Draw(image)
    brows = {
        "neutral": ((92, 82, 104, 79), (132, 79, 144, 82)),
        "happy": ((90, 80, 104, 76), (132, 76, 148, 79)),
        "shocked": ((87, 74, 105, 71), (128, 72, 149, 70)),
        "skeptical": ((89, 76, 105, 70), (132, 85, 148, 84)),
        "angry": ((87, 79, 107, 87), (130, 88, 150, 80)),
        "worried": ((88, 85, 106, 75), (129, 76, 149, 86)),
        "smug": ((90, 80, 107, 78), (132, 79, 149, 73)),
        "sad": ((90, 89, 105, 82), (133, 82, 149, 89)),
    }
    for brow in brows[name]:
        draw.line(brow, fill=INK, width=5)
    for x in (99, 138):
        if name in {"happy", "smug", "sad"}:
            if name == "happy":
                draw.arc((x - 8, 89, x + 8, 106), 182, 357, fill=INK, width=4)
            else:
                draw.line(((x - 8, 93), (x + 7, 94)), fill=INK, width=4)
                draw.ellipse((x - 1, 94, x + 5, 101), fill=INK)
        else:
            height = 14 if name in {"shocked", "worried"} else 10
            draw.ellipse((x - 8, 96 - height, x + 8, 100 + height // 3), fill="#fff8e7", outline=INK, width=3)
            draw.ellipse((x - 1, 93, x + 5, 101), fill=INK)
    return image


def mouth(name):
    image = Image.new("RGBA", TILE)
    draw = ImageDraw.Draw(image)
    if name == "closed":
        draw.arc((103, 114, 136, 133), 13, 162, fill=INK, width=4)
    elif name == "half":
        draw.rounded_rectangle((106, 118, 134, 130), radius=5, fill=INK)
        draw.line((111, 121, 129, 121), fill="#fff0d5", width=3)
    else:
        draw.ellipse((105, 113, 135, 140), fill=INK)
        draw.ellipse((111, 128, 130, 136), fill="#e5847c")
        draw.line((111, 119, 128, 119), fill="#fff0d5", width=3)
    return image


def accessory(name):
    image = Image.new("RGBA", TILE)
    draw = ImageDraw.Draw(image)
    if name in {"beard_robe", "sailor"}:
        beard = [(69, 105), (80, 114), (93, 122), (103, 111), (119, 113), (135, 111), (145, 123),
                 (159, 112), (169, 104), (165, 132), (153, 150), (139, 165), (126, 162), (114, 168),
                 (96, 160), (82, 147), (73, 133)]
        _polygon(draw, beard, "#e9dec6")
        for x, y in ((89, 136), (106, 151), (133, 149), (150, 134)):
            draw.line((x, y, x + 5, y + 8), fill="#a79a9d", width=3)
        headwrap = [(66, 81), (68, 61), (88, 46), (124, 41), (157, 48), (173, 62), (174, 76),
                    (145, 66), (119, 65), (92, 69)]
        _polygon(draw, headwrap, "#fff4d5" if name == "beard_robe" else "#798fb9")
        draw.line(((76, 69), (100, 58), (136, 55), (166, 66)), fill="#a89a84", width=3)
    elif name == "worker_cap":
        _polygon(draw, [(63, 77), (78, 54), (105, 47), (134, 49), (158, 63), (176, 74), (190, 78),
                        (166, 82), (135, 72), (99, 70)], "#edac56")
    elif name == "crown":
        _polygon(draw, [(71, 65), (68, 37), (96, 52), (118, 24), (140, 50), (169, 35), (163, 67),
                        (134, 61), (100, 61)], "#f5c64f")
    elif name == "traveler":
        _polygon(draw, [(63, 72), (79, 54), (88, 31), (152, 32), (156, 55), (182, 67), (171, 74),
                        (105, 68)], "#b88a64")
        draw.line(((84, 55), (113, 55), (156, 58)), fill=INK, width=3)
    else:
        for x in (97, 139):
            draw.ellipse((x - 15, 80, x + 15, 108), outline=INK, width=4)
        draw.line((112, 92, 124, 92), fill=INK, width=4)
    return image


def _ark(draw, *, hull_only=False):
    hull = [(29, 144), (65, 150), (147, 143), (240, 144), (346, 139), (328, 176), (294, 207),
            (248, 226), (107, 227), (67, 210), (44, 182)]
    _polygon(draw, hull, "#bd7249", width=8)
    draw.line(((47, 169), (135, 176), (237, 172), (325, 164)), fill="#6c403b", width=6)
    draw.line(((67, 193), (154, 201), (237, 198), (314, 182)), fill="#e6a673", width=5)
    if hull_only:
        return
    _polygon(draw, [(94, 147), (101, 100), (277, 98), (289, 144)], "#e9aa63", width=7)
    _polygon(draw, [(77, 102), (128, 70), (242, 70), (301, 101), (247, 105), (128, 108)], "#a44d46", width=7)
    for x in (119, 172, 228):
        draw.rounded_rectangle((x, 112, x + 22, 135), radius=4, fill="#604653", outline=INK, width=4)
    draw.line(((261, 156), (277, 137), (285, 113)), fill="#ffe0a1", width=3)


def _animals(draw):
    for x, y, fill, long in ((100, 140, "#aa825f", True), (152, 157, "#e7be8c", True),
                             (228, 158, "#b0a9af", False), (279, 169, "#828596", False)):
        for leg in (-18, 11):
            _stroke(draw, ((x + leg, y + 23), (x + leg, y + 53)), fill, 9)
        draw.ellipse((x - 35, y - 6, x + 29, y + 34), fill=fill, outline=INK, width=5)
        high = y - 49 if long else y - 22
        _stroke(draw, ((x + 15, y + 8), (x + 25, high + 6)), fill, 15)
        draw.ellipse((x + 11, high - 12, x + 52, high + 16), fill=fill, outline=INK, width=4)
        _polygon(draw, [(x + 18, high - 10), (x + 14, high - 28), (x + 28, high - 14)], fill, width=3)
        draw.ellipse((x + 36, high - 3, x + 42, high + 3), fill=INK)


def prop(name):
    image = Image.new("RGBA", (376, 256))
    draw = ImageDraw.Draw(image)
    if name == "ark":
        _ark(draw)
    elif name == "animals":
        _animals(draw)
    elif name == "planks":
        for i, y in enumerate((199, 165, 131, 96)):
            x = 36 + i * 16
            _polygon(draw, [(x, y), (x + 239, y - 15), (x + 274, y + 13), (x + 19, y + 30)],
                     ("#db9965", "#e7ac65")[i % 2], width=6)
            draw.line(((x + 38, y + 11), (x + 156, y + 4)), fill="#905f4b", width=4)
    elif name == "cactus":
        _stroke(draw, ((191, 225), (191, 57)), "#70a47d", 32)
        _stroke(draw, ((190, 150), (147, 143), (142, 105)), "#70a47d", 21)
        _stroke(draw, ((194, 169), (233, 151), (239, 122)), "#70a47d", 21)
        draw.line(((186, 74), (187, 190)), fill="#a4bf86", width=4)
    elif name == "cloud":
        _polygon(draw, [(33, 143), (54, 116), (88, 113), (115, 76), (155, 69), (184, 86), (211, 60),
                        (252, 64), (277, 99), (312, 106), (347, 138), (330, 161), (286, 173), (83, 172)],
                 "#c1c5d4", width=6)
    elif name == "mountain":
        _polygon(draw, [(16, 220), (109, 125), (148, 150), (235, 41), (360, 220)], "#8b9ca0", width=6)
        _polygon(draw, [(174, 120), (235, 41), (290, 119), (263, 109), (240, 122), (225, 102), (199, 127)],
                 "#fff0d3", width=5)
    elif name == "rainbow":
        for offset, color in enumerate((INK, "#d76757", "#e8a95c", "#efd286", "#72a87d", "#778fbb", INK)):
            inset = 7 + offset * 12
            draw.arc((inset, inset - 1, 375 - inset, 335 - inset), 180, 360, fill=color, width=14)
    else:
        teeth = [(x, 157 + (idx % 2) * 18) for idx, x in enumerate(range(297, 69, -13))]
        _polygon(draw, [(69, 156), (80, 107), (259, 76), (297, 90), (305, 154), *teeth], "#b3bdc3", width=5)
        _polygon(draw, [(263, 81), (307, 56), (344, 91), (328, 152), (295, 155), (308, 116), (281, 100)],
                 RED, width=6)
    return image


def ark_hull():
    image = Image.new("RGBA", (376, 256))
    _ark(ImageDraw.Draw(image), hull_only=True)
    return image


def paper_texture():
    image = Image.new("RGBA", (384, 384))
    draw = ImageDraw.Draw(image)
    random = Random(12)
    for _ in range(6200):
        x, y = random.randrange(image.width), random.randrange(image.height)
        fill = random.choice(((113, 78, 53, 18), (255, 248, 224, 75), (105, 90, 74, 12)))
        draw.line((x, y, x + random.randrange(3), y), fill=fill, width=1)
    return image

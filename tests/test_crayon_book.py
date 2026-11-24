from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.registry import discover, get_plugin
from core.schema import EpisodeSpec
from plugins.styles.crayon_book import BookOptions, _actor_sprite, _camera_image, _layers, _portrait_options
from plugins.styles.crayon_draw_on import DrawOn, Stroke
from plugins.styles.crayon_materials import WHITE_WAX, Sheet, wash_pigment
from plugins.styles.crayon_motion import flight_position, gait, walk_position
from plugins.styles.crayon_pages import captions, mobile_safe_box, page_turn, title
from plugins.styles.paint_motion import drawing_step


def test_wax_resist_leaves_white_strokes_under_the_wash():
    size = (144, 96)
    sheet = Sheet(size)
    wax = Image.new("L", size)
    ImageDraw.Draw(wax).rectangle((34, 25, 71, 70), fill=255)
    sheet.paint(wax, WHITE_WAX, strength=.98, hatch=0)
    wash = wash_pigment(Image.new("L", size, 190), (.15, .24, .5))
    dry = np.asarray(sheet.image(), dtype=float)
    wet = np.asarray(sheet.image(wash), dtype=float)
    assert abs(wet[31:62, 40:64].mean() - dry[31:62, 40:64].mean()) < 22
    assert dry[31:62, 88:112].mean() - wet[31:62, 88:112].mean() > 75
    assert np.std(dry[31:62, 88:112]) > 3


def test_caption_writes_ink_over_an_irregular_paper_mask():
    size = (320, 180)
    caption, = captions("A arca de Noé.", [], size, 2)
    first = Image.new("RGB", size, "#b18562")
    last = first.copy()
    caption.draw(first, 0, 0)
    caption.draw(last, 1, 0)
    assert caption.paper.getpixel((0, 0)) == 0
    assert caption.paper.getpixel((caption.paper.width // 2, caption.paper.height // 2)) == 255
    assert np.count_nonzero(np.asarray(first) != np.asarray(last)) > 50


def test_page_turn_reveals_next_illustration_before_completion():
    size = (320, 180)
    current = Image.new("RGB", size, "#e6bc65")
    following = Image.new("RGB", size, "#819acf")
    assert page_turn(current, following, 0) is current
    assert page_turn(current, following, 1) is following
    turning = page_turn(current, following, .55)
    assert turning.getpixel((280, 45)) == following.getpixel((280, 45))
    assert turning.getpixel((40, 45)) != following.getpixel((40, 45))


def test_camera_moves_between_two_frames_of_the_same_drawing():
    assert [drawing_step(index, 24) for index in range(6)] == [0, 0, 1, 1, 2, 2]
    image = Image.linear_gradient("L").resize((320, 180)).convert("RGB")
    options = BookOptions(camera="float")
    first = _camera_image(image, options, 0, 4, None)
    second = _camera_image(image, options, 1 / 24, 4, None)
    assert first.tobytes() != second.tobytes()


def test_ark_scales_to_visible_actor_height_and_reuses_its_hull_alignment():
    size = (960, 540)
    options = BookOptions.model_validate({
        "characters": [{"name": "noe", "x": .49, "y": .72, "scale": .61, "accessory": "sailor"}],
        "props": [{"kind": "ark", "layer": "ark_hull", "motion": "bob"}],
    })
    actor = options.characters[0]
    _, top, _, bottom = _actor_sprite(actor, size, actor.pose, actor.expression, "closed").getbbox()
    layers = _layers(options, size, [])
    assert abs(layers.back[0].image.height - 2.2 * (bottom - top)) <= 1
    assert layers.front[0].position == layers.back[0].position
    assert layers.front[0].image.size == layers.back[0].image.size


def test_draw_on_follows_distance_and_finishes_without_fading_the_page():
    stroke = Stroke.from_points([(0, 0), (12, 0), (12, 20)])
    assert stroke.prefix(17) == ((0, 0), (12, 0), (12, 5))
    size = (160, 100)
    art = Image.new("RGBA", (85, 60), "#2d3263")
    picture = Image.new("RGB", size, "#d69a6a")
    draw_on = DrawOn(size, [(art, (35, 22))], ())
    paper = draw_on.frame(picture, 0)
    early = draw_on.frame(picture, .15)
    assert early.getpixel((70, 52)) == paper.getpixel((70, 52))
    assert early.tobytes() != paper.tobytes()
    assert draw_on.frame(picture, .90) is picture


def test_animals_change_footfall_and_dove_reaches_its_perch():
    start, end = (.2, .82), (.6, .72)
    assert walk_position(start, end, 0, 2) == start
    assert walk_position(start, end, 2, 2) == end
    assert gait(.25, 2, 0) != gait(.5, 2, 0)
    assert np.allclose(flight_position((.8, .32), (.4, .56), 2, 2), (.4, .56))
    assert flight_position((.8, .32), (.4, .56), 1, 2)[1] < .4


@pytest.mark.parametrize("size", [(540, 960), (1080, 1920)])
def test_portrait_title_and_caption_fit_platform_safe_box(size):
    line = "Quando a chuva passou, uma pomba voltou com um ramo no bico."
    words = [(word, index / 4) for index, word in enumerate(line.split())]
    safe_left, safe_top, safe_right, safe_bottom = mobile_safe_box(size)
    for panel in (title("A arca\nde Noé", size), *captions(line, words, size, 4.5)):
        x, y = panel.position
        assert safe_left <= x < x + panel.paper.width <= safe_right
        assert safe_top <= y < y + panel.paper.height <= safe_bottom


def test_portrait_reflows_demo_arks_and_keeps_the_hull_rig_aligned():
    path = Path(__file__).resolve().parents[1] / "episodes" / "demo_crayon.json"
    spec = EpisodeSpec.from_json(path.read_bytes())
    size = (540, 960)
    safe_left, _, safe_right, _ = mobile_safe_box(size)
    for scene in spec.scenes:
        authored = BookOptions.model_validate(scene.options)
        portrait = _portrait_options(authored, size)
        layers = _layers(portrait, size, [])
        for item in layers.back:
            if item.prop.kind == "ark":
                assert safe_left <= item.position[0]
                assert item.position[0] + item.image.width <= safe_right
        if layers.front:
            ark, = [item for item in layers.back if item.prop.kind == "ark"]
            hull, = layers.front
            assert ark.position == hull.position
            assert ark.image.size == hull.image.size


def test_only_crayon_book_is_discovered_as_a_style():
    discover()
    assert get_plugin("style", "crayon_book").__module__ == "plugins.styles.crayon_book"
    assert get_plugin("style", "comedy_paint", optional=True) is None

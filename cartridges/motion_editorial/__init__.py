"""Register the Motion Editorial cartridge."""

from core.registry import register_style

from .renderer import render_scene

register_style("motion_editorial")(render_scene)

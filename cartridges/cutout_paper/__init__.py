"""Editorial paper-cut animation."""

from core.registry import register_style

from .renderer import render_scene

register_style("cutout_paper")(render_scene)

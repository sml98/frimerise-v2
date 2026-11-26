"""Register the Motion Editorial cartridge."""

from core.director import DirectionProfile, DirectorError, has_text
from core.registry import register_direction, register_style

from .renderer import Options, render_scene

register_style("motion_editorial")(render_scene)


def _directed_visual(options, scene, generated):
    if generated and not options.module:
        raise DirectorError("informe module (sem visual_type legado)")
    if options.bg_mode == "broll_video" and not (options.broll_query or options.media_file):
        raise DirectorError("broll_video exige broll_query ou media_file")
    if (options.bg_mode == "photo_duotone" or options.module in {"photo_spotlight", "photo_split_kpi"}) and not (
        options.media_query or options.media_file
    ):
        raise DirectorError("o módulo fotográfico exige media_query ou media_file")
    if generated:
        data = options.data
        if options.module in {"kpi_hero", "gauge_meter", "photo_split_kpi"} and (
            "value" not in data.model_fields_set or not has_text(data.context)
        ):
            raise DirectorError("o indicador exige data.value e data.context")
        if options.module == "quote_callout" and not has_text(data.quote):
            raise DirectorError("quote_callout exige data.quote")
        if options.module == "news_headline" and (
            not all(has_text(value) for value in (data.source, data.headline, data.excerpt, data.highlight))
            or data.highlight not in data.excerpt
        ):
            raise DirectorError("news_headline exige data.source, headline, excerpt e highlight literal do excerpt")
    return options.module or options.visual_type


register_direction("motion_editorial")(DirectionProfile(
    "cartridges/motion_editorial/SKILL.md", ("| Módulo |", "## Composição web"),
    (
        "Use kicker e headline curtos, module explícito, accent (amber/emerald/cyan/crimson/violet) por ato. "
        "bg_mode aceita grid_glow, radial_dramatic, photo_duotone ou broll_video. "
        "Para fotos inclua media_query em inglês, para broll_video inclua broll_query e broll_description. "
        "Números e gráficos exigem dados sustentados. News_headline deve ter excerpt com highlight literal. "
        "Ao descrever acervos históricos, prefira Wikimedia para fotos e Prelinger para filmes; deixe Pixabay "
        "para imagens contextuais contemporâneas."
    ),
    Options, _directed_visual,
))

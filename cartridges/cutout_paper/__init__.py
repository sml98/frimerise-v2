"""Editorial paper-cut animation."""

from pathlib import Path

from core.director import DirectionProfile, DirectorError, has_text
from core.registry import register_direction, register_style

from .renderer import Options, render_scene

register_style("cutout_paper")(render_scene)


def _directed_visual(options, scene, generated):
    if generated and options.cutouts:
        raise DirectorError("cutouts exige um pacote de arte fornecido pelo usuário; escolha um módulo fotográfico")
    if options.module:
        return options.module
    if options.media_file and Path(options.media_file).suffix.lower() == ".mp4":
        return "local_film"
    if options.layout == "custom" and has_text(options.media_query):
        return "custom"
    if options.media_file:
        return "photo_cutout"
    if "layout" not in scene or (options.layout == "custom" and not options.cutouts):
        raise DirectorError("informe um módulo fotográfico ou um layout ilustrado com arte")
    if generated and options.layout != "custom":
        raise DirectorError("os layouts ilustrados específicos da Guerra dos Emus não servem para novos temas")
    return options.layout


register_direction("cutout_paper")(DirectionProfile(
    "cartridges/cutout_paper/SKILL.md", ("## Colagem de revista", "## Erros Proibidos"),
    (
        "Use headline (até 42 caracteres), label curto, paper kraft/newsprint por ato, stamp curto quando útil. "
        "Alterne photo_cutout, newspaper_clipping, collage_board e layout custom conforme o conteúdo. "
        "Para montar uma gravura ou mapa em papel, use layout custom (sem module) com media_query. "
        "Layouts map/field/expedition/routes/portrait são exclusivos da Guerra dos Emus. "
        "Para imagem histórica use media_source wikimedia e assunto pesquisável em inglês, "
        "sem adivinhar nomes de arquivo. Reutilize consultas quando o documento retratado for o mesmo. "
        "Consultas de retratos, gravuras de figuras, mapas e objetos também podem contextualizar o período; "
        "identifique contexto ou reconstituição sem atribuir uma imagem genérica ao fato. "
        "No jornal, inclua publication e clipping_headline. Em cada foto do board inclua label."
    ),
    Options, _directed_visual,
))

"""Resolve the named files declared in a scene's media object."""

from core.registry import register_media


@register_media("local_pack")
def resolve_media(scene, timing, assets_root):
    declared = scene.options.get("media", {})
    if not isinstance(declared, dict):
        raise ValueError(f"Scene {scene.id!r}: media must be an object of asset names and paths")
    assets = {}
    for name, location in declared.items():
        path = (assets_root / location).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Scene {scene.id!r}, asset {name!r}: {path}")
        assets[name] = path
    return assets

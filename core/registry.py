"""Discover plugin modules by importing packages under plugins/."""

from importlib import import_module
from pkgutil import walk_packages

import plugins

_plugins = {}


class TTSUnavailable(RuntimeError):
    """A synthesis backend failed and the episode may use its fallback."""


def _register(kind):
    def named(name):
        def decorator(plugin):
            key = (kind, name)
            if key in _plugins:
                raise ValueError(f"Duplicate {kind} plugin: {name}")
            _plugins[key] = plugin
            return plugin

        return decorator

    return named


register_style = _register("style")
register_tts = _register("tts")
register_media = _register("media")
register_audio = _register("audio")
register_qc = _register("qc")
register_transcriber = _register("transcriber")
register_exports = _register("exports")


def discover():
    for module in walk_packages(plugins.__path__, plugins.__name__ + "."):
        import_module(module.name)


def get_plugin(kind, name, *, optional=False):
    try:
        return _plugins[kind, name]
    except KeyError:
        if optional:
            return None
        available = ", ".join(sorted(label for category, label in _plugins if category == kind))
        raise ValueError(f"Unknown {kind} plugin {name!r}. Available: {available or 'none'}") from None

"""Motion primitives ported from Lemo-Opuscar and the HyperFrames registry."""

from math import cos, exp, floor, log, sin, tau

DRAWING_FPS = 12
PUNCH_ZOOM = 1.22
POP_SECONDS = .15
SAW_PERIOD = .50


def _clamp(t):
    return max(0.0, min(1.0, t))


def _lerp(a, b, progress):
    return a + (b - a) * progress


# Lemo-Opuscar core/lib.js, eio/eo/back, at 8ca0dd8 (MIT).
def ease_in_out(t):
    t = _clamp(t)
    return 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def _ease_out(t):
    return 1 - (1 - _clamp(t)) ** 3


def pop(elapsed):
    t = _clamp(elapsed / POP_SECONDS) - 1
    overshoot = 2.6
    return 1 + t * t * ((overshoot + 1) * t + overshoot)


# Lemo-Opuscar stick-explainer/demo/full.js, lerpZ.
def _zoom_between(a, b, progress):
    return exp(_lerp(log(a), log(b), progress))


def camera(name, at, duration, punch, focus):
    progress = at / duration
    zoom, x, y = 1.0, .5, .5
    if name == "zoom_in_fast":
        zoom = _zoom_between(1, 1.095, ease_in_out(at / .8))
        x, y = focus
    elif name == "zoom_out":
        zoom = _zoom_between(1.12, 1.005, ease_in_out(progress))
    elif name in {"pan_left", "pan_right"}:
        zoom = 1.085
        x = _lerp(.92, .08, ease_in_out(progress))
        if name == "pan_right":
            x = 1 - x
    elif name == "float":
        zoom = 1.045 + .013 * cos(tau * at / 2.7)
        x = .5 + .23 * sin(tau * at / 3.7)
        y = .5 + .20 * sin(tau * at / 2.9)
    if punch is not None and at >= punch:
        # Lemo dataviz/demo/film.js attack, with the HyperFrames yt-camera-move return.
        elapsed = at - punch
        strength = _ease_out(elapsed / .12) * (1 - ease_in_out((elapsed - .235) / .30))
        zoom = _zoom_between(zoom, PUNCH_ZOOM, strength)
        x, y = _lerp(x, focus[0], strength), _lerp(y, focus[1], strength)
    return zoom, x, y


def drawing_step(frame, fps):
    # crayon-book/demo/main.js advances the drawing seed at floor(t * 12).
    return frame * DRAWING_FPS // fps


def boil_offset(step, actor_index):
    # HyperFrames registry/components/hw-boil, hwHash and hwBoil at 34c7e64 (Apache-2.0).
    def hw_hash(n, seed):
        x = sin(n * 127.1 + seed * 311.7) * 43758.5453
        return (x - floor(x)) * 2 - 1

    n = step * 3 + actor_index * 97
    return .65 * hw_hash(n, 1), .65 * hw_hash(n + 1, 1)


def saw_wrist(at):
    # Same phase-driven arm swing used in stick-explainer/demo/index.html.
    return round(11 * sin(tau * max(0.0, at) / SAW_PERIOD))

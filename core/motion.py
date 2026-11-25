"""Shared timing for continuous camera and scene accents."""

from math import ceil


def ease_in_out_cubic(value):
    u = max(0.0, min(1.0, value))
    return 4 * u ** 3 if u < .5 else 1 - 4 * (1 - u) ** 3


def ease_out_back(value):
    u = max(0.0, min(1.0, value)) - 1
    return 1 + 2.05 * u ** 3 + 1.05 * u ** 2


def visual_cues(duration):
    """Place beats in wall-clock seconds, retaining at least half a second to play each one."""
    if duration <= 5:
        return ()
    return tuple(2.75 * i for i in range(1, ceil(duration / 2.75)) if 2.75 * i <= duration - .5)


def camera_accents(at, duration):
    zoom, pan = 0.0, 0.0
    for index, cue in enumerate(visual_cues(duration)):
        age = at - cue
        if -.25 <= age <= 1.05:
            pulse = ease_in_out_cubic((age + .25) / .53) * (1 - ease_in_out_cubic((age - .32) / .73))
            if index % 3 == 0:
                zoom += .14 * pulse
            else:
                pan += (.027 if index % 3 == 1 else -.044) * pulse
    return zoom, pan

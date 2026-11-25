"""Register the shared studio mixer for existing and new cartridges."""

from core.audio_master import _mix, master
from core.registry import register_audio

register_audio("master_dsp")(master)

"""Receive a JSON synthesis request over stdin from the active Colab runtime.

Install the optional MIT chatterbox-tts==0.1.7 package in a GPU-enabled
environment. Point FRIMERISE_CHATTERBOX_PYTHON to its Python executable when
its Transformers dependency differs from the main render environment.
"""

import json
import sys

from core.tts_plugins.chatterbox_ptbr import INVALID_REQUEST, WorkerError, render_wav


def main():
    try:
        render_wav(**json.load(sys.stdin))
    except WorkerError as error:
        print(error, file=sys.stderr)
        return error.status
    except (TypeError, ValueError):
        print("Solicitação Chatterbox PT-BR inválida.", file=sys.stderr)
        return INVALID_REQUEST
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

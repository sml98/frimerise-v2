#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }

[[ $# -ge 1 ]] || fail "Uso: $0 {episodes,examples}/<slug>.json [--aspect 9:16|16:9] [--audio arquivo | --continuous] [--workers N]"
episode=$1
shift
aspect=
audio=
continuous=
tts=
voice=
workers=
while (( $# )); do
    case $1 in
        --continuous) continuous=1; shift ;;
        --aspect|--audio|--tts|--voice|--workers)
            [[ $# -ge 2 && -n $2 && $2 != --* ]] || fail "Faltou um valor para $1."
            case $1 in
                --aspect) aspect=$2 ;;
                --audio) audio=$(realpath -e -- "$2") || fail "Arquivo de áudio inexistente: $2" ;;
                --tts) tts=$2 ;;
                --voice) voice=$2 ;;
                --workers) workers=$2 ;;
            esac
            shift 2
            ;;
        *) fail "Opção desconhecida: $1" ;;
    esac
done
[[ -z $audio || -z $continuous ]] || fail "Escolha --audio ou --continuous."
[[ -z $workers || $workers =~ ^[1-9][0-9]*$ ]] || fail "Informe um inteiro positivo em --workers."
[[ -z $audio || ( -f $audio && -s $audio ) ]] || fail "Informe um arquivo de áudio legível."
[[ -z $aspect || $aspect == 9:16 || $aspect == 16:9 ]] || fail "Formato inválido: $aspect"
[[ $episode =~ ^(episodes|examples)/[a-z0-9]+([_-][a-z0-9]+)*[.]json$ ]] ||
    fail "Informe um arquivo episodes/<slug>.json ou examples/<slug>.json."
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ -f $episode ]] || fail "Episódio inexistente: $episode"
for cli in colab git python3 flock ssh ssh-keygen tar; do
    command -v "$cli" >/dev/null || fail "Comando obrigatório ausente: $cli"
done

revision=$(git rev-parse --verify HEAD 2>/dev/null) || fail "Crie o commit do episódio antes de renderizar."
git cat-file -e "HEAD:$episode" 2>/dev/null && git diff --quiet HEAD -- "$episode" ||
    fail "Inclua a versão atual de $episode no commit."
git cat-file -e HEAD:scripts/colab_render.py 2>/dev/null ||
    fail "Inclua scripts/colab_render.py no commit."

db=${FRIMERISE_DB:-frimerise.db}
config=${RCLONE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/rclone/rclone.conf}
remote=${FRIMERISE_RCLONE_REMOTE:-gdrive}
gpu=${FRIMERISE_COLAB_GPU:-L4}
[[ -s $config ]] || fail "Configuração rclone inexistente: $config"
[[ $remote =~ ^[[:alnum:]_.-]+$ ]] || fail "Nome de remote rclone inválido."
[[ $gpu == L4 || $gpu == T4 ]] || fail "GPU inválida: $gpu"

stage=$(mktemp -d)
clean_stage() {
    for file in frimerise.db audio-input rclone.conf job.json repo.bundle runner.py colab-key colab-key.pub; do
        if [[ -f $stage/$file ]]; then unlink -- "$stage/$file"; fi
    done
    rmdir -- "$stage"
}
trap clean_stage EXIT
python3 - "$db" "$stage" "$episode" "$remote" "$revision" "$aspect" "$gpu" "$audio" "$continuous" "$tts" "$voice" "$workers" <<'PY'
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

source, stage, episode, remote, revision, aspect, gpu, audio, continuous, tts, voice, workers = sys.argv[1:]
stage = Path(stage)
spec = json.loads(Path(episode).read_text(encoding="utf-8"))
backends = (tts or spec.get("tts_plugin", "gemini_tts"), spec.get("tts_fallback_plugin"))
settings = []
if not audio and "gemini_tts" in backends:
    settings.append("GEMINI_API_KEY")
for scene in spec.get("scenes", ()):
    options = {**scene.get("options", {}), **scene}
    for prefix in ("media", "broll"):
        query = options.get(f"{prefix}_query", "")
        provider = options.get(f"{prefix}_source", "auto")
        if query and not query.lower().startswith(("file:", "archive:")):
            if provider in ("auto", "pixabay"):
                settings.append("PIXABAY_API_KEY")
            if prefix == "broll" and provider in ("auto", "pexels"):
                settings.append("PEXELS_API_KEY")
rows = []
for key in dict.fromkeys(settings):
    value = os.environ.get(key, "").strip() if key != "GEMINI_API_KEY" else ""
    try:
        if not value:
            with closing(sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)) as database:
                row = database.execute(
                    "SELECT value FROM settings WHERE upper(key) = ? AND trim(value) <> '' LIMIT 1", (key,),
                ).fetchone()
            value = row[0] if row else ""
    except sqlite3.Error:
        if key == "GEMINI_API_KEY":
            sys.exit("Não foi possível ler frimerise.db como uma base de configurações.")
    if value:
        rows.append((key, value))
    elif key == "GEMINI_API_KEY":
        sys.exit("frimerise.db precisa de GEMINI_API_KEY na tabela settings.")
if rows:
    with closing(sqlite3.connect(stage / "frimerise.db")) as snapshot, snapshot:
        snapshot.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        snapshot.executemany("INSERT INTO settings VALUES (?, ?)", rows)
(stage / "job.json").write_text(json.dumps({"episode": episode, "remote": remote, "revision": revision,
                                           "aspect": aspect, "gpu": gpu, "audio": bool(audio),
                                           "continuous": bool(continuous), "tts": tts, "voice": voice,
                                           "workers": int(workers) if workers else None}))
PY
narration_files=()
if [[ -n $audio ]]; then
    cp -- "$audio" "$stage/audio-input"
    chmod 600 "$stage/audio-input"
    narration_files=(audio-input)
fi
if [[ -f $stage/frimerise.db ]]; then
    narration_files+=(frimerise.db)
fi
git bundle create "$stage/repo.bundle" HEAD
git show HEAD:scripts/colab_render.py > "$stage/runner.py"
cp -- "$config" "$stage/rclone.conf"
chmod 600 "$stage/rclone.conf"
ssh-keygen -q -t ed25519 -N '' -f "$stage/colab-key"

(
    exec 9>"${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/frimerise-colab-${UID}.lock"
    flock -n 9 || fail "Já existe um render Colab em andamento."
    status=$(colab status -s frime)
    [[ $status == *"Session 'frime' not found."* ]] ||
        fail "A sessão frime já existe. Encerre-a antes de rodar este script."

    finish_runtime() {
        local code=$1
        trap - EXIT HUP INT TERM
        if ! colab stop -s frime && ! colab stop -s frime; then
            printf '%s\n' "Não foi possível confirmar o encerramento da sessão frime." >&2
            if (( code == 0 )); then code=1; fi
        fi
        exit "$code"
    }
    trap 'finish_runtime "$?"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    colab new -s frime --gpu "$gpu"
    remote_job='
        set -eu
        umask 077
        mkdir -m 700 /content/frimerise-job
        tar -xf - -C /content/frimerise-job
        exec python3 -u /content/frimerise-job/runner.py
    '
    tar -C "$stage" -cf - repo.bundle job.json "${narration_files[@]}" rclone.conf runner.py |
        colab ssh -s frime -i "$stage/colab-key" -- "$remote_job"
)

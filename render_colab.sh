#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }

[[ $# -ge 1 ]] || fail "Uso: $0 {episodes,examples}/<slug>.json [--aspect 9:16|16:9] [--audio arquivo | --continuous]"
episode=$1
shift
aspect=
audio=
continuous=
tts=
voice=
while (( $# )); do
    case $1 in
        --continuous) continuous=1; shift ;;
        --aspect|--audio|--tts|--voice)
            [[ $# -ge 2 && -n $2 && $2 != --* ]] || fail "Faltou um valor para $1."
            case $1 in
                --aspect) aspect=$2 ;;
                --audio) audio=$(realpath -e -- "$2") || fail "Arquivo de áudio inexistente: $2" ;;
                --tts) tts=$2 ;;
                --voice) voice=$2 ;;
            esac
            shift 2
            ;;
        *) fail "Opção desconhecida: $1" ;;
    esac
done
[[ -z $audio || -z $continuous ]] || fail "Escolha --audio ou --continuous."
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
[[ -n $audio || -s $db ]] || fail "Aponte FRIMERISE_DB para um frimerise.db preenchido."
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
python3 - "$db" "$stage" "$episode" "$remote" "$revision" "$aspect" "$gpu" "$audio" "$continuous" "$tts" "$voice" <<'PY'
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

source, stage, episode, remote, revision, aspect, gpu, audio, continuous, tts, voice = sys.argv[1:]
stage = Path(stage)
if not audio:
    try:
        with closing(sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)) as database:
            row = database.execute(
                "SELECT value FROM settings WHERE upper(key) = 'GEMINI_API_KEY' AND trim(value) <> '' LIMIT 1"
            ).fetchone()
        if not row:
            sys.exit("frimerise.db precisa de GEMINI_API_KEY na tabela settings.")
        with closing(sqlite3.connect(stage / "frimerise.db")) as snapshot, snapshot:
            snapshot.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            snapshot.execute("INSERT INTO settings VALUES (?, ?)", ("GEMINI_API_KEY", row[0]))
    except sqlite3.Error:
        sys.exit("Não foi possível ler frimerise.db como uma base de configurações.")
(stage / "job.json").write_text(json.dumps({"episode": episode, "remote": remote, "revision": revision,
                                           "aspect": aspect, "gpu": gpu, "audio": bool(audio),
                                           "continuous": bool(continuous), "tts": tts, "voice": voice}))
PY
if [[ -n $audio ]]; then
    cp -- "$audio" "$stage/audio-input"
    chmod 600 "$stage/audio-input"
    narration_file=audio-input
else
    narration_file=frimerise.db
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
    tar -C "$stage" -cf - repo.bundle job.json "$narration_file" rclone.conf runner.py |
        colab ssh -s frime -i "$stage/colab-key" -- "$remote_job"
)

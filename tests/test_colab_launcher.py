import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("remote_exit", "episode", "aspect", "gpu", "mode"), [
    (0, "episodes/example.json", "", "L4", "gemini"),
    (17, "episodes/example.json", "", "L4", "gemini"),
    (0, "examples/example.json", "16:9", "T4", "gemini"),
    (0, "examples/example.json", "9:16", "L4", "external"),
    (0, "examples/example.json", "9:16", "L4", "edge"),
    (0, "examples/example.json", "9:16", "L4", "studio"),
])
def test_shell_transfers_the_revision_and_preserves_failure_while_stopping(
    tmp_path, remote_exit, episode, aspect, gpu, mode,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    launcher = repo / "render_colab.sh"
    launcher.write_bytes((ROOT / launcher.name).read_bytes())
    launcher.chmod(0o755)
    for filename in (episode, "scripts/colab_render.py"):
        path = repo / filename
        path.parent.mkdir(exist_ok=True)
        path.write_text("{}\n")
    git = ("git", "-C", str(repo))
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "--all"], check=True)
    subprocess.run([*git, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "fixture"],
                   check=True)

    database = tmp_path / "account.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO settings VALUES (?, ?)",
                               [("GEMINI_API_KEY", "mock-key"), ("UNRELATED", "outside-job")])
    config = tmp_path / "rclone.conf"
    config.write_text("[gdrive]\ntype = drive\n")
    audio = tmp_path / "external narration.wav"
    audio.write_bytes(b"external audio")

    cli = tmp_path / "colab"
    cli.write_text("""#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["COLAB_CALL_LOG"], "a") as log:
    print(args[0], file=log)
if args == ["status", "-s", "frime"]:
    print("[colab] Session 'frime' not found.")
elif args == ["new", "-s", "frime", "--gpu", os.environ["COLAB_TEST_GPU"]] or args == ["stop", "-s", "frime"]:
    pass
elif args[0] == "ssh":
    command = args[args.index("--") + 1]
    assert command.strip().startswith("set -eu") and "exec python3 -u" in command
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as bundle:
        files = {entry.name: bundle.extractfile(entry).read() for entry in bundle if entry.isfile()}
    mode = os.environ["COLAB_TEST_MODE"]
    expected = {"repo.bundle", "runner.py", "rclone.conf", "job.json"}
    if mode in {"gemini", "external"}:
        expected.add("audio-input" if mode == "external" else "frimerise.db")
    elif mode == "studio":
        expected.update(("voice-ref.wav", "bgm-input"))
    assert set(files) == expected
    assert files["repo.bundle"].startswith(b"# v")
    metadata = json.loads(files["job.json"])
    assert metadata["episode"] == os.environ["COLAB_TEST_EPISODE"]
    assert metadata["aspect"] == os.environ["COLAB_TEST_ASPECT"]
    assert metadata["gpu"] == os.environ["COLAB_TEST_GPU"]
    assert metadata["audio"] == (mode == "external")
    if mode == "external":
        assert files["audio-input"] == b"external audio"
    elif mode == "edge":
        assert metadata["tts"] == "edge_kokoro_tts"
        assert metadata["continuous"]
        assert metadata["workers"] == 3
    elif mode == "studio":
        assert metadata["tts-engine"] == "voxcpm2"
        assert metadata["tts_backend"] == "voxcpm2_tts"
        assert metadata["voice_ref"] and metadata["denoise"] and metadata["bgm"] == "file"
        assert files["voice-ref.wav"] == files["bgm-input"] == b"external audio"
    else:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "frimerise.db"
            db.write_bytes(files["frimerise.db"])
            with sqlite3.connect(db) as connection:
                assert connection.execute("SELECT * FROM settings").fetchall() == [("GEMINI_API_KEY", "mock-key")]
    sys.exit(int(os.environ["COLAB_REMOTE_EXIT"]))
else:
    sys.exit(99)
""")
    cli.chmod(0o755)
    log = tmp_path / "cli.log"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": str(tmp_path),
           "COLAB_CALL_LOG": str(log), "COLAB_REMOTE_EXIT": str(remote_exit), "FRIMERISE_DB": str(database),
           "RCLONE_CONFIG": str(config), "FRIMERISE_RCLONE_REMOTE": "gdrive", "FRIMERISE_COLAB_GPU": gpu,
           "COLAB_TEST_GPU": gpu, "COLAB_TEST_EPISODE": episode, "COLAB_TEST_ASPECT": aspect,
           "COLAB_TEST_MODE": mode}
    args = ["--aspect", aspect] if aspect else []
    if mode == "external":
        args.extend(("--audio", str(audio)))
    elif mode == "edge":
        args.extend(("--continuous", "--tts", "edge_kokoro_tts", "--workers", "3"))
    elif mode == "studio":
        args.extend(("--tts-engine", "voxcpm2", "--voice-ref", str(audio), "--bgm", str(audio), "--denoise"))
    if mode != "gemini":
        env["FRIMERISE_DB"] = str(tmp_path / "missing.db")
    result = subprocess.run([str(launcher), episode, *args], cwd=repo, env=env,
                            capture_output=True, text=True)
    assert result.returncode == remote_exit, result.stderr
    assert log.read_text().splitlines() == ["status", "new", "ssh", "stop"]

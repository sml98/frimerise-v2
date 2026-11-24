"""Render a pinned FrimeRise revision on a Colab GPU and publish the QC artifacts."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


def run(*args, capture=False, **options):
    result = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None, **options)
    return result.stdout.strip() if capture else None


def render_environment(python, database, continuous):
    env = os.environ.copy()
    env["FRIMERISE_COLAB"] = "1"
    if database.is_file():
        env["FRIMERISE_DB"] = str(database)
    if not continuous:
        return env
    system_libraries = list(Path("/usr/local/lib").glob("python*/dist-packages/nvidia/*/lib"))
    initial = [*(str(path) for path in system_libraries), env.get("LD_LIBRARY_PATH", "")]
    env["LD_LIBRARY_PATH"] = os.pathsep.join(path for path in initial if path)
    probe = subprocess.run([str(python), "-c",
                            "from ctypes import CDLL; CDLL('libcublas.so.12'); CDLL('libcudnn.so.9')"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode:
        try:
            run(str(python), "-m", "pip", "install", "-q", "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10")
        except subprocess.CalledProcessError:
            print("Bibliotecas CUDA indisponíveis. O alinhador pode usar o fallback CPU.", file=sys.stderr)
        installed = python.parents[1].glob("lib/python*/site-packages/nvidia/*/lib")
        env["LD_LIBRARY_PATH"] = os.pathsep.join([*(str(path) for path in installed), *filter(None, initial)])
    return env


def main():
    os.umask(0o077)
    job = Path(__file__).resolve().parent
    metadata = json.loads((job / "job.json").read_text())
    db, config = job / "frimerise.db", job / "rclone.conf"
    for secret in (db, config, job / "audio-input"):
        if secret.is_file():
            secret.chmod(0o600)
    repo = job / "repo"
    gpu = run("nvidia-smi", "--query-gpu=name", "--format=csv,noheader", capture=True)
    expected_gpu = metadata.get("gpu", "L4")
    if not any(name.strip() in {f"NVIDIA {expected_gpu}", f"Tesla {expected_gpu}", expected_gpu}
               for name in gpu.splitlines()):
        raise RuntimeError(f"GPU NVIDIA {expected_gpu} esperada, runtime reportou {gpu!r}.")
    print(f"GPU: {gpu}", flush=True)
    run("git", "init", "-q", str(repo))
    run("git", "-C", str(repo), "fetch", "--quiet", str(job / "repo.bundle"), "HEAD")
    run("git", "-C", str(repo), "checkout", "--quiet", "--detach", "FETCH_HEAD")
    revision = run("git", "-C", str(repo), "rev-parse", "HEAD", capture=True)
    if revision != metadata["revision"]:
        raise RuntimeError("A revisão recebida no Colab diverge da revisão local.")
    episode = Path(metadata["episode"])
    episode_path = repo / episode
    spec = json.loads(episode_path.read_text())
    if aspect := metadata.get("aspect"):
        width, height = {"9:16": (1080, 1920), "16:9": (1920, 1080)}[aspect]
        spec.update(aspect_ratio=aspect, width=width, height=height)
        episode_path = job / episode.name
        episode_path.write_text(json.dumps(spec), encoding="utf-8")
    width, height, fps = (spec.get(key, default) for key, default in (("width", 1920), ("height", 1080), ("fps", 24)))

    packages = [package for package in ("ffmpeg", "rclone") if not shutil.which(package)]
    if not any(Path(path).exists() for path in ("/usr/lib/x86_64-linux-gnu/libcairo.so.2", "/usr/lib/libcairo.so.2")):
        packages.append("libcairo2")
    if packages:
        apt = [] if os.geteuid() == 0 else ["sudo", "-n"]
        run(*apt, "apt-get", "update", "-qq")
        run(*apt, "apt-get", "install", "-y", "-qq", *packages)
    run("ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc=s={width}x{height}:r={fps}",
        "-frames:v", "1", "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-preset", "p4", "-f", "null", "-")

    python = job / "venv" / "bin" / "python"
    run(sys.executable, "-m", "venv", "--without-pip", str(python.parents[1]))
    # Colab's SSH Python can omit ensurepip. Bootstrap only inside the job's venv.
    bootstrap = job / "get-pip.py"
    with urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=45) as download:
        bootstrap.write_bytes(download.read())
    run(str(python), str(bootstrap), "-q", "--disable-pip-version-check")
    run(str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check",
        "-r", str(repo / "requirements.txt"))
    if any(scene.get("plugin") == "motion_editorial" for scene in spec.get("scenes", ())):
        run(str(python), "-m", "playwright", "install", "--with-deps", "--only-shell", "chromium")

    artifacts = job / "artifacts"
    artifacts.mkdir()
    output = artifacts / f"{episode.stem}.mp4"
    print(f"Renderizando {episode} na revisão {revision[:12]}", flush=True)
    args = []
    if metadata.get("audio"):
        args.extend(("--audio", str(job / "audio-input")))
    elif metadata.get("continuous"):
        args.append("--continuous")
    for option in ("tts", "voice", "workers"):
        if value := metadata.get(option):
            args.extend((f"--{option}", str(value)))
    continuous = metadata.get("audio") or metadata.get("continuous") or spec.get("audio_mode") == "continuous"
    env = render_environment(python, db, continuous)
    report = json.loads(run(str(python), "-m", "core.engine", str(episode_path), str(output),
                            "--keyframes", str(artifacts), *args, cwd=repo, capture=True, env=env))
    if report["encoder"] != "h264_nvenc":
        raise RuntimeError(f"O render usou {report['encoder']}, mas o Colab exige h264_nvenc.")
    expected = {output.name, "10.png", "50.png", "90.png"}
    credits = output.with_name(f"{output.stem}_fontes.md")
    if credits.is_file():
        expected.add(credits.name)
    if {path.name for path in artifacts.iterdir() if path.is_file() and path.stat().st_size} != expected:
        raise RuntimeError("O render precisa gerar um MP4 e três keyframes PNG.")

    drive = f"{metadata['remote']}:FrimeRise_Exports/{episode.stem}"
    rclone = ("rclone", "--config", str(config))
    run(*rclone, "copy", str(artifacts), drive)
    run(*rclone, "check", "--one-way", str(artifacts), drive)
    video = json.loads(run(*rclone, "lsjson", "--stat", f"{drive}/{output.name}", capture=True))
    report["keyframes"] = [f"{drive}/{Path(path).name}" for path in report["keyframes"]]
    print(json.dumps({**report, "drive_path": f"{drive}/{output.name}", "drive_file_id": video.get("ID")}, indent=2))


if __name__ == "__main__":
    main()

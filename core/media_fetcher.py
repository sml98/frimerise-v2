"""Fetch render-ready, credited stock media with a shared, offline-friendly cache."""

import fcntl
import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from . import broll_selector, semantic_broll

HEADERS = {"User-Agent": "FrimeRise/2.0 (editorial video renderer)"}
PIXABAY = "https://pixabay.com/api/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
PEXELS = "https://api.pexels.com/v1/videos/search"
ARCHIVE = "https://archive.org"
TIMEOUT = 7
MAX_REEL_SEC = 2400
LOCAL_SOURCE = "Arquivo local (Google Flow / próprio)"
_credits = ContextVar("media_credits", default=None)
_scene_images = ContextVar("scene_images", default=None)
_assets = ContextVar("media_assets_root", default=None)
_semantic_cpu = ContextVar("media_semantic_cpu", default=False)
_semantic_shot_ids = ContextVar("media_semantic_shot_ids", default=None)
_public_domain_only = ContextVar("media_public_domain_only", default=False)


@dataclass(frozen=True)
class Candidate:
    urls: tuple[str, ...]
    page: str
    author: str
    license: str
    source: str
    extension: str
    title: str = ""
    license_url: str = ""


def _key(name="PIXABAY_API_KEY"):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return database_setting(os.environ.get("FRIMERISE_DB", "frimerise.db"), name)


def database_setting(path, name):
    """Read a single setting without creating or modifying the SQLite database."""
    path = Path(path).expanduser().resolve()
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as database:
            row = database.execute("SELECT value FROM settings WHERE upper(key) = ? LIMIT 1",
                                   (name.upper(),)).fetchone()
    except sqlite3.Error:
        return ""
    return row[0].strip() if row and isinstance(row[0], str) else ""


def _json(endpoint, *, headers=None, **params):
    url = f"{endpoint}?{urlencode(params, doseq=True)}" if params else endpoint
    request = Request(url, headers={**HEADERS, **(headers or {})})
    with urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read(3_000_000))


def _oriented(width, height, orientation):
    return (not orientation or not width or not height
            or (orientation == "vertical" and height >= width)
            or (orientation == "horizontal" and width >= height))


def _pixabay(query, video, orientation, limit):
    key = _key()
    if not key:
        return
    settings = {"key": key, "q": query[:100], "safesearch": "true", "per_page": 8}
    if video:
        hits = _json(PIXABAY + "videos/", **settings, video_type="film").get("hits", ())
    else:
        hits = _json(PIXABAY, **settings, orientation=orientation, image_type="photo").get("hits", ())
    for hit in hits:
        if video:
            if not 0 < hit.get("duration", 0) <= limit:
                continue
            sizes = (hit.get("videos", {}).get(size, {}) for size in ("medium", "small", "tiny"))
            urls = tuple(size["url"] for size in sizes if size.get("url")
                         and _oriented(size.get("width"), size.get("height"), orientation))
        else:
            urls = tuple(hit[field] for field in ("largeImageURL", "webformatURL") if hit.get(field))
        if urls:
            yield Candidate(urls, hit["pageURL"], hit.get("user", ""), "Pixabay Content License",
                            "Pixabay", ".mp4" if video else ".jpg", hit.get("tags", ""),
                            "https://pixabay.com/service/license-summary/")


def _pexels(query, video, orientation, limit):
    key = _key("PEXELS_API_KEY")
    if not video or not key:
        return
    params = {"query": query, "per_page": 8}
    if orientation:
        params["orientation"] = {"horizontal": "landscape", "vertical": "portrait"}[orientation]
    for hit in _json(PEXELS, headers={"Authorization": key}, **params).get("videos", ()):
        if not 1 <= hit.get("duration", 0) <= max(30, limit):
            continue
        files = [item for item in hit.get("video_files", ()) if item.get("link")
                 and item.get("file_type") == "video/mp4"
                 and _oriented(item.get("width"), item.get("height"), orientation)]
        files.sort(key=lambda item: abs(max(item.get("width") or 0, item.get("height") or 0) - 960))
        if files:
            yield Candidate(tuple(item["link"] for item in files[:2]), hit["url"], hit.get("user", {}).get("name", ""),
                            "Pexels License", "Pexels", ".mp4", query, "https://www.pexels.com/license/")


def _archive(query, video, orientation, limit):
    if not video:
        return
    if query.casefold().startswith("archive:"):
        identifiers = [query.partition(":")[2].strip()]
    else:
        terms = re.findall(r"[\w-]+", query)[:10]
        if not terms:
            return
        words = " ".join(terms)
        results = _json(ARCHIVE + "/advancedsearch.php", q=f"collection:prelinger AND mediatype:movies AND ({words})",
                        **{"fl[]": "identifier", "rows": 8, "output": "json"})
        identifiers = [result["identifier"] for result in results.get("response", {}).get("docs", ())]
    for item in identifiers:
        if not re.fullmatch(r"[\w][\w.-]+", item, flags=re.ASCII):
            continue
        data = _json(f"{ARCHIVE}/metadata/{quote(item)}")
        metadata = data.get("metadata", {})
        license_url = metadata.get("licenseurl", "")
        if isinstance(license_url, list):
            license_url = next((url for url in license_url if _public_domain(url)), "")
        if not _public_domain(license_url):
            continue
        files = []
        for file in data.get("files", ()):
            name = file.get("name", "")
            if (Path(name).suffix.lower() in {".mp4", ".ogv"} and not file.get("private")
                    and _oriented(int(file.get("width") or 0), int(file.get("height") or 0), orientation)):
                size = int(file.get("size") or 0)
                if size:
                    files.append((size, name))
        if files:
            urls = tuple(f"{ARCHIVE}/download/{quote(item)}/{quote(name, safe='')}" for _, name in sorted(files)[:3])
            creator = metadata.get("creator", "Prelinger Archives")
            if isinstance(creator, list):
                creator = ", ".join(creator)
            yield Candidate(urls, f"{ARCHIVE}/details/{quote(item)}", _plain(creator), "Domínio público",
                            "Internet Archive / Prelinger Archives", ".mp4",
                            _plain(metadata.get("title", item)), license_url.replace("http://", "https://", 1))


def _public_domain(url):
    parsed = urlsplit(url or "")
    return parsed.hostname == "creativecommons.org" and parsed.path.startswith(("/publicdomain/", "/licenses/publicdomain/"))


def _plain(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def clean_author(value):
    """Collapse adjacent duplicate words and repeated names from provider metadata."""
    result, keys = [], []
    for word in _plain(str(value or "")).split():
        result.append(word)
        keys.append(word.strip(",;:.()[]").casefold())
        for length in range(1, len(keys) // 2 + 1):
            if keys[-length:] == keys[-2 * length:-length]:
                del result[-2 * length:-length]
                del keys[-2 * length:-length]
                break
    return " ".join(result)


def _commons(query, video, orientation, limit):
    exact = query.casefold().startswith("file:")
    settings = ({"titles": query.replace("_", " "), "redirects": 1} if exact else
                {"generator": "search", "gsrnamespace": 6, "gsrlimit": 7,
                 "gsrsearch": f"{query} filetype:{'video' if video else 'bitmap'}"})
    pages = _json(COMMONS, action="query", prop="imageinfo", iiprop="url|mime|size|extmetadata",
                  iiurlwidth=1200, format="json", formatversion=2, **settings).get("query", {}).get("pages", ())
    for page in sorted(pages, key=lambda page: page.get("index", 0)):
        if not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        mime = info.get("mime", "")
        if not mime.startswith("video/" if video else "image/"):
            continue
        if video and (info.get("duration") or 0) > limit:
            continue
        if not _oriented(info.get("width"), info.get("height"), orientation):
            continue
        metadata = info.get("extmetadata", {})
        license_name = _plain(metadata.get("LicenseShortName", {}).get("value"))
        if not (re.match(r"^CC BY(?:-SA)?(?: |$)", license_name)
                or license_name.startswith(("CC0", "Public domain", "PDM"))):
            continue
        fields = ("url",) if video else ("thumburl", "url")
        urls = tuple(dict.fromkeys(info[field] for field in fields if info.get(field)))
        if urls:
            author = clean_author(metadata.get("Artist", {}).get("value"))
            extension = {"video/webm": ".webm", "video/ogg": ".ogv", "video/mp4": ".mp4"}.get(mime)
            if not video or extension:
                yield Candidate(urls, info.get("descriptionurl", ""), author[:180], license_name,
                                "Wikimedia Commons", extension or ".jpg", page.get("title", "").removeprefix("File:"),
                                _plain(metadata.get("LicenseUrl", {}).get("value")))


def _download(url, path, limit):
    if urlsplit(url).scheme != "https":
        return False
    with urlopen(Request(url, headers=HEADERS), timeout=TIMEOUT) as response, path.open("wb") as output:
        size = 0
        while chunk := response.read(128 * 1024):
            size += len(chunk)
            if size > limit:
                return False
            output.write(chunk)
    return size > 0


def _clip(source, output, start, duration):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(source),
                    "-t", str(duration), "-map", "0:v:0", "-an", "-sn", "-dn",
                    "-vf", "fps=30,scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "22",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)],
                   capture_output=True, check=True, timeout=55)


def _video_probe(path):
    data = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)], capture_output=True, check=True, timeout=12,
    )
    info = json.loads(data.stdout)
    stream = info["streams"][0]
    return stream["width"], stream["height"], float(info["format"]["duration"])


def _checked_asset(candidate, stage, video, orientation, max_duration):
    raw = stage / "download"
    result = stage / f"asset{candidate.extension}"
    for url in candidate.urls:
        try:
            if not _download(url, raw, 256_000_000 if video else 16_000_000):
                continue
            if video:
                width, height, duration = _video_probe(raw)
                if not 0 < duration <= max_duration or not _oriented(width, height, orientation):
                    continue
                raw.replace(result)
            else:
                with Image.open(raw) as image:
                    image = ImageOps.exif_transpose(image)
                    if not _oriented(image.width, image.height, orientation):
                        continue
                    image.thumbnail((1600, 1600))
                    image.convert("RGB").save(result, quality=91)
            return result
        except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError, Image.DecompressionBombError):
            raw = stage / "download"
            result = stage / f"asset{candidate.extension}"
            continue
    return None


def media_credit(path):
    """Return the source, author, reuse license and page attached to a cached file."""
    try:
        entry = json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
        if entry.get("author"):
            entry["author"] = clean_author(entry["author"])
        return entry
    except (OSError, ValueError):
        return {}


@contextmanager
def capture_media_credits(*, assets_root=None, semantic_cpu=False, public_domain_only=False, semantic_broll=False):
    credits = []
    token = _credits.set(credits)
    root = _assets.set(assets_root)
    semantic = _semantic_cpu.set(semantic_cpu)
    shots = _semantic_shot_ids.set(set() if semantic_broll else None)
    domain = _public_domain_only.set(public_domain_only)
    try:
        yield credits
    finally:
        _public_domain_only.reset(domain)
        _semantic_shot_ids.reset(shots)
        _semantic_cpu.reset(semantic)
        _assets.reset(root)
        _credits.reset(token)


@contextmanager
def capture_scene_images():
    images = {}
    token = _scene_images.set(images)
    try:
        yield images
    finally:
        _scene_images.reset(token)


def _record(path):
    images = _scene_images.get()
    if images is not None and Path(path).suffix.lower() in {".png", ".jpg", ".jpeg"}:
        if path not in images:
            with Path(path).open("rb") as image:
                images[path] = hashlib.file_digest(image, "sha256").hexdigest()
    credits = _credits.get()
    if credits is not None:
        entry = media_credit(path)
        if entry and entry not in credits:
            credits.append(entry)
    return path


def write_media_credits(path, credits):
    path = Path(path)
    if not credits and not path.is_file():
        return
    lines = ["# Créditos da mídia usada no render", ""]
    if not credits:
        lines.append("Nenhuma mídia externa foi usada.")
    for item in credits:
        title = (item.get("title") or item.get("source", "")).replace("[", "\\[").replace("]", "\\]")
        credit = " · ".join(filter(None, (item["source"], clean_author(item.get("author")), item.get("license"))))
        if item.get("type") == "video":
            credit += f" · trecho a partir de {item.get('start_sec', 0):g} s"
        license_url = item.get("license_url")
        license_link = f" · [Licença](<{license_url}>)" if license_url else ""
        label = f"[{title}](<{item['url']}>)" if item.get("url") else title
        lines.append(f"- {label} · {credit}{license_link}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cache_root():
    return Path(os.environ.get("FRIMERISE_MEDIA_CACHE", ".cache/media")).resolve()


def _key_for(options):
    return hashlib.sha256(json.dumps(options, ensure_ascii=False).encode()).hexdigest()


def _save_credit(path, credit):
    path.write_text(json.dumps(credit, ensure_ascii=False), encoding="utf-8")


def _fetch(query, source, orientation, *, video=False, limit=0):
    if not isinstance(query, str) or not query.strip():
        return None
    if source not in {"auto", "pixabay", "pexels", "archive", "wikimedia"} or orientation not in {None, "vertical", "horizontal"}:
        return None
    query = " ".join(query.split())
    options = [query.casefold(), source, orientation, video, limit]
    domain_only = _public_domain_only.get()
    key = _key_for([*options, "public-domain"] if domain_only else options)
    cache = _cache_root() / "reels" if video else _cache_root()
    sources = {"pixabay": _pixabay, "wikimedia": _commons, "pexels": _pexels, "archive": _archive}
    if source != "auto":
        order = (source,)
    elif query.casefold().startswith("archive:") and video:
        order = ("archive",)
    elif query.casefold().startswith("file:"):
        order = ("wikimedia",)
    else:
        order = ("pixabay", "pexels", "archive", "wikimedia") if video else ("pixabay", "wikimedia")
    if domain_only:
        order = tuple(provider for provider in order if provider in {"wikimedia", "archive"})
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with (cache / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for suffix in ((".mp4", ".webm", ".ogv") if video else (".jpg",)):
                path = cache / (key + suffix)
                if path.is_file() and path.stat().st_size:
                    return path if video else _record(path)
            for provider in order:
                try:
                    for candidate in sources[provider](query, video, orientation, limit):
                        if domain_only and not (
                            candidate.license.startswith(("CC0", "Public domain", "PDM", "Domínio público"))
                            or _public_domain(candidate.license_url)
                        ):
                            continue
                        with TemporaryDirectory(dir=cache) as directory:
                            stage = Path(directory)
                            asset = _checked_asset(candidate, stage, video, orientation, limit)
                            if asset is None:
                                continue
                            target = cache / (key + asset.suffix)
                            credit = stage / "credit.json"
                            _save_credit(credit, {"source": candidate.source, "author": clean_author(candidate.author),
                                                  "license": candidate.license, "url": candidate.page,
                                                  "title": candidate.title, "license_url": candidate.license_url,
                                                  "type": "video" if video else "image", "start_sec": 0})
                            credit.replace(target.with_suffix(".json"))
                            asset.replace(target)
                            return target if video else _record(target)
                except (OSError, URLError, ValueError, TypeError, KeyError):
                    # Provider exceptions can include the full API URL, including its key.
                    continue
    except OSError:
        pass
    return None


def local_media_file(value):
    """Resolve an explicit asset relative to the configured assets root."""
    path = Path(value).expanduser()
    path = ((_assets.get() or Path.cwd()) / path).resolve()
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".mp4"} or not path.is_file():
        raise ValueError(f"media_file precisa apontar para PNG, JPG ou MP4 legível: {path}")
    return path


def _local_credit(path, kind):
    credit = {"source": LOCAL_SOURCE, "title": path.name, "type": kind, "start_sec": 0}
    if _semantic_shot_ids.get() is not None:
        declared = media_credit(path)
        for field in ("source", "title", "author", "license", "license_url", "url"):
            value = declared.get(field)
            if value and isinstance(value, str):
                if field in {"license_url", "url"}:
                    try:
                        raw_url = urlsplit(value)
                        if raw_url.scheme != "https" or not raw_url.hostname or raw_url.username or raw_url.password:
                            continue
                        value = raw_url._replace(query="", fragment="").geturl()
                    except ValueError:
                        continue
                credit[field] = value
        offset = declared.get("source_offset_sec")
        if isinstance(offset, (int, float)) and isfinite(offset) and offset >= 0:
            credit["source_offset_sec"] = offset
    return credit


def _local_image(path, visual, narration):
    stat = path.stat()
    source_id = broll_selector._fingerprint(path, stat.st_size, stat.st_mtime_ns)
    options = [source_id, path.name, visual, narration, "local-poster-v1"]
    used_ids = _semantic_shot_ids.get()
    semantic_video = used_ids is not None and path.suffix.lower() == ".mp4"
    if semantic_video:
        options.extend([semantic_broll.MODEL_REVISION, semantic_broll.INDEX_VERSION])
    key = _key_for(options)
    cache = _cache_root() / "local"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{key}.png"
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not output.is_file():
            with TemporaryDirectory(dir=cache) as directory:
                stage = Path(directory) / output.name
                if path.suffix.lower() == ".mp4":
                    dimensions = _video_probe(path)
                    duration = dimensions[2]
                    if semantic_video:
                        shot = semantic_broll.select_shot(path, visual, narration, video_info=dimensions,
                                                         credit=_local_credit(path, "video"))
                        at = min(duration - .04, shot["frame_sec"])
                    else:
                        shot = broll_selector.select_shot(path, visual, narration,
                                                         device=broll_selector.scoring_device(_semantic_cpu.get()))
                        at = min(duration - .04, shot.frame_sec) if shot else duration / 2
                    broll_selector._ffmpeg("-ss", max(0, at), "-i", path, "-frames:v", "1", "-threads", "1", stage)
                else:
                    with Image.open(path) as image:
                        photo = ImageOps.exif_transpose(image)
                        photo.thumbnail((1600, 1600))
                        photo.convert("RGBA" if "A" in photo.getbands() or "transparency" in photo.info else "RGB").save(stage)
                _save_credit(output.with_suffix(".json"), _local_credit(path, "image"))
                stage.replace(output)
    return _record(output)


def fetch_image(query="", source="auto", orientation="horizontal", *, media_file=None, visual="", narration=""):
    """Fetch an image, or use the explicitly supplied local photo or video poster."""
    return _local_image(local_media_file(media_file), visual, narration) if media_file else _fetch(query, source, orientation)


def fetch_broll(query="", source="auto", orientation="horizontal", duration_sec=12, *, start_sec=0,
                max_duration=None, media_file=None, visual="", narration=""):
    """Return the best detected shot. Legacy start_sec remains accepted for old callers."""
    if isinstance(source, (int, float)):
        duration_sec, source = source, "auto"
    if max_duration is not None:
        duration_sec = max_duration
    if (not isinstance(duration_sec, (int, float)) or not 0 < duration_sec <= 60
            or not isinstance(start_sec, (int, float)) or not isfinite(start_sec) or start_sec < 0):
        return None
    local = local_media_file(media_file) if media_file else None
    if local and local.suffix.lower() != ".mp4":
        return None
    if not local and not (isinstance(query, str) and query.strip()):
        return None
    if local:
        stat = local.stat()
        identity = [local.name, broll_selector._fingerprint(local, stat.st_size, stat.st_mtime_ns)]
    else:
        identity = [" ".join(query.casefold().split()), source, orientation]
        if _public_domain_only.get():
            identity.append("public-domain")
    used_ids = _semantic_shot_ids.get()
    if used_ids is not None:
        return _fetch_semantic_broll(local, query, source, orientation, duration_sec, visual, narration, identity, used_ids)
    key = _key_for([identity, float(duration_sec), visual, narration, broll_selector.INDEX_VERSION,
                    broll_selector.MODEL_REVISION, "broll-v2"])
    cache = _cache_root() / "broll"
    cache.mkdir(parents=True, exist_ok=True)
    targets = {method: cache / f"{key}-{method}.mp4" for method in ("siglip2", "visual_cpu", "local_original")}
    with (cache / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if targets["siglip2"].is_file():
            return _record(targets["siglip2"])
        device = broll_selector.scoring_device(_semantic_cpu.get())
        if device == "visual":
            for method in ("visual_cpu", "local_original"):
                if targets[method].is_file():
                    return _record(targets[method])
        reel = local or _fetch(query, source, orientation, video=True, limit=MAX_REEL_SEC)
        if not reel:
            return None
        try:
            chosen = broll_selector.select_shot(reel, visual, narration, duration_sec=duration_sec, device=device)
            if chosen is None and not local:
                return None
            if chosen is not None:
                selection = asdict(chosen)
            else:
                _, _, duration = _video_probe(reel)
                selection = {"start_sec": 0, "end_sec": min(duration, duration_sec), "method": "local_original", "score": None}
            output = targets[selection["method"]]
            if output.is_file():
                return _record(output)
            with TemporaryDirectory(dir=cache) as directory:
                stage = Path(directory) / output.name
                start, end = selection["start_sec"], selection["end_sec"]
                _clip(reel, stage, start, end - start)
                credit = _local_credit(local, "video") if local else media_credit(reel)
                credit.update(start_sec=start, selection=selection)
                _save_credit(output.with_suffix(".json"), credit)
                stage.replace(output)
            return _record(output)
        except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError):
            if local:
                raise
            return None


def _fetch_semantic_broll(local, query, source, orientation, duration, visual, narration, identity, used_ids):
    local_credit = _local_credit(local, "video") if local else None
    key = _key_for([identity, float(duration), visual, narration, semantic_broll.INDEX_VERSION,
                    semantic_broll.MODEL_REVISION, sorted(used_ids), semantic_broll.METHOD, local_credit])
    cache = _cache_root() / "broll"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{key}-{semantic_broll.METHOD}.mp4"
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        selection = media_credit(output).get("selection", {})
        if not output.is_file() or not selection.get("shot_id"):
            reel = local or _fetch(query, source, orientation, video=True, limit=MAX_REEL_SEC)
            if not reel:
                return None
            credit = local_credit or media_credit(reel)
            selection = semantic_broll.select_shot(
                reel, visual, narration, duration_sec=duration, used_ids=used_ids, credit=credit,
                video_info=_video_probe(reel),
            )
            start, end = selection["start_sec"], selection["end_sec"]
            if "source_offset_sec" in credit:
                offset = credit["source_offset_sec"]
                selection.update(source_start_sec=start + offset, source_end_sec=end + offset)
            with TemporaryDirectory(dir=cache) as directory:
                stage = Path(directory) / output.name
                _clip(reel, stage, start, end - start)
                credit.update(start_sec=start, selection=selection)
                _save_credit(output.with_suffix(".json"), credit)
                stage.replace(output)
        used_ids.add(selection["shot_id"])
        return _record(output)

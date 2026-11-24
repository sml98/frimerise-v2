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
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

HEADERS = {"User-Agent": "FrimeRise/2.0 (editorial video renderer)"}
PIXABAY = "https://pixabay.com/api/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
PEXELS = "https://api.pexels.com/v1/videos/search"
ARCHIVE = "https://archive.org"
TIMEOUT = 7
_credits = ContextVar("media_credits", default=None)


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
    path = Path(os.environ.get("FRIMERISE_DB", "frimerise.db")).expanduser().resolve()
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as database:
            row = database.execute("SELECT value FROM settings WHERE upper(key) = ? LIMIT 1",
                                   (name,)).fetchone()
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
            author = _plain(metadata.get("Artist", {}).get("value"))
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


def _clip(source, output, start, duration, *, remote=False):
    network = ["-rw_timeout", "8000000", "-seekable", "1"] if remote else []
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *network, "-ss", str(start), "-i", str(source),
                    "-t", str(duration), "-map", "0:v:0", "-an", "-sn", "-dn",
                    "-vf", "scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "22",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)],
                   capture_output=True, check=True, timeout=55)


def _range_endpoint(url):
    request = Request(url, headers={**HEADERS, "Range": "bytes=0-1"})
    with urlopen(request, timeout=TIMEOUT) as response:
        if response.status == 206 and response.headers.get("Content-Range", "").startswith("bytes 0-1/"):
            return response.geturl()
    return None


def _checked_asset(candidate, stage, video, orientation, max_duration, start):
    raw = stage / "download"
    result = stage / f"asset{candidate.extension}"
    for url in candidate.urls:
        try:
            if candidate.source.startswith("Internet Archive"):
                origin = _range_endpoint(url)
                if not origin:
                    continue
                _clip(origin, result, start, max_duration, remote=True)
                raw = result
            elif not _download(url, raw, 40_000_000 if video else 16_000_000):
                continue
            if video:
                data = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=width,height:format=duration", "-of", "json", str(raw)],
                    capture_output=True, check=True, timeout=12,
                )
                probe = json.loads(data.stdout)
                stream = probe["streams"][0]
                duration = float(probe["format"]["duration"])
                if not isfinite(duration) or duration <= 0 or not _oriented(stream["width"], stream["height"], orientation):
                    continue
                if raw != result:
                    if start >= duration:
                        continue
                    if start or duration > max_duration + .05:
                        result = stage / "clip.mp4"
                        _clip(raw, result, start, max_duration)
                    else:
                        raw.replace(result)
                if result.stat().st_size < 1024:
                    continue
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
        return json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextmanager
def capture_media_credits():
    credits = []
    token = _credits.set(credits)
    try:
        yield credits
    finally:
        _credits.reset(token)


def _record(path):
    credits = _credits.get()
    if credits is not None:
        entry = media_credit(path)
        if entry and entry not in credits:
            credits.append(entry)
    return path


def write_media_credits(path, credits):
    if not credits:
        return
    lines = ["# Créditos da mídia usada no render", ""]
    for item in credits:
        title = (item.get("title") or item.get("source", "")).replace("[", "\\[").replace("]", "\\]")
        credit = " · ".join(filter(None, (item["source"], item.get("author"), item.get("license"))))
        if item.get("type") == "video":
            credit += f" · trecho a partir de {item.get('start_sec', 0):g} s"
        license_url = item.get("license_url")
        license_link = f" · [Licença](<{license_url}>)" if license_url else ""
        lines.append(f"- [{title}](<{item['url']}>) · {credit}{license_link}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fetch(query, source, orientation, *, video=False, limit=0, start=0):
    if not isinstance(query, str) or not query.strip():
        return None
    if source not in {"auto", "pixabay", "pexels", "archive", "wikimedia"} or orientation not in {None, "vertical", "horizontal"}:
        return None
    query = " ".join(query.split())
    if video:
        limit = int(limit) if int(limit) == limit else limit
        start = int(start) if int(start) == start else start
    cache_key = [query.casefold(), source, orientation, video, limit]
    if video:
        cache_key += [start, "trim-v1"]
    fingerprint = json.dumps(cache_key, ensure_ascii=False)
    key = hashlib.sha256(fingerprint.encode()).hexdigest()
    cache = Path(os.environ.get("FRIMERISE_MEDIA_CACHE", ".cache/media")).resolve()
    sources = {"pixabay": _pixabay, "wikimedia": _commons, "pexels": _pexels, "archive": _archive}
    if source != "auto":
        order = (source,)
    elif query.casefold().startswith("archive:") and video:
        order = ("archive",)
    elif query.casefold().startswith("file:"):
        order = ("wikimedia",)
    else:
        order = ("pixabay", "pexels", "archive", "wikimedia") if video else ("pixabay", "wikimedia")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with (cache / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for suffix in ((".mp4", ".webm", ".ogv") if video else (".jpg",)):
                path = cache / (key + suffix)
                if path.is_file() and path.stat().st_size:
                    return _record(path)
            for provider in order:
                try:
                    for candidate in sources[provider](query, video, orientation, limit):
                        with TemporaryDirectory(dir=cache) as directory:
                            stage = Path(directory)
                            asset = _checked_asset(candidate, stage, video, orientation, limit, start)
                            if asset is None:
                                continue
                            target = cache / (key + asset.suffix)
                            credit = stage / "credit.json"
                            credit.write_text(json.dumps({"source": candidate.source, "author": candidate.author,
                                                          "license": candidate.license, "url": candidate.page,
                                                          "title": candidate.title, "license_url": candidate.license_url,
                                                          "type": "video" if video else "image", "start_sec": start},
                                                         ensure_ascii=False), encoding="utf-8")
                            credit.replace(target.with_suffix(".json"))
                            return _record(asset.replace(target))
                except (OSError, URLError, ValueError, TypeError, KeyError):
                    # Provider exceptions can include the full API URL, including its key.
                    continue
    except OSError:
        pass
    return None


def fetch_image(query, source="auto", orientation="horizontal"):
    """Return a cached JPEG, or None if no suitable search result is available."""
    return _fetch(query, source, orientation)


def fetch_broll(query, source="auto", orientation="horizontal", duration_sec=12, *, start_sec=0, max_duration=None):
    """Return a cached clip trimmed to the requested duration."""
    if isinstance(source, (int, float)):
        duration_sec, source = source, "auto"
    if max_duration is not None:
        duration_sec = max_duration
    if (not isinstance(duration_sec, (int, float)) or not 0 < duration_sec <= 60
            or not isinstance(start_sec, (int, float)) or not isfinite(start_sec) or start_sec < 0):
        return None
    return _fetch(query, source, orientation, video=True, limit=duration_sec, start=start_sec)

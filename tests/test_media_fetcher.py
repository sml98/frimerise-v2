import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError

from PIL import Image

from core import media_fetcher as media


def test_pixabay_prefers_env_over_database_and_never_logs_provider_error(tmp_path, monkeypatch, capsys):
    database = tmp_path / "settings.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        connection.execute("INSERT INTO settings VALUES (?,?)", ("pixabay_api_key", "db-key"))
    monkeypatch.setenv("FRIMERISE_DB", str(database))
    monkeypatch.setenv("FRIMERISE_MEDIA_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assert media._key() == "db-key"
    monkeypatch.setenv("PIXABAY_API_KEY", "private-test-value")
    assert media._key() == "private-test-value"
    used = []

    def offline(endpoint, **params):
        used.append(endpoint)
        raise URLError(params.get("key", "offline"))

    monkeypatch.setattr(media, "_json", offline)
    assert media.fetch_image("offline fixture") is None
    assert used == [media.PIXABAY, media.COMMONS]
    assert not any(capsys.readouterr())


def test_concurrent_image_queries_download_once_and_preserve_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIMERISE_MEDIA_CACHE", str(tmp_path))
    calls = []

    def provider(*args):
        calls.append(args)
        yield media.Candidate(("https://example.invalid/image.jpg",), "https://example.invalid/file",
                              "Photographer", "Public domain", "Wikimedia Commons", ".jpg")

    def download(url, path, limit):
        Image.new("RGB", (64, 32)).save(path, format="JPEG")
        return True

    monkeypatch.setattr(media, "_commons", provider)
    monkeypatch.setattr(media, "_download", download)
    with ThreadPoolExecutor(3) as pool:
        paths = list(pool.map(lambda _: media.fetch_image("archive sample", "wikimedia"), range(3)))
    assert paths[0].is_file() and paths.count(paths[0]) == 3
    assert len(calls) == 1
    assert media.media_credit(paths[0])["author"] == "Photographer"


def test_pixabay_filters_broll_by_duration_and_orientation(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-only")
    monkeypatch.setattr(media, "_json", lambda *args, **kwargs: {"hits": [
        {"pageURL": "too-long", "duration": 11, "videos": {}},
        {"pageURL": "wrong-orientation", "duration": 8, "videos": {
            "medium": {"width": 400, "height": 800, "url": "https://example.invalid/vertical"},
        }},
        {"pageURL": "selected", "duration": 9, "videos": {
            "medium": {"width": 800, "height": 400, "url": "https://example.invalid/horizontal"},
        }},
    ]})
    clips = list(media._pixabay("fixture", True, "horizontal", 10))
    assert [clip.page for clip in clips] == ["selected"]


def test_archive_reads_public_domain_derivatives_in_size_order(monkeypatch):
    response = {
        "metadata": {"title": "Historical film", "creator": "A producer",
                     "licenseurl": "http://creativecommons.org/licenses/publicdomain/"},
        "files": [
            {"name": "large.mp4", "size": "80000", "width": "640", "height": "480"},
            {"name": "small.ogv", "size": "4000", "width": "400", "height": "300"},
            {"name": "portrait.mp4", "size": "3000", "width": "200", "height": "400"},
        ],
    }
    monkeypatch.setattr(media, "_json", lambda *args, **kwargs: response)
    (result,) = media._archive("archive:public-test", True, "horizontal", 12)
    assert [url.rsplit("/", 1)[-1] for url in result.urls] == ["small.ogv", "large.mp4"]
    response["metadata"]["licenseurl"] = "https://creativecommons.org/licenses/by/4.0/"
    assert not list(media._archive("archive:public-test", True, "horizontal", 12))


def test_pexels_uses_authorization_header_and_optional_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIMERISE_DB", str(tmp_path / "missing.db"))
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert not list(media._pexels("fixture", True, "vertical", 12))
    monkeypatch.setenv("PEXELS_API_KEY", "mock-only")

    def service(endpoint, *, headers, **parameters):
        assert endpoint == media.PEXELS
        assert headers["Authorization"] == "mock-only"
        assert parameters["orientation"] == "portrait"
        return {"videos": [{"duration": 12, "url": "https://example.invalid/credit", "user": {"name": "Creator"},
                            "video_files": [{"link": "https://example.invalid/clip.mp4", "file_type": "video/mp4",
                                             "width": 540, "height": 960}]}]}

    monkeypatch.setattr(media, "_json", service)
    (clip,) = media._pexels("fixture", True, "vertical", 12)
    assert clip.author == "Creator"

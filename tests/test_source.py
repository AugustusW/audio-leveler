import json

import pytest

import source


def test_is_url_only_accepts_http_schemes():
    assert source.is_url("https://podcasts.apple.com/tw/podcast/id1")
    assert source.is_url("http://example.com/a.mp3")
    assert not source.is_url("/Users/me/a.mp3")
    assert not source.is_url("a.mp3")


def test_apple_ids_extracts_collection_episode_and_country():
    got = source._apple_ids("https://podcasts.apple.com/tw/podcast/x/id1880110323?i=1000784427778")
    assert got == ("1880110323", "1000784427778", "tw")


def test_apple_ids_returns_none_for_other_hosts():
    assert source._apple_ids("https://example.com/id123?i=456") is None


def test_canonical_apple_drops_slug_and_storefront():
    a = "https://podcasts.apple.com/tw/podcast/show-name/id111?i=222"
    b = "https://podcasts.apple.com/us/podcast/episode-title/id111?i=222&si=abc"
    assert source._canonical_apple(a) == source._canonical_apple(b)


def test_canonical_apple_returns_none_for_show_page():
    assert source._canonical_apple("https://podcasts.apple.com/tw/podcast/x/id111") is None


def test_normalize_url_strips_tracking_params():
    assert source._normalize_url("https://Example.com/a/?utm_source=x&keep=1") == \
        "https://example.com/a?keep=1"


def test_resolve_apple_podcast_rejects_a_show_page():
    with pytest.raises(source.DownloadError) as e:
        source.resolve_apple_podcast("https://podcasts.apple.com/tw/podcast/x/id111")
    assert "show page" in str(e.value)


def test_resolve_apple_podcast_returns_media_url(monkeypatch):
    payload = {"results": [{"trackId": 222, "trackName": "EP13",
                            "episodeUrl": "https://cdn.example.com/ep13.mp3"}]}
    monkeypatch.setattr(source, "_lookup_json", lambda url: payload)
    got = source.resolve_apple_podcast("https://podcasts.apple.com/tw/podcast/x/id111?i=222")
    assert got == ("https://cdn.example.com/ep13.mp3", "EP13")


def test_resolve_apple_podcast_falls_back_to_the_rss_enclosure(monkeypatch):
    payload = {"results": [{"trackId": 222, "trackName": "EP13",
                            "feedUrl": "https://feed.example.com/rss"}]}
    monkeypatch.setattr(source, "_lookup_json", lambda url: payload)
    monkeypatch.setattr(source, "_enclosure_from_feed",
                        lambda feed, title: "https://cdn.example.com/from-feed.mp3")
    media, _ = source.resolve_apple_podcast("https://podcasts.apple.com/tw/podcast/x/id111?i=222")
    assert media == "https://cdn.example.com/from-feed.mp3"


def test_resolve_apple_podcast_names_the_failing_step(monkeypatch):
    monkeypatch.setattr(source, "_lookup_json", lambda url: {"results": []})
    with pytest.raises(source.DownloadError) as e:
        source.resolve_apple_podcast("https://podcasts.apple.com/tw/podcast/x/id111?i=222")
    assert "episode not found" in str(e.value)


def test_download_audio_reports_missing_yt_dlp(monkeypatch, tmp_path):
    monkeypatch.setattr(source.shutil, "which", lambda name: None)
    with pytest.raises(source.DownloadError) as e:
        source.download_audio("https://example.com/x", tmp_path)
    assert "pip install yt-dlp" in str(e.value)


def test_fetch_prefers_apple_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "resolve_apple_podcast",
                        lambda url: ("https://cdn.example.com/ep13.mp3", "EP13"))

    def fake_download(url, workdir):
        p = workdir / "ep13.mp3"
        p.write_bytes(b"audio")
        return str(p), "ignored"

    monkeypatch.setattr(source, "download_audio", fake_download)
    _, title = source.fetch("https://podcasts.apple.com/tw/podcast/x/id111?i=222",
                            cache_root=tmp_path)
    assert title == "EP13"


def test_fetch_passes_non_apple_urls_straight_to_yt_dlp(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "resolve_apple_podcast", lambda url: None)

    def fake_download(url, workdir):
        p = workdir / "v.mp3"
        p.write_bytes(b"audio")
        return str(p), "A Talk"

    monkeypatch.setattr(source, "download_audio", fake_download)
    _, title = source.fetch("https://youtube.com/watch?v=x", cache_root=tmp_path)
    assert title == "A Talk"


def test_fetch_reuses_the_cached_download_on_a_second_call(monkeypatch, tmp_path):
    """measure 與 apply 是兩次獨立呼叫打在同一個來源上；沒有快取就會下載兩次。"""
    monkeypatch.setattr(source, "resolve_apple_podcast",
                        lambda url: ("https://cdn.example.com/ep13.mp3", "EP13"))
    downloads = []

    def fake_download(url, workdir):
        downloads.append(url)
        p = workdir / "ep13.mp3"
        p.write_bytes(b"audio")
        return str(p), "EP13"

    monkeypatch.setattr(source, "download_audio", fake_download)
    url = "https://podcasts.apple.com/tw/podcast/x/id111?i=222"
    first, _ = source.fetch(url, cache_root=tmp_path)
    second, title = source.fetch(url, cache_root=tmp_path)
    assert first == second
    assert len(downloads) == 1
    assert title == "EP13"


def test_fetch_cache_survives_a_different_storefront_and_slug(monkeypatch, tmp_path):
    """同一集從不同國別頁或不同 slug 貼進來，應命中同一份快取。"""
    monkeypatch.setattr(source, "resolve_apple_podcast",
                        lambda url: ("https://cdn.example.com/ep13.mp3", "EP13"))
    downloads = []

    def fake_download(url, workdir):
        downloads.append(url)
        p = workdir / "ep13.mp3"
        p.write_bytes(b"audio")
        return str(p), "EP13"

    monkeypatch.setattr(source, "download_audio", fake_download)
    source.fetch("https://podcasts.apple.com/tw/podcast/show-name/id111?i=222", cache_root=tmp_path)
    source.fetch("https://podcasts.apple.com/us/podcast/episode-title/id111?i=222&si=x",
                 cache_root=tmp_path)
    assert len(downloads) == 1


def test_fetch_redownloads_when_the_cached_audio_is_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "resolve_apple_podcast",
                        lambda url: ("https://cdn.example.com/ep13.mp3", "EP13"))
    downloads = []

    def fake_download(url, workdir):
        downloads.append(url)
        p = workdir / "ep13.mp3"
        p.write_bytes(b"audio")
        return str(p), "EP13"

    monkeypatch.setattr(source, "download_audio", fake_download)
    url = "https://podcasts.apple.com/tw/podcast/x/id111?i=222"
    path, _ = source.fetch(url, cache_root=tmp_path)
    __import__("os").unlink(path)
    source.fetch(url, cache_root=tmp_path)
    assert len(downloads) == 2

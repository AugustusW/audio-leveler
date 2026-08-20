"""來源解析與下載。

移植自 audio-tldr-skill（MIT (c) AugustusW），2026-08-20。
上游：https://github.com/AugustusW/audio-tldr-skill
      skills/audio-tldr/scripts/transcribe.py

刻意的重複，見 spec ADR-2：為 183 行開一個共用發佈物過重。
**Apple lookup API 或 yt-dlp 介面變動時，兩邊都要修。** 當兩邊因上游變動而各自
修過一次以上時，回頭重新評估要不要抽成套件。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

TRACKING_PARAMS = frozenset({
    "si", "feature", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "fbclid", "gclid", "igsh", "ref",
})


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _normalize_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING_PARAMS]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", urlencode(q), ""))


def _canonical_apple(url: str):
    """Apple episode URL -> slug/storefront-independent identity string.
    The path slug differs between a show-page resolution (show name) and a
    directly copied episode link (episode title), and the storefront segment
    varies by region — none of that changes which episode it is. Identity is
    (collection id, episode id) only. Returns None when there is no episode id
    (bare show page) or the URL is not Apple Podcasts."""
    ids = _apple_ids(url)
    if ids is None:
        return None
    coll, ep, _country = ids
    if coll and ep:
        return f"https://podcasts.apple.com/podcast/id{coll}?i={ep}"
    return None


def cache_key(source: str) -> str:
    if is_url(source):
        canon = _canonical_apple(source) or _normalize_url(source)
        return hashlib.sha256(canon.encode()).hexdigest()
    h = hashlib.sha256()
    with open(source, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "audio-leveler"


class DownloadError(Exception):
    pass


def download_audio(url: str, workdir: Path):
    if not shutil.which("yt-dlp"):
        raise DownloadError(
            "yt-dlp not found — install with: pip install yt-dlp (or brew install yt-dlp)")
    probe = subprocess.run(
        ["yt-dlp", "--no-warnings", "--dump-json", "--no-download", "--no-playlist", url],
        capture_output=True, text=True, timeout=120,
    )
    if probe.returncode != 0:
        raise DownloadError(f"yt-dlp probe failed: {probe.stderr.strip()[:300]}")
    title = json.loads(probe.stdout).get("title", "untitled")
    safe = "".join(c for c in title if c.isalnum() or c in " -_")[:80] or "audio"
    # 上游 audio-tldr 在這裡寫死 --audio-format mp3，那是為轉錄挑的（轉錄不在乎多一代
    # 有損編碼）。這裡的產出要拿去處理再重新編碼，所以取 best：來源本來就是 mp3 時
    # ffmpeg 直接 copy，是 opus/m4a 時也不會白白多墊一代。
    out = workdir / f"{safe}.%(ext)s"
    dl = subprocess.run(
        ["yt-dlp", "--no-warnings", "-x", "--audio-format", "best",
         "-o", str(out), "--no-playlist", url],
        capture_output=True, text=True, timeout=1800,
    )
    if dl.returncode != 0:
        raise DownloadError(f"yt-dlp download failed: {dl.stderr.strip()[:300]}")
    found = sorted(f for f in workdir.iterdir() if f.is_file() and f.suffix != ".json")
    if not found:
        raise DownloadError("yt-dlp finished but produced no audio file")
    return str(found[0]), title


def _apple_ids(url: str):
    p = urlparse(url)
    if p.netloc.lower() != "podcasts.apple.com":
        return None
    m = re.search(r"/id(\d+)", p.path)
    coll = m.group(1) if m else None
    ep = dict(parse_qsl(p.query)).get("i")
    # storefront country is the first path segment (/tw/podcast/...) — the
    # lookup API misses region-specific shows without it
    seg = p.path.strip("/").split("/", 1)[0]
    country = seg if len(seg) == 2 and seg.isalpha() else None
    return (coll, ep, country)


def _lookup_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "audio-leveler"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _enclosure_from_feed(feed_url: str, title: str):
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "audio-leveler"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml_text = r.read().decode(errors="replace")
    except OSError:
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for item in root.iter("item"):
        if (item.findtext("title") or "").strip() == title.strip():
            enc = item.find("enclosure")
            if enc is not None:
                return enc.get("url")
    return None


def _resolve_show_to_latest(source: str):
    """Apple show-page URL (no ?i=) -> (canonical latest-episode URL, episode title).
    Returns (source, None) for anything that is not an Apple show page.
    Rewriting happens BEFORE cache-key computation so the cache binds to the
    episode — next week the same show link resolves to the new latest episode
    instead of hitting a stale cache entry."""
    ids = _apple_ids(source)
    if ids is None:
        return source, None
    coll, ep, country = ids
    if ep or not coll:
        return source, None
    q = f"https://itunes.apple.com/lookup?id={coll}&entity=podcastEpisode&limit=200"
    if country:
        q += f"&country={country}"
    try:
        data = _lookup_json(q)
    except (OSError, ValueError) as e:
        raise DownloadError(f"Apple lookup failed: {e}")
    episodes = [r for r in data.get("results", [])
                if r.get("kind") != "podcast" and r.get("trackId")]
    if not episodes:
        raise DownloadError("Apple Podcasts: no episodes found for this show")
    latest = max(episodes, key=lambda r: r.get("releaseDate") or "")
    sep = "&" if "?" in source else "?"
    return f"{source}{sep}i={latest['trackId']}", latest.get("trackName") or "untitled"


def resolve_apple_podcast(url: str):
    """Apple Podcasts page URL -> (media_url, episode_title).
    Returns None for non-Apple URLs; raises DownloadError with a specific
    reason when an Apple URL cannot be resolved."""
    ids = _apple_ids(url)
    if ids is None:
        return None
    coll, ep, country = ids
    if not ep:
        raise DownloadError(
            "Apple Podcasts: this is a show page, not an episode link — open the "
            "episode and copy its link (it needs ?i=<episodeId>)")
    if not coll:
        raise DownloadError("Apple Podcasts: could not parse the show id from the URL")
    # Episode-id lookup returns 0 results even with a storefront; the reliable
    # path is collection lookup listing recent episodes, then matching trackId.
    q = f"https://itunes.apple.com/lookup?id={coll}&entity=podcastEpisode&limit=200"
    if country:
        q += f"&country={country}"
    try:
        data = _lookup_json(q)
    except (OSError, ValueError) as e:
        raise DownloadError(f"Apple lookup failed: {e}")
    hits = [r for r in data.get("results", []) if str(r.get("trackId")) == str(ep)]
    if not hits:
        raise DownloadError(
            "Apple lookup: episode not found in the show's recent episodes "
            "(removed, region-locked, subscriber-only, or older than the 200-episode "
            "lookup window — paste the episode's RSS/media URL directly instead)")
    r0 = hits[0]
    title = r0.get("trackName") or "untitled"
    media = r0.get("episodeUrl")
    if not media and r0.get("feedUrl"):
        media = _enclosure_from_feed(r0["feedUrl"], title)
    if not media:
        raise DownloadError(
            "Apple Podcasts: no public media URL for this episode "
            "(subscriber-only content cannot be fetched)")
    return media, title




def fetch(url, cache_root=None):
    """URL -> (本地檔路徑, 標題)，同一集重複解析走快取。

    快取不是效能上的錦上添花：measure 與 apply 是兩次獨立的 CLI 呼叫打在同一個
    來源上（判斷發生在兩者之間），沒有快取的話一集 54 分鐘的節目會被下載兩次。

    節目頁（沒有 ?i=）先解成最新一集**再**算 cache key，快取才會綁在集數上——
    否則下週同一個節目連結會撞到上週那集的舊快取。
    """
    url, _ = _resolve_show_to_latest(url)
    root = Path(cache_root) if cache_root is not None else cache_dir()
    entry = root / cache_key(url)
    meta_path = entry / "meta.json"

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            meta = {}
        cached = entry / meta.get("audio", "")
        if meta.get("audio") and cached.exists():
            return str(cached), meta.get("title") or "untitled"

    entry.mkdir(parents=True, exist_ok=True)
    resolved = resolve_apple_podcast(url)
    if resolved is not None:
        media, title = resolved
        path, _ = download_audio(media, entry)
    else:
        path, title = download_audio(url, entry)
    meta_path.write_text(json.dumps(
        {"audio": Path(path).name, "title": title, "url": url}, ensure_ascii=False))
    return path, title

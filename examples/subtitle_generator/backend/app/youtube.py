"""
Fetch the audio of a YouTube video, for jobs started with a YouTube link (YOUTUBE_LINKS in .env).

Only single YouTube videos: the video id is taken from the link and yt-dlp is given a clean watch link built from it,
with only its YouTube video handler enabled, so a pasted link can't make it fetch anything else. Playlists and channels
are refused, and so are live streams and videos longer than MAX_VIDEO_SECONDS (checked before downloading). Only the
audio is downloaded (it is all the pipeline needs, about 1 MB a minute).

YouTube needs a JavaScript runtime to hand out its media links: Deno, installed by pip (the `deno` package), like FFmpeg.
YouTube changes often; when links stop working, update yt-dlp (see the README).
"""

import asyncio
import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .audio import MediaError
from .config import settings

logger = logging.getLogger(__name__)

HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# Paths that carry the video id: /shorts/ID, /live/ID, /embed/ID, /v/ID
ID_PATHS = ("shorts", "live", "embed", "v")

Progress = Callable[[int | None], None]  # percent downloaded, or None when unknown


def is_youtube(url: str) -> bool:
    host = (urlsplit(url.strip()).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def video_id(url: str) -> str | None:
    """The video id of a link to one YouTube video, or None (not YouTube, or a playlist / channel link)."""
    if not is_youtube(url):
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if (parts.hostname or "").lower().endswith("youtu.be"):
        found = segments[0] if segments else ""
    elif segments[:1] == ["watch"]:
        found = (parse_qs(parts.query).get("v") or [""])[0]
    elif len(segments) >= 2 and segments[0] in ID_PATHS:
        found = segments[1]
    else:
        found = ""
    return found if VIDEO_ID.fullmatch(found) else None


def watch_link(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def check_link(url: str) -> str:
    """Refuse a YouTube link that isn't one video, before a job is created. Returns the clean watch link."""
    vid = video_id(url)
    if not vid:
        raise MediaError("Paste a link to one YouTube video (youtube.com/watch?v=…, youtu.be/…, or a Shorts link). Playlists and channels aren't supported.")
    return watch_link(vid)


async def download(url: str, workdir: str, report: Progress) -> tuple[str, str]:
    """Download the audio of the video into workdir. Returns (path, video title)."""
    return await asyncio.to_thread(_download, check_link(url), workdir, report)


class _Log:
    """yt-dlp's messages go to the backend log, not to the terminal as raw output."""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        logger.info("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logger.warning("yt-dlp: %s", message)


def _options(workdir: str, report: Progress) -> Any:  # yt-dlp's own options type
    import deno

    def progress(state: dict) -> None:
        if state.get("status") == "downloading":
            total = state.get("total_bytes") or state.get("total_bytes_estimate")
            report(int(state.get("downloaded_bytes", 0) * 100 // total) if total else None)

    return {
        "allowed_extractors": ["youtube"],  # the single-video handler only: no playlists, no other sites, no generic pages
        "noplaylist": True,
        "format": "bestaudio[abr<=128]/bestaudio",
        "outtmpl": {"default": os.path.join(workdir, "audio.%(ext)s")},
        "max_filesize": settings.max_upload_mb * 1024 * 1024,
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
        "progress_hooks": [progress],
        "logger": _Log(),
        "quiet": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 30,
    }


def _download(url: str, workdir: str, report: Progress) -> tuple[str, str]:
    import yt_dlp
    from yt_dlp.utils import DownloadError

    try:
        with yt_dlp.YoutubeDL(_options(workdir, report)) as ydl:
            info = ydl.extract_info(url, download=False)  # details first: refuse before downloading anything
            _check_video(info)
            report(0)
            info = ydl.process_ie_result(info, download=True)
    except DownloadError as exc:
        raise MediaError(_message(str(exc))) from None
    downloads = info.get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if not path or not os.path.exists(path):
        raise MediaError(f"The audio is larger than {settings.max_upload_mb} MB, or YouTube sent nothing. Try another video.")
    return path, str(info.get("title") or "YouTube video")


def _check_video(info: Mapping[str, Any]) -> None:
    status = info.get("live_status")
    if status in ("is_live", "is_upcoming") or info.get("is_live"):
        raise MediaError("Live streams and premieres that haven't started aren't supported. Try again once the video is published.")
    duration = info.get("duration") or 0
    if duration > settings.max_video_seconds:
        raise MediaError(f"The video is {duration // 60} minutes long. The limit is {settings.max_video_seconds // 60} minutes.")


# yt-dlp's errors, in the words a user can act on
KNOWN_ERRORS = (
    ("private video", "This video is private."),
    ("confirm your age", "This video is age-restricted; it can't be downloaded without signing in."),
    ("not a bot", "YouTube asked to confirm this isn't a bot. Try again later, or upload the video file instead."),
    ("not available in your country", "This video isn't available in your country."),
    ("members-only", "This video is for channel members only."),
    ("video unavailable", "This video is unavailable. Check the link."),
    ("unable to download video data", "YouTube didn't send the video. Try again later."),
)


def _message(error: str) -> str:
    lowered = error.lower()
    for clue, message in KNOWN_ERRORS:
        if clue in lowered:
            return message
    detail = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?([\w-]{11}:\s*)?", "", error).strip()
    return f"YouTube download failed: {detail[:200]}" if detail else "YouTube download failed. Try again later."

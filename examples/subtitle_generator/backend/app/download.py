"""
Fetch a video from a direct link (DIRECT_VIDEO_LINKS in .env): a link that returns the video file itself
(https://example.com/talk.mp4, a public cloud-storage file link). The file is streamed to disk with the upload limits.
YouTube links go to youtube.py instead; links to other video sites are refused with a clear message, and links to
this computer or a private network are refused so a pasted link can't reach local services.
"""

import ipaddress
import os
import socket
from collections.abc import Callable
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

from .audio import MediaError
from .config import settings

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
# Types servers use for "a file" when they don't say which: accepted when the file name says it is a video
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream", "application/binary", "application/x-download", "application/force-download"}
# Video sites: their links open a player page, not the video file
SITE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "x.com",
    "twitter.com",
    "dailymotion.com",
    "tiktok.com",
    "twitch.tv",
    "linkedin.com",
    "reddit.com",
    "streamable.com",
)
# Some hosts (Wikimedia, for one) refuse requests from a library's default User-Agent: say what is asking
USER_AGENT = "SubtitleGenerator/0.1 (Bodhan AI Cookbook recipe; +https://github.com/Bodhan-AI/Bodhan-AI-Cookbook)"
NOT_A_FILE = "That link opens a web page, not the video file. Paste a link that points straight at the video file."

Progress = Callable[[int | None], None]  # percent downloaded, or None when unknown


def check_link(url: str) -> None:
    """Refuse a link before a job is created."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MediaError("Paste a full link that starts with http:// or https://.")
    if is_site(url):
        raise MediaError("Links to this video site aren't supported. Paste a YouTube link, or a link that points straight at the video file.")
    _check_host(parts.hostname)


def is_site(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SITE_HOSTS)


def name_from_link(url: str) -> str:
    """A readable name for the job: the file name in the link, or else the site's name."""
    parts = urlsplit(url)
    name = unquote(PurePosixPath(parts.path).name)
    return name if PurePosixPath(name).suffix.lower() in VIDEO_EXTENSIONS else (parts.hostname or url)


def _require_public(address: str) -> None:
    if not ipaddress.ip_address(address.split("%")[0]).is_global:
        raise MediaError("Links to this computer or to a private network aren't allowed.")


def _check_host(host: str) -> None:
    """Every address the host resolves to must be on the public internet."""
    try:
        addresses = {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise MediaError(f"Could not find {host}. Check the link.") from None
    for address in addresses:
        _require_public(address)


def _attachment_name(header: str) -> str:
    """The file name a server gives in Content-Disposition (cloud storage links often name the file only there)."""
    message = Message()
    message["content-disposition"] = header
    return PurePosixPath((message.get_filename() or "").replace("\\", "/")).name


async def download(url: str, workdir: str, report: Progress) -> tuple[str, str]:
    """Stream the video file to workdir, stopping at the upload size limit. Returns (path, file name)."""
    check_link(url)
    try:
        return await _fetch(url, workdir, report)
    except httpx.TimeoutException:
        host = urlsplit(url).hostname
        raise MediaError(f"{host} took too long to respond. Check the link, or try again later.") from None
    except httpx.HTTPError as exc:  # connection refused, too many redirects, broken response...
        raise MediaError(f"Could not download from that link ({type(exc).__name__}). Check that it opens the video.") from None


async def _fetch(url: str, workdir: str, report: Progress) -> tuple[str, str]:
    limit = settings.max_upload_mb * 1024 * 1024

    async def check_redirect(request: httpx.Request) -> None:
        _check_host(request.url.host)  # every hop, so a redirect can't lead to a private address

    async def check_connected(response: httpx.Response) -> None:
        # The address actually connected to: DNS can answer differently than it did for the check above (rebinding)
        stream = response.extensions.get("network_stream")
        peer = stream.get_extra_info("server_addr") if stream else None
        if peer:
            _require_public(str(peer[0]))

    hooks = {"request": [check_redirect], "response": [check_connected]}
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60, connect=20), headers=headers, event_hooks=hooks) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise MediaError(f"The link returned HTTP {response.status_code}. Check that it opens the video and is shared publicly.")
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            named = _attachment_name(response.headers.get("content-disposition", ""))
            name = named or name_from_link(str(response.url))
            if name == response.url.host:
                name = name_from_link(url)
            suffix = next((s for s in (PurePosixPath(named).suffix.lower(), PurePosixPath(response.url.path).suffix.lower()) if s in VIDEO_EXTENSIONS), "")
            if not (kind.startswith(("video/", "audio/")) or (kind in GENERIC_TYPES and suffix)):
                raise MediaError(NOT_A_FILE)
            total = int(response.headers.get("content-length") or 0)
            if total > limit:
                raise MediaError(f"The video is larger than {settings.max_upload_mb} MB.")
            path = os.path.join(workdir, "video" + (suffix or ".mp4"))
            size, last = 0, -1
            with open(path, "wb") as out:
                async for piece in response.aiter_bytes(1024 * 1024):
                    size += len(piece)
                    if size > limit:
                        raise MediaError(f"The video is larger than {settings.max_upload_mb} MB.")
                    out.write(piece)
                    percent = size * 100 // total if total else None
                    if percent != last:
                        report(percent)
                        last = percent
    if size == 0:
        raise MediaError("The link returned an empty file.")
    return path, name

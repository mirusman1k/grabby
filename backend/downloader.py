"""yt-dlp wrapper: metadata lookup, background download jobs, progress tracking."""
from __future__ import annotations

import threading
import uuid
from typing import Any

import yt_dlp

from . import config

# job_id -> mutable status dict. Read by the API, written by worker threads.
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_slots = threading.Semaphore(config.MAX_CONCURRENT)


# --------------------------------------------------------------------------
# Format selection -- the one place where UI choices become yt-dlp behaviour.
# --------------------------------------------------------------------------
def build_format_selector(quality: str, audio_only: bool, compatible: bool = True) -> str:
    """Turn the UI choice into a yt-dlp format string.

    compatible=True asks for H.264 video and AAC audio. That matters because
    QuickTime, Photos, iMovie and iOS decode only those; YouTube's top streams
    are VP9/AV1 with Opus, which yield an .mp4 that no Apple player can open.
    H.264 stops at 1080p on YouTube, so compatible=False is the way to 1440p
    and 4K -- those files need VLC or IINA.

    Every branch ends in a plain fallback so an unusual video still downloads.
    """
    if audio_only:
        # Extracted to mp3 by a postprocessor afterwards, so the source codec
        # does not affect whether the result plays.
        return "bestaudio/best"

    h = "" if quality == "best" else f"[height<=?{quality}]"
    if compatible:
        return (
            f"bestvideo[vcodec^=avc1]{h}+bestaudio[acodec^=mp4a]/"
            f"best[vcodec^=avc1]{h}/"
            f"bestvideo{h}+bestaudio/best{h}/best"
        )
    return f"bestvideo{h}+bestaudio/best{h}/best"


def _ydl_opts(job_id: str, quality: str, audio_only: bool, compatible: bool) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": build_format_selector(quality, audio_only, compatible),
        "outtmpl": str(config.DOWNLOAD_DIR / "%(title).150B [%(id)s].%(ext)s"),
        "progress_hooks": [_progress_hook(job_id)],
        "postprocessor_hooks": [_postprocessor_hook(job_id)],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "windowsfilenames": True,  # keeps names portable if you sync the folder
        "retries": 5,
        "fragment_retries": 5,
    }
    if config.FFMPEG:
        opts["ffmpeg_location"] = config.FFMPEG
    if audio_only:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # Ensure the merged container is something every player understands.
        opts["merge_output_format"] = "mp4"
    return opts


# --------------------------------------------------------------------------
# Progress plumbing
# --------------------------------------------------------------------------
def _update(job_id: str, **fields: Any) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _progress_hook(job_id: str):
    def hook(d: dict[str, Any]) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            _update(
                job_id,
                state="downloading",
                downloaded=done,
                total=total,
                speed=d.get("speed"),
                eta=d.get("eta"),
                percent=round(done / total * 100, 1) if total else None,
            )
        elif status == "finished":
            # The bytes are on disk; ffmpeg may still have work to do.
            _update(job_id, state="processing", percent=100.0, speed=None, eta=None)
        elif status == "error":
            _update(job_id, state="error", error="download failed")
    return hook


def _postprocessor_hook(job_id: str):
    def hook(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            _update(job_id, state="processing", stage=d.get("postprocessor"))
    return hook


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def _thumb(entry: dict[str, Any]) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    return thumbs[-1]["url"] if thumbs else None


def fetch_info(url: str) -> dict[str, Any]:
    """Look up a link without downloading. Returns a video or a playlist."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        # Flatten playlist entries: pulls titles/ids only instead of doing a
        # full network round-trip per video. A 200-item playlist resolves in
        # about a second this way rather than several minutes.
        "extract_flat": "in_playlist",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ValueError("That playlist has no downloadable videos.")
        return {
            "type": "playlist",
            "title": info.get("title") or "Playlist",
            "uploader": info.get("uploader") or info.get("channel"),
            "count": len(entries),
            "webpage_url": info.get("webpage_url") or url,
            "entries": [
                {
                    "id": e.get("id"),
                    "title": e.get("title") or "Untitled",
                    "url": e.get("url") or e.get("webpage_url"),
                    "duration": e.get("duration"),
                    "thumbnail": _thumb(e),
                }
                for e in entries
            ],
        }

    heights = sorted(
        {f["height"] for f in (info.get("formats") or [])
         if f.get("height") and f.get("vcodec") != "none"},
        reverse=True,
    )
    return {
        "type": "video",
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": _thumb(info),
        "view_count": info.get("view_count"),
        "webpage_url": info.get("webpage_url") or url,
        "available_heights": heights,
    }


def start_download(url: str, quality: str, audio_only: bool,
                   title: str | None = None, compatible: bool = True) -> str:
    """Kick off a download on a worker thread and return its job id."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "url": url,
            "title": title or url,
            "quality": "audio" if audio_only else quality,
            "state": "queued",
            "percent": None,
            "downloaded": 0,
            "total": None,
            "speed": None,
            "eta": None,
            "filepath": None,
            "error": None,
        }

    threading.Thread(
        target=_run, args=(job_id, url, quality, audio_only, compatible), daemon=True
    ).start()
    return job_id


def _run(job_id: str, url: str, quality: str, audio_only: bool, compatible: bool) -> None:
    with _slots:  # blocks here while MAX_CONCURRENT downloads are already active
        try:
            _update(job_id, state="starting")
            opts = _ydl_opts(job_id, quality, audio_only, compatible)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = info.get("requested_downloads", [{}])[0].get("filepath")
            _update(job_id, state="done", percent=100.0, filepath=path,
                    title=info.get("title") or url, speed=None, eta=None)
        except NotImplementedError as exc:
            _update(job_id, state="error", error=str(exc))
        except yt_dlp.utils.DownloadError as exc:
            _update(job_id, state="error", error=str(exc).replace("\033[0;31m", "").strip())
        except Exception as exc:  # noqa: BLE001 - surface anything else to the UI
            _update(job_id, state="error", error=f"{type(exc).__name__}: {exc}")


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def all_jobs() -> list[dict[str, Any]]:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def start_batch(items: list[dict[str, Any]], quality: str, audio_only: bool,
                compatible: bool = True) -> list[str]:
    """Queue several videos at once. Returns one job id per item.

    Every job is started immediately, but the semaphore in _run means only
    MAX_CONCURRENT are actually fetching at any moment -- the rest sit in
    'queued' until a slot frees up.
    """
    return [
        start_download(it["url"], quality, audio_only, it.get("title"), compatible)
        for it in items
        if it.get("url")
    ]

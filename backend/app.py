"""FastAPI app: serves the UI and exposes the download API."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, downloader, images

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Grabby", docs_url="/api/docs")

# Binding to 127.0.0.1 keeps other machines out, but a hostile web page can
# still reach a loopback server by pointing its own domain at 127.0.0.1 (DNS
# rebinding) -- the browser then treats it as same-origin. Requests genuinely
# aimed at this server always carry a loopback Host header, so anything else
# is rejected.
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


@app.middleware("http")
async def block_foreign_hosts(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host and host not in {h.strip("[]") for h in ALLOWED_HOSTS}:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    return await call_next(request)


class InfoRequest(BaseModel):
    url: str
    # Which browser's cookies to borrow: a name from config.BROWSERS, "none"
    # to force an anonymous request, or omitted to use whatever the
    # COOKIES_FROM_BROWSER / COOKIES_FILE settings say.
    browser: str | None = None


class DownloadItem(BaseModel):
    url: str
    title: str | None = None
    # Set for one item of a carousel, where url points at the whole post.
    playlist_index: int | None = None


class DownloadRequest(BaseModel):
    items: list[DownloadItem]
    quality: str = "best"
    audio_only: bool = False
    compatible: bool = True
    browser: str | None = None
    # Set when downloading a profile: every post lands in this subfolder.
    folder: str | None = None


@app.post("/api/info")
def info(req: InfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "No URL given.")
    try:
        return downloader.fetch_info(url, req.browser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, downloader.friendly_error(str(exc), url, req.browser)) from exc


@app.post("/api/download")
def download(req: DownloadRequest):
    items = [
        {"url": i.url.strip(), "title": i.title, "playlist_index": i.playlist_index}
        for i in req.items if i.url.strip()
    ]
    if not items:
        raise HTTPException(400, "Nothing selected to download.")
    try:
        job_ids = downloader.start_batch(
            items, req.quality, req.audio_only, req.compatible, req.browser,
            req.folder)
    except ValueError as exc:  # bad browser name / missing cookies file
        raise HTTPException(400, str(exc)) from exc
    return {"job_ids": job_ids, "queued": len(job_ids)}


class ImagesRequest(BaseModel):
    url: str
    title: str | None = None
    folder: str | None = None
    browser: str | None = None


@app.post("/api/images")
def save_images(req: ImagesRequest):
    """Save every still image in a post."""
    url = req.url.strip()
    try:
        found = images.images_for(url, req.browser)
        if not found:
            raise HTTPException(404, "No images in that post.")
        # One post's pictures belong together, the same way its videos do, so
        # anything with more than one image gets a folder named after the post.
        # req.folder is the account folder for a profile download; the post
        # folder nests inside it rather than replacing it.
        segments = [req.folder] if req.folder else []
        if len(found) > 1:
            segments.append(images.folder_for_post(url, req.title))
        paths = images.save_images(found, req.title or "image", segments, req.browser)
        platform = downloader.platform_of(url)
        for path in paths:
            downloader.register_saved(Path(path).name, path, platform, "image")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not save those images: {exc}") from exc
    return {"saved": len(paths), "paths": paths}


class AvatarRequest(BaseModel):
    user_id: str
    browser: str | None = None


@app.post("/api/avatar")
def avatar(req: AvatarRequest):
    """Save an Instagram account's profile picture. Works without a login."""
    try:
        result = downloader.avatar_result(req.user_id.strip(), req.browser)
        downloader.register_saved(
            f"@{result['username']} profile picture", result["filepath"],
            "instagram", "image")
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not save that profile picture: {exc}") from exc


@app.get("/api/options")
def options():
    """What the UI needs to build its controls, so browser names live in one place."""
    return {
        "browsers": list(config.BROWSERS),
        "cookies_from_browser": config.COOKIES_FROM_BROWSER,
        "cookies_file": str(config.COOKIES_FILE) if config.COOKIES_FILE else None,
    }


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: str, keep_file: bool = False):
    """Remove a download from the list and, by default, delete the file."""
    if not downloader.forget_job(job_id, delete_file=not keep_file):
        raise HTTPException(404, "No such download.")
    return {"removed": job_id}


@app.get("/api/jobs")
def jobs():
    return {"jobs": downloader.all_jobs(), "download_dir": str(config.DOWNLOAD_DIR)}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job


class RevealRequest(BaseModel):
    # Which download to point at. Without one, the download folder opens.
    # A job id rather than a path: the browser never gets to name a file for
    # the OS to open, so there is nothing to escape the download folder with.
    job_id: str | None = None


@app.post("/api/reveal")
def reveal(req: RevealRequest | None = None):
    """Open the download folder, or reveal one file inside it."""
    target = config.DOWNLOAD_DIR
    select = None

    if req and req.job_id:
        job = downloader.get_job(req.job_id)
        if not job:
            raise HTTPException(404, "No such download.")
        if not job.get("filepath"):
            raise HTTPException(400, "That download has no file yet.")
        path = Path(job["filepath"])
        try:
            resolved = path.resolve()
            resolved.relative_to(config.DOWNLOAD_DIR.resolve())
        except (OSError, ValueError):
            raise HTTPException(400, "That file is outside the download folder.")
        if not resolved.exists():
            raise HTTPException(404, "That file is no longer on disk.")
        select, target = resolved, resolved.parent

    if sys.platform == "darwin":
        # -R reveals the file in Finder with it selected, rather than just
        # opening the folder and leaving you to find it.
        cmd = ["open", "-R", str(select)] if select else ["open", str(target)]
    elif sys.platform.startswith("win"):
        cmd = ["explorer", f"/select,{select}"] if select else ["explorer", str(target)]
    else:
        # No portable "select this file" on Linux; open the containing folder.
        cmd = ["xdg-open", str(target)]
    subprocess.run(cmd, check=False)
    return {"opened": str(select or target)}


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

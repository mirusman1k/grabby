"""FastAPI app: serves the UI and exposes the download API."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, downloader

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


class DownloadItem(BaseModel):
    url: str
    title: str | None = None


class DownloadRequest(BaseModel):
    items: list[DownloadItem]
    quality: str = "best"
    audio_only: bool = False
    compatible: bool = True


@app.post("/api/info")
def info(req: InfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "No URL given.")
    try:
        return downloader.fetch_info(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not read that link: {exc}") from exc


@app.post("/api/download")
def download(req: DownloadRequest):
    items = [{"url": i.url.strip(), "title": i.title} for i in req.items if i.url.strip()]
    if not items:
        raise HTTPException(400, "Nothing selected to download.")
    job_ids = downloader.start_batch(items, req.quality, req.audio_only, req.compatible)
    return {"job_ids": job_ids, "queued": len(job_ids)}


@app.get("/api/jobs")
def jobs():
    return {"jobs": downloader.all_jobs(), "download_dir": str(config.DOWNLOAD_DIR)}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job


@app.post("/api/reveal")
def reveal():
    """Open the download folder in the OS file browser."""
    d = str(config.DOWNLOAD_DIR)
    if sys.platform == "darwin":
        subprocess.run(["open", d], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["explorer", d], check=False)
    else:
        subprocess.run(["xdg-open", d], check=False)
    return {"opened": d}


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

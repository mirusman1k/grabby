"""Configuration. Override any of these with environment variables."""
import os
import shutil
from pathlib import Path

# A GUI app started by launchd inherits a bare PATH -- no /opt/homebrew/bin --
# so Homebrew-installed tools are invisible unless we put them back.
for _extra in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
               str(Path.home() / ".local/bin")):
    if os.path.isdir(_extra) and _extra not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _extra + os.pathsep + os.environ.get("PATH", "")

# Resolve to an absolute path once and hand it to yt-dlp explicitly, so the
# merge step never depends on PATH lookup at all.
FFMPEG = shutil.which("ffmpeg")

# Where finished files land. Change DOWNLOAD_DIR env var to point elsewhere.
DOWNLOAD_DIR = Path(
    os.environ.get("DOWNLOAD_DIR", Path.home() / "Downloads" / "yt-downloader")
).expanduser()

# Bind to loopback only. This server executes ffmpeg and writes to your disk --
# exposing it on 0.0.0.0 would let anyone on your network drive both.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# How many downloads may run at once.
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "3"))

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

"""Configuration. Override any of these with environment variables."""
import os
import shutil
import sys
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

# Intermediates (partial downloads, the source file an mp3 is extracted from)
# are staged here rather than in the download folder, so a half-finished or
# about-to-be-deleted file never shares a name with something already saved.
TEMP_DIR = DOWNLOAD_DIR / ".grabby-tmp"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# A crash or a force-quit mid-download leaves a staging directory behind, and
# nothing else ever reads it, so start each run with it empty.
if TEMP_DIR.exists():
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Authentication for login-gated sites
# --------------------------------------------------------------------------
# Instagram serves almost nothing to logged-out clients, and some LinkedIn and
# X posts are restricted too. Both knobs below are optional; without either,
# only public content downloads.
#
# COOKIES_FROM_BROWSER reads the live session straight out of a browser
# profile. Safari and Firefox hand it over quietly; Chromium browsers encrypt
# their cookie store with a Keychain key, so macOS prompts for your password.
#
# COOKIES_FILE points at a Netscape-format cookies.txt you exported yourself.
# No Keychain prompt and it touches nothing but that file, at the cost of
# re-exporting whenever the session expires.
BROWSERS = ("safari", "chrome", "firefox", "brave", "edge", "chromium", "opera", "vivaldi")

COOKIES_FROM_BROWSER = os.environ.get("COOKIES_FROM_BROWSER") or None

_cookies_file = os.environ.get("COOKIES_FILE")
COOKIES_FILE = Path(_cookies_file).expanduser() if _cookies_file else None


# --------------------------------------------------------------------------
# Instagram profile listing
# --------------------------------------------------------------------------
# yt-dlp's instagram:user extractor is disabled upstream (_WORKING = False) --
# Instagram retired the GraphQL query it depended on. gallery-dl still tracks
# the current API, so it is used to enumerate a profile's posts; yt-dlp then
# downloads each one, which keeps quality selection, progress and naming
# identical to every other download.
#
# Look beside this interpreter first: a venv install puts gallery-dl in the
# same bin directory, which PATH may not include when launched from Finder.
GALLERY_DL = (
    shutil.which("gallery-dl", path=str(Path(sys.executable).parent))
    or shutil.which("gallery-dl")
)

# How many of a profile's newest posts to list. Listing is one API page per 30
# or so posts, so an uncapped fetch on a large account is slow and makes the
# rate limiting that Instagram applies to bulk reads much more likely.
MAX_PROFILE_POSTS = int(os.environ.get("MAX_PROFILE_POSTS", "200"))

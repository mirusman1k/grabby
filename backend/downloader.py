"""yt-dlp wrapper: metadata lookup, background download jobs, progress tracking."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import uuid
from typing import Any
from urllib.parse import urlparse

import yt_dlp

from pathlib import Path

from . import config, images as _images

# job_id -> mutable status dict. Read by the API, written by worker threads.
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_slots = threading.Semaphore(config.MAX_CONCURRENT)


# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------
# Format selection and the "compatible" toggle only make sense per-site, so
# every link gets classified once and the answer is passed down.
_PLATFORM_PATTERNS = (
    ("youtube",   re.compile(r"(?:^|\.)(?:youtube\.com|youtu\.be|youtube-nocookie\.com)$", re.I)),
    ("instagram", re.compile(r"(?:^|\.)(?:instagram\.com|instagr\.am|ddinstagram\.com)$", re.I)),
    ("twitter",   re.compile(r"(?:^|\.)(?:twitter\.com|x\.com|t\.co|fxtwitter\.com|vxtwitter\.com)$", re.I)),
    ("linkedin",  re.compile(r"(?:^|\.)(?:linkedin\.com|lnkd\.in)$", re.I)),
)

# Sites that serve H.264 video with AAC audio and nothing else. Apple players
# open their files unconditionally, so the codec juggling below is wasted work
# on them -- and actively harmful, see build_format_selector.
MUXED_PLATFORMS = frozenset({"instagram", "twitter", "linkedin"})


def platform_of(url: str) -> str:
    """Classify a link by host. Returns 'other' for anything unrecognised."""
    host = urlparse(url.strip()).hostname or ""
    for name, pattern in _PLATFORM_PATTERNS:
        if pattern.search(host):
            return name
    return "other"


# --------------------------------------------------------------------------
# Format selection -- the one place where UI choices become yt-dlp behaviour.
# --------------------------------------------------------------------------
def build_format_selector(quality: str, audio_only: bool, compatible: bool = True,
                          platform: str = "youtube") -> str:
    """Turn the UI choice into a yt-dlp format string.

    compatible=True asks for H.264 video and AAC audio. That matters because
    QuickTime, Photos, iMovie and iOS decode only those; YouTube's top streams
    are VP9/AV1 with Opus, which yield an .mp4 that no Apple player can open.
    H.264 stops at 1080p on YouTube, so compatible=False is the way to 1440p
    and 4K -- those files need VLC or IINA.

    On Instagram, X and LinkedIn the codec filters are skipped entirely. Those
    sites are H.264/AAC only, so there is nothing to protect against -- and
    filtering hurts: X advertises its best file (a progressive, already-muxed
    MP4) with the codec fields left empty, and a yt-dlp filter drops any format
    whose field is missing. Asking for vcodec^=avc1 therefore rejects the one
    stream we actually want and falls through to downloading HLS video and HLS
    audio separately, then paying for an ffmpeg merge to rebuild the file that
    was available in one piece.

    Every branch ends in a plain fallback so an unusual video still downloads.
    """
    if audio_only:
        # Extracted to mp3 by a postprocessor afterwards, so the source codec
        # does not affect whether the result plays.
        return "bestaudio/best"

    # LinkedIn (and the odd X post) publish formats with no height at all --
    # only a bitrate distinguishes them. Those arrive as "tbr:<kbps>" so there
    # is still something to choose between.
    if quality.startswith("tbr:"):
        h = f"[tbr<=?{quality[4:]}]"
    else:
        h = "" if quality == "best" else f"[height<=?{quality}]"

    if platform in MUXED_PLATFORMS:
        # Muxed file first, merge only if the site has nothing pre-combined.
        return f"best{h}/bestvideo{h}+bestaudio/best/b"

    if compatible:
        return (
            f"bestvideo[vcodec^=avc1]{h}+bestaudio[acodec^=mp4a]/"
            f"best[vcodec^=avc1]{h}/"
            f"bestvideo{h}+bestaudio/best{h}/best"
        )
    return f"bestvideo{h}+bestaudio/best{h}/best"


# --------------------------------------------------------------------------
# Cookies -- the difference between "empty media response" and a download
# --------------------------------------------------------------------------
def _cookie_opts(browser: str | None = None) -> dict[str, Any]:
    """yt-dlp options carrying a logged-in session, or {} for anonymous.

    A browser chosen in the UI wins over the COOKIES_FROM_BROWSER default,
    which in turn wins over an exported COOKIES_FILE. Passing "none"
    explicitly forces an anonymous request even when a default is configured.
    """
    if browser == "none":
        return {}
    choice = browser or config.COOKIES_FROM_BROWSER
    if choice:
        if choice not in config.BROWSERS:
            raise ValueError(
                f"Unknown browser {choice!r}. Try one of: {', '.join(config.BROWSERS)}."
            )
        # (browser, profile, keyring, container) -- defaults for the last three.
        return {"cookiesfrombrowser": (choice, None, None, None)}
    if config.COOKIES_FILE:
        if not config.COOKIES_FILE.is_file():
            raise ValueError(f"COOKIES_FILE is set but {config.COOKIES_FILE} does not exist.")
        return {"cookiefile": str(config.COOKIES_FILE)}
    return {}


# One carousel is one post, so its clips are kept together in a folder named
# after it rather than scattered through the download directory. Both templates
# are built from yt-dlp's own fields so it does the filename sanitising -- a
# caption interpolated by hand is a path-traversal waiting to happen.
_SINGLE_TMPL = "%(title).150B [%(id)s].%(ext)s"
# A profile's posts share a folder. The name is a validated Instagram username
# plus a fixed suffix, never free text, so it cannot escape the download dir.
# Brackets and @ are safe in a path and match how single files are named.
# A slash, a backslash or a leading dot-dot is what this exists to reject.
_SAFE_FOLDER = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._@\[\] -]{1,64}$")
_CAROUSEL_TMPL = ("%(playlist_title).120B [%(playlist_id)s]/"
                  "%(playlist_index)02d - %(title).100B [%(id)s].%(ext)s")


def _ydl_opts(job_id: str, quality: str, audio_only: bool, compatible: bool,
              platform: str = "youtube", browser: str | None = None,
              playlist_index: int | None = None,
              folder: str | None = None) -> dict[str, Any]:
    if playlist_index is not None:
        tmpl = _CAROUSEL_TMPL
    elif folder:
        # Validated at the API boundary, but re-checked here because this is
        # the line that builds a filesystem path.
        if not _SAFE_FOLDER.match(folder):
            raise ValueError(f"Unsafe folder name: {folder!r}")
        tmpl = f"{folder}/{_SINGLE_TMPL}"
    else:
        tmpl = _SINGLE_TMPL
    opts: dict[str, Any] = {
        "format": build_format_selector(quality, audio_only, compatible, platform),
        "outtmpl": str(config.DOWNLOAD_DIR / tmpl),
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
        # Extracting audio downloads the source first, and on sites that serve
        # audio in an mp4 container that source lands on exactly the path an
        # already-downloaded video occupies -- which the extractor then
        # deletes once it has the mp3, taking the video with it.
        #
        # yt-dlp's "temp" path only covers .part fragments; the complete file
        # still lands in home before postprocessing. So the whole job runs
        # inside the staging directory and _run moves the finished mp3 out,
        # which keeps the saved name clean.
        # One directory per job, so concurrent audio jobs cannot collide and
        # cleanup can remove the whole thing without inspecting what is in it.
        opts["outtmpl"] = str(config.TEMP_DIR / job_id / tmpl)
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # Ensure the merged container is something every player understands.
        opts["merge_output_format"] = "mp4"
    opts.update(_cookie_opts(browser))
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


# --------------------------------------------------------------------------
# Instagram profile pictures
# --------------------------------------------------------------------------
# Instagram gates almost everything from logged-out clients, but one endpoint
# still answers: /api/v1/users/<numeric id>/info/. It needs the web app id that
# yt-dlp already sends, and it returns a reduced record -- the 150x150 avatar
# and little else. The HD versions and the follower counts require a session.
#
# The catch is the *numeric* id. Resolving a username to one (web_profile_info,
# topsearch) is 401 without a login, and the profile page is now a bare JS
# shell that does not contain it. Any post by the account does, though, and
# yt-dlp hands it over as uploader_id -- so an avatar is reachable from a post
# link even when the profile itself is not.
_IG_USER_INFO = "https://i.instagram.com/api/v1/users/{}/info/"


def _ig_api_headers() -> dict[str, str]:
    """Borrow yt-dlp's Instagram headers so the app id stays in sync with it."""
    from yt_dlp.extractor.instagram import InstagramIE
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    ie = InstagramIE(ydl)
    ie.initialize()
    return dict(ie._api_headers)


def _best_avatar(user: dict[str, Any]) -> tuple[str | None, int | None]:
    """Pick the largest profile picture Instagram offered.

    A logged-out response carries one field, profile_pic_url, and it is always
    the 150x150 thumbnail. A session unlocks the HD fields below. Rewriting the
    CDN URL to ask for a bigger size does not work -- the oh= parameter signs
    the whole URL including the resize token, so any edit returns 403.
    """
    hd = (user.get("hd_profile_pic_url_info") or {}).get("url")
    if hd:
        info = user["hd_profile_pic_url_info"]
        return hd, info.get("width")

    versions = [v for v in (user.get("hd_profile_pic_versions") or []) if v.get("url")]
    if versions:
        best = max(versions, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))
        return best["url"], best.get("width")

    if user.get("profile_pic_url_hd"):
        return user["profile_pic_url_hd"], None
    return user.get("profile_pic_url"), 150 if user.get("profile_pic_url") else None


def instagram_avatar(user_id: str, browser: str | None = None) -> dict[str, Any]:
    """Look up an account's profile picture from its numeric id. No login."""
    if not str(user_id).isdigit():
        raise ValueError("Instagram user id must be numeric.")
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True}
    opts.update(_cookie_opts(browser))
    ydl = yt_dlp.YoutubeDL(opts)
    req = yt_dlp.networking.Request(_IG_USER_INFO.format(user_id),
                                    headers=_ig_api_headers())
    try:
        user = json.loads(ydl.urlopen(req).read()).get("user") or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Instagram did not return that profile: {exc}") from exc
    pic, width = _best_avatar(user)
    if not pic:
        raise ValueError("No profile picture in Instagram's response.")
    return {
        "user_id": str(user_id),
        "username": user.get("username") or str(user_id),
        "url": pic,
        "width": width,
        # 150x150 is all Instagram returns to a logged-out client; the HD
        # fields only appear when the request carries a session.
        "hd": bool(width and width > 150),
    }


def save_instagram_avatar(user_id: str, browser: str | None = None) -> str:
    """Download a profile picture into the download folder. Returns the path."""
    meta = instagram_avatar(user_id, browser)
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    data = ydl.urlopen(yt_dlp.networking.Request(meta["url"])).read()
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Instagram returned something that is not a JPEG.")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", meta["username"])[:40]
    path = config.DOWNLOAD_DIR / f"{name} [profile pic].jpg"
    path.write_bytes(data)
    return str(path)


def avatar_result(user_id: str, browser: str | None = None) -> dict[str, Any]:
    """Save an avatar and report what quality was actually obtained."""
    meta = instagram_avatar(user_id, browser)
    path = save_instagram_avatar(user_id, browser)
    return {"filepath": path, "width": meta.get("width"), "hd": meta["hd"],
            "username": meta["username"]}


# --------------------------------------------------------------------------
# Instagram profiles
# --------------------------------------------------------------------------
# Instagram usernames are letters, digits, dots and underscores, up to 30. The
# pattern is deliberately strict: the name becomes a directory, so anything
# outside this set must never reach a filesystem path.
_IG_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# Paths under instagram.com that are features, not people.
_IG_RESERVED = frozenset({
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "about", "developer", "legal", "privacy", "terms", "challenge", "share",
    "s", "web", "graphql", "api", "session",
})


def instagram_profile_of(url: str) -> str | None:
    """Return the username if this is an Instagram profile URL, else None.

    Matches instagram.com/<name> and nothing deeper -- a post, reel or story
    link has a second path segment and belongs to yt-dlp.
    """
    if platform_of(url) != "instagram":
        return None
    parts = [p for p in urlparse(url.strip()).path.split("/") if p]
    if len(parts) != 1:
        return None
    name = parts[0]
    if name.lower() in _IG_RESERVED or not _IG_USERNAME.match(name) or name in (".", ".."):
        return None
    return name


def list_instagram_profile(username: str, browser: str | None = None,
                           limit: int | None = None) -> list[dict[str, Any]]:
    """Ask gallery-dl for a profile's newest posts, newest first.

    Returns one entry per post -- deduplicated, because a carousel yields one
    gallery-dl record per slide but is a single post to download.
    """
    if not config.GALLERY_DL:
        raise ValueError(
            "gallery-dl is not installed. Run: .venv/bin/pip install gallery-dl"
        )
    limit = limit or config.MAX_PROFILE_POSTS
    cmd = [
        # -J, not -j: a profile URL resolves to an intermediary "posts" queue
        # entry, and only --resolve-json follows it through to the posts.
        config.GALLERY_DL, "--resolve-json", "--quiet",
        # gallery-dl counts slides, not posts, so ask for extra and trim later.
        "--range", f"1-{limit * 3}",
        f"https://www.instagram.com/{username}/",
    ]
    if browser and browser != "none":
        cmd += ["--cookies-from-browser", browser]
    elif config.COOKIES_FROM_BROWSER:
        cmd += ["--cookies-from-browser", config.COOKIES_FROM_BROWSER]
    elif config.COOKIES_FILE:
        cmd += ["--cookies", str(config.COOKIES_FILE)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    # gallery-dl reports an auth failure on stderr and still exits 0 with a
    # valid but empty result, so the exit code alone proves nothing.
    combined = f"{proc.stderr or ''}\n{proc.stdout[:2000]}"
    if any(k in combined for k in ("401", "Unauthorized", "login required",
                                   "HttpError", "not found")):
        if "404" in combined or "not found" in combined.lower():
            raise ValueError(f"No Instagram account called @{username}.")
        raise ValueError(
            "Instagram will not list a profile without a login. Pick your "
            "browser in the Login menu, then try again."
        )
    if not proc.stdout.strip():
        detail = (proc.stderr or "no output").strip().splitlines()[-1]
        raise ValueError(f"Could not list that profile: {detail}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("gallery-dl returned output this build cannot read.") from exc

    posts: dict[str, dict[str, Any]] = {}
    for record in _walk_dicts(data):
        code = record.get("post_shortcode") or record.get("shortcode")
        if not code or code in posts:
            continue
        posts[code] = {
            "id": code,
            "title": (record.get("description") or "").strip().split("\n")[0][:90]
                     or f"Post {code}",
            "url": record.get("post_url") or f"https://www.instagram.com/p/{code}/",
            "playlist_index": None,
            "duration": None,
            "thumbnail": record.get("display_url"),
        }
        if len(posts) >= limit:
            break
    return list(posts.values())


def _walk_dicts(node: Any) -> Any:
    """Yield every dict inside gallery-dl's nested [type, url, metadata] output."""
    if isinstance(node, dict):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _has_video(entry: dict[str, Any]) -> bool:
    """Does an already-resolved entry contain anything worth downloading?

    Instagram carousels mix photos and clips, and yt-dlp lists the photos as
    playlist entries too -- they just have no video in them. Left in, they show
    up as tickable rows that can only fail with "no video formats found", so
    they are dropped at lookup time and counted instead.

    Only meaningful for eager entries. A lazy entry has no formats yet, which
    says nothing about whether it holds video, so callers must not ask.
    """
    formats = entry.get("formats") or []
    return any(f.get("vcodec") != "none" or f.get("height") for f in formats)


def _entry_target(entry: dict[str, Any], parent_url: str, index: int) -> dict[str, Any]:
    """Work out how to re-reach one entry of a playlist at download time.

    Entries arrive in two shapes. A lazy entry (YouTube playlists under
    extract_flat) is just a title and a link, so we download it by URL. An
    eager entry -- an Instagram carousel, where the whole post resolves in one
    request -- already carries its formats inline, and its "url" field is a
    signed CDN media link, not a page. Handing that back to yt-dlp later would
    download an expiring blob with no title, so those are addressed as
    "the Nth item of the parent post" instead.
    """
    if entry.get("formats") or entry.get("requested_formats"):
        return {"url": parent_url, "playlist_index": index}
    return {"url": entry.get("webpage_url") or entry.get("url"), "playlist_index": None}


def _images_or_none(url: str, browser: str | None) -> list[dict[str, Any]]:
    """Still images in a post. Never raises -- a video lookup must not fail
    because the picture side of the same post could not be read."""
    try:
        return _images.images_for(url, browser)
    except Exception:  # noqa: BLE001
        return []


def _image_only_result(url: str, platform: str,
                       pics: list[dict[str, Any]]) -> dict[str, Any]:
    """A post that holds pictures and no video, shaped like a video result so
    the UI needs no special case beyond hiding the quality controls."""
    label = {"instagram": "Instagram", "twitter": "X", "linkedin": "LinkedIn"}.get(
        platform, platform.title())
    return {
        "type": "video",
        "platform": platform,
        "images": pics,
        "id": None,
        "title": f"{label} post - {len(pics)} image{'s' if len(pics) > 1 else ''}",
        "uploader": None,
        "uploader_id": None,
        "channel": None,
        "duration": None,
        "thumbnail": pics[0]["url"],
        "view_count": None,
        "webpage_url": url,
        "available_heights": [],
        "available_bitrates": [],
    }


def fetch_info(url: str, browser: str | None = None) -> dict[str, Any]:
    """Look up a link without downloading. Returns a video or a playlist."""
    url = url.strip()
    platform = platform_of(url)

    # yt-dlp cannot list an Instagram profile, so this branch never reaches it.
    username = instagram_profile_of(url)
    if username:
        entries = list_instagram_profile(username, browser)
        if not entries:
            raise ValueError(f"No posts found on @{username}.")
        return {
            "type": "playlist",
            "platform": platform,
            "profile": username,
            "skipped_photos": 0,
            "title": f"@{username}",
            "uploader": username,
            "count": len(entries),
            "webpage_url": url,
            "entries": entries,
        }

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        # Flatten playlist entries: pulls titles/ids only instead of doing a
        # full network round-trip per video. A 200-item playlist resolves in
        # about a second this way rather than several minutes.
        "extract_flat": "in_playlist",
    }
    opts.update(_cookie_opts(browser))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        # yt-dlp refuses a post with no video in it. That is not a dead end
        # any more -- a photo post is still downloadable, just not by yt-dlp,
        # so ask the image side before giving up.
        pics = _images_or_none(url, browser)
        if not pics:
            raise
        return _image_only_result(url, platform, pics)

    parent_url = info.get("webpage_url") or url

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ValueError("That link has no downloadable videos.")
        out_entries = []
        skipped = 0
        for i, e in enumerate(entries, start=1):
            # Eager entries carry their formats, so photos can be spotted now.
            # The index still counts them -- it has to match yt-dlp's own
            # numbering, which includes photos, or playlist_items selects the
            # wrong item.
            if (e.get("formats") is not None) and not _has_video(e):
                skipped += 1
                continue
            target = _entry_target(e, parent_url, i)
            if not target["url"]:
                continue
            out_entries.append({
                "id": e.get("id"),
                "title": e.get("title") or f"{info.get('title') or 'Item'} #{i}",
                "url": target["url"],
                "playlist_index": target["playlist_index"],
                "duration": e.get("duration"),
                "thumbnail": _thumb(e),
            })
        if not out_entries:
            pics = _images_or_none(parent_url, browser)
            if pics:
                # Photo-only carousel: still worth showing, just with nothing
                # for the video pipeline to do.
                return {
                    "type": "video", "platform": platform, "images": pics,
                    "id": info.get("id"), "title": info.get("title"),
                    "uploader": info.get("uploader") or info.get("channel"),
                    "uploader_id": None, "channel": info.get("channel"),
                    "duration": None, "thumbnail": _thumb(info),
                    "view_count": None, "webpage_url": parent_url,
                    "available_heights": [], "available_bitrates": [],
                }
            raise ValueError("That link has nothing to download.")
        return {
            "type": "playlist",
            "platform": platform,
            # The photos filtered out of the video list are downloadable now.
            "images": _images_or_none(parent_url, browser),
            "skipped_photos": skipped,
            "title": info.get("title") or "Playlist",
            "uploader": info.get("uploader") or info.get("channel"),
            "count": len(out_entries),
            "webpage_url": parent_url,
            "entries": out_entries,
        }

    formats = info.get("formats") or []
    heights = sorted(
        {f["height"] for f in formats
         if f.get("height") and f.get("vcodec") != "none"},
        reverse=True,
    )
    # Only worth offering when there are no heights to offer instead, and only
    # when more than one exists -- a lone bitrate is not a choice.
    bitrates: list[int] = []
    if not heights:
        # Round up: a filter of tbr<=?873 would exclude the 873.239 format it
        # was derived from.
        bitrates = sorted({math.ceil(f["tbr"]) for f in formats
                           if f.get("tbr") and f.get("vcodec") != "none"}, reverse=True)
        if len(bitrates) < 2:
            bitrates = []
    return {
        "type": "video",
        "platform": platform,
        "images": _images_or_none(parent_url, browser),
        "available_bitrates": bitrates,
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        # Numeric account id, the only handle Instagram's avatar endpoint takes.
        "uploader_id": str(info["uploader_id"]) if str(
            info.get("uploader_id") or "").isdigit() else None,
        "channel": info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": _thumb(info),
        "view_count": info.get("view_count"),
        "webpage_url": parent_url,
        "available_heights": heights,
    }


# yt-dlp appends the same support boilerplate to most errors. It is written for
# someone filing a bug report, not someone who just pasted a link.
_NOISE = re.compile(
    r"\s*;?\s*(?:please report this issue on\s+\S+.*?issue template\.?"
    r"|Confirm you are on the latest version using\s+yt-dlp\s+-U"
    r"|See\s+\S*wiki/FAQ\S*\s+for how to manually pass cookies\.?"
    r"|Otherwise, if the post is accessible in browser without being logged-in,.*?issues\?q=\s*,?)",
    re.I | re.S,
)


def friendly_error(raw: str, url: str, browser: str | None = None) -> str:
    """Turn a yt-dlp failure into something a person can act on.

    yt-dlp errors are written for a terminal: several sentences, a wiki link
    and a "report this issue" nudge, all on one line. Dropped into a job row
    verbatim they are unreadable, and the sentence that matters is buried.

    Recognised cases get replaced outright. Anything else keeps yt-dlp's own
    wording -- it is the only clue left when something genuinely unexpected
    breaks -- with the boilerplate trimmed off.
    """
    text = " ".join(raw.replace("\033[0;31m", "").split())
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[^:]{1,40}:\s*", "", text)  # "[LinkedIn] 7501...: "
    low = text.lower()

    site = platform_of(url)
    label = {"instagram": "Instagram", "twitter": "X", "linkedin": "LinkedIn"}.get(
        site, site.title() if site != "other" else "This site"
    )

    if any(k in low for k in ("empty media response", "login required", "you must be logged in",
                              "--cookies", "checkpoint", "sign in to confirm",
                              "requested content is not available")):
        if browser and browser != "none":
            return (f"{label} rejected the session from {browser.title()}. Open {label} "
                    f"in {browser.title()}, confirm you are logged in, then try again.")
        return (f"Could not read that {label} link. Check the link opens in your browser; "
                f"if it is a private or restricted post, pick your browser in the Login menu.")

    # LinkedIn scrapes a single <video> tag, so this fires on text, image and
    # document posts -- far more often than on a genuine extractor break.
    if "unable to extract video" in low or "no video formats found" in low \
            or "there's no video" in low:
        return (f"No video found in that {label} post. Text, image and document "
                f"posts have nothing to download.")

    if "unable to obtain file audio codec" in low or "no audio" in low:
        return "That video has no audio track, so there is nothing to extract."

    if "private" in low and "video" in low:
        return f"That {label} post is private."
    if any(k in low for k in ("not available", "has been removed", "does not exist",
                              "unavailable", "404")):
        return f"That {label} post is unavailable -- it may have been deleted."
    if "geo" in low and ("restrict" in low or "block" in low):
        return f"{label} does not serve that video in your region."
    if "rate-limit" in low or "too many requests" in low or "429" in low:
        return (f"{label} is rate-limiting this machine. Wait a few minutes, or pick "
                f"your browser in the Login menu.")

    return _NOISE.sub("", text).strip(" ;,.") or "Download failed."


def start_download(url: str, quality: str, audio_only: bool,
                   title: str | None = None, compatible: bool = True,
                   browser: str | None = None, playlist_index: int | None = None,
                   folder: str | None = None) -> str:
    """Kick off a download on a worker thread and return its job id."""
    job_id = uuid.uuid4().hex[:12]
    platform = platform_of(url)
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "url": url,
            "title": title or url,
            "platform": platform,
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
        target=_run,
        args=(job_id, url, quality, audio_only, compatible, platform, browser,
              playlist_index, folder),
        daemon=True,
    ).start()
    return job_id


def _finished_file(info: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pull the written path and title out of a finished extract_info result.

    Selecting one item of a post with playlist_items still returns a playlist
    dict, so the download details sit one level down under "entries" rather
    than at the top -- reading only the top level loses the path on every
    Instagram carousel item.
    """
    node = info
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            return None, info.get("title")
        node = entries[0]
    downloads = node.get("requested_downloads") or [{}]
    return downloads[0].get("filepath"), node.get("title") or info.get("title")


def _move_out_of_staging(path_str: str, job_id: str) -> str:
    """Move a finished audio file into place, keeping the folder structure the
    output template built under the job's staging directory."""
    staging = config.TEMP_DIR / job_id
    src = Path(path_str)
    try:
        relative = src.relative_to(staging)
    except ValueError:
        return path_str  # not staged after all; leave it alone
    dest = config.DOWNLOAD_DIR / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    return str(dest)


def _run(job_id: str, url: str, quality: str, audio_only: bool, compatible: bool,
         platform: str = "youtube", browser: str | None = None,
         playlist_index: int | None = None, folder: str | None = None) -> None:
    with _slots:  # blocks here while MAX_CONCURRENT downloads are already active
        try:
            _update(job_id, state="starting")
            opts = _ydl_opts(job_id, quality, audio_only, compatible, platform,
                             browser, playlist_index, folder)
            if playlist_index is not None:
                # Addressing one item of a carousel: the post *is* the
                # playlist, so noplaylist would throw away the thing we are
                # pointing at.
                opts["noplaylist"] = False
                opts["playlist_items"] = str(playlist_index)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            path, title = _finished_file(info)
            if audio_only and path:
                path = _move_out_of_staging(path, job_id)
            _update(job_id, state="done", percent=100.0, filepath=path,
                    title=title or url, speed=None, eta=None)
        except NotImplementedError as exc:
            _update(job_id, state="error", error=str(exc))
        except yt_dlp.utils.DownloadError as exc:
            _update(job_id, state="error", error=friendly_error(str(exc), url, browser))
        except Exception as exc:  # noqa: BLE001 - surface anything else to the UI
            _update(job_id, state="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            # A failed extraction leaves its half-finished source behind, so
            # the staging directory is cleared whether the job worked or not.
            if audio_only:
                shutil.rmtree(config.TEMP_DIR / job_id, ignore_errors=True)


def register_saved(title: str, filepath: str, platform: str,
                   kind: str = "image") -> str:
    """Record an already-finished save as a job.

    Images and avatars are fetched directly rather than queued through yt-dlp,
    but they still belong in the Downloads list -- it is where someone looks
    for what they just saved, and where the button to undo it lives.
    """
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "url": "", "title": title, "platform": platform,
            "kind": kind, "quality": None, "state": "done", "percent": 100.0,
            "downloaded": 0, "total": None, "speed": None, "eta": None,
            "filepath": filepath, "error": None,
        }
    return job_id


def forget_job(job_id: str, delete_file: bool = True) -> bool:
    """Drop a job from the list, optionally deleting what it wrote.

    Only paths inside the download folder are removed. A job's filepath comes
    from yt-dlp rather than from the browser, but this is the one place that
    deletes, so it re-checks rather than trusting the caller.
    """
    with _lock:
        job = _jobs.pop(job_id, None)
    if not job:
        return False
    path_str = job.get("filepath")
    if delete_file and path_str:
        try:
            path = Path(path_str).resolve()
            root = config.DOWNLOAD_DIR.resolve()
            if path.is_relative_to(root) and path.is_file():
                path.unlink()
                # Tidy up a carousel folder once its last file is gone.
                for parent in path.parents:
                    if parent == root or not parent.is_relative_to(root):
                        break
                    if any(parent.iterdir()):
                        break
                    parent.rmdir()
        except OSError:
            pass  # already gone, or not ours to remove
    return True


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def all_jobs() -> list[dict[str, Any]]:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def start_batch(items: list[dict[str, Any]], quality: str, audio_only: bool,
                compatible: bool = True, browser: str | None = None,
                folder: str | None = None) -> list[str]:
    """Queue several videos at once. Returns one job id per item.

    Every job is started immediately, but the semaphore in _run means only
    MAX_CONCURRENT are actually fetching at any moment -- the rest sit in
    'queued' until a slot frees up.
    """
    return [
        start_download(it["url"], quality, audio_only, it.get("title"), compatible,
                       browser, it.get("playlist_index"), folder)
        for it in items
        if it.get("url")
    ]

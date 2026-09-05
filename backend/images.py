"""Still images from social posts.

yt-dlp is a video downloader: it discards anything without a video stream, so
photo posts and the photo half of a carousel never reach it. Each site keeps
its pictures somewhere different, so this module holds one extractor per site
and a dispatcher, mirroring the shape of downloader.platform_of.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import urlencode_postdata

from . import config

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")


def _ydl(browser: str | None = None) -> yt_dlp.YoutubeDL:
    from . import downloader
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True}
    opts.update(downloader._cookie_opts(browser))
    return yt_dlp.YoutubeDL(opts)


def _get(ydl: yt_dlp.YoutubeDL, url: str, headers: dict[str, str] | None = None,
         data: bytes | None = None, impersonate: bool = False) -> bytes:
    ext = {"impersonate": ImpersonateTarget("chrome")} if impersonate else {}
    req = Request(url, headers={"User-Agent": _UA, **(headers or {})},
                  data=data, extensions=ext)
    return ydl.urlopen(req).read()


# --------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------
# Instagram serves post data to logged-out clients through one deliberate
# GraphQL path -- the response field is literally named
# "if_not_gated_logged_out" -- but only to something that looks like a real
# browser all the way down to the TLS handshake. Without curl_cffi installed
# the same request comes back as the 600 KB app shell, so impersonation is not
# optional here.
_IG_DOC_ID = "27130156389949648"
_IG_QUERY = "PolarisLoggedOutDesktopWWWPostRootContentQuery"


# Every resized variant has an sNNNxNNN or pNNNxNNN token in its path; the
# untouched upload is the one without. Instagram lists it first, but that is
# not promised anywhere, so it is identified by the missing token rather than
# by position, and the largest labelled size is the fallback.
_IG_SIZE_TOKEN = re.compile(r"[sp](\d+)x(\d+)")


def _ig_original(candidates: list[dict[str, Any]]) -> str:
    for candidate in candidates:
        if not _IG_SIZE_TOKEN.search(candidate["url"]):
            return candidate["url"]
    return max(candidates,
               key=lambda c: max((int(w) * int(h) for w, h in
                                  _IG_SIZE_TOKEN.findall(c["url"])), default=0))["url"]


def instagram_images(shortcode: str, browser: str | None = None) -> list[dict[str, Any]]:
    from yt_dlp.extractor.instagram import InstagramIE, _id_to_pk

    ydl = _ydl(browser)
    ie = InstagramIE(ydl)
    ie.initialize()
    lsd = ie._lsd_token
    headers = {
        **ie._api_headers,
        "X-FB-Friendly-Name": _IG_QUERY,
        "X-FB-LSD": lsd,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/p/{shortcode}/",
    }
    body = urlencode_postdata({
        "lsd": lsd,
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": _IG_QUERY,
        "server_timestamps": "true",
        "variables": json.dumps({"media_id": _id_to_pk(shortcode)}, separators=(",", ":")),
        "doc_id": _IG_DOC_ID,
    })
    raw = _get(ydl, "https://www.instagram.com/api/graphql", headers, body, impersonate=True)
    if not raw.startswith(b"{"):
        raise ValueError(
            "Instagram returned its web page instead of data. This needs "
            "curl_cffi installed: .venv/bin/pip install curl_cffi"
        )
    media = (json.loads(raw).get("data") or {}).get("xig_polaris_media") or {}
    media = media.get("if_not_gated_logged_out") or {}
    if not media:
        raise ValueError("Instagram returned no media for that post.")

    # media_type: 1 photo, 2 video, 8 carousel. A carousel's children carry
    # their own type, so one post can be a mix of both.
    children = media.get("carousel_media") or [media]
    out = []
    for i, child in enumerate(children, start=1):
        if child.get("media_type") == 2:
            continue  # a video; downloader.py already handles those
        candidates = (child.get("image_versions2") or {}).get("candidates") or []
        if not candidates:
            continue
        out.append({
            "url": _ig_original(candidates),
            # The candidates carry no dimensions; the child records the size of
            # the upload itself, which is what _ig_original returns.
            "width": child.get("original_width"),
            "height": child.get("original_height"),
            "index": i,
        })
    return out


# --------------------------------------------------------------------------
# X / Twitter
# --------------------------------------------------------------------------
# The syndication endpoint that powers embedded tweets answers without any
# auth and lists a post's photos. The token parameter is not validated.
def twitter_images(tweet_id: str, browser: str | None = None) -> list[dict[str, Any]]:
    ydl = _ydl(browser)
    url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=a&lang=en"
    data = json.loads(_get(ydl, url))
    out = []
    for i, photo in enumerate(data.get("photos") or [], start=1):
        if photo.get("url"):
            out.append({
                # ?name=orig asks the CDN for the untouched upload rather than
                # the resized version the embed would show.
                "url": re.sub(r"\?.*$", "", photo["url"]) + "?format=jpg&name=orig",
                "width": photo.get("width"),
                "height": photo.get("height"),
                "index": i,
            })
    return out


# --------------------------------------------------------------------------
# LinkedIn
# --------------------------------------------------------------------------
# LinkedIn renders a logged-out post page with Open Graph tags intact, and the
# feedshare-image-high-res variant is the full upload.
def linkedin_images(url: str, browser: str | None = None) -> list[dict[str, Any]]:
    ydl = _ydl(browser)
    page = _get(ydl, url).decode("utf8", "replace")
    urls: list[str] = []
    for match in re.finditer(r'property="og:image"\s+content="([^"]+)"', page):
        # The URL is signed and carries &e=/&v=/&t= parameters. In the markup
        # those ampersands are &amp;, and passing them through verbatim breaks
        # the signature -- LinkedIn answers 403 deny-missing-version.
        urls.append(html.unescape(match.group(1)))
    if not urls:
        urls = [html.unescape(u) for u in re.findall(
            r'(https://media\.licdn\.com/dms/image/[^"\s]*feedshare[^"\s]*)', page)]
    seen, out = set(), []
    for i, u in enumerate(dict.fromkeys(urls), start=1):
        # Profile photos and site chrome are not the post's content.
        if "profile-displayphoto" in u or "company-logo" in u or u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "width": None, "height": None, "index": i})
    return out


# --------------------------------------------------------------------------
# Dispatch and saving
# --------------------------------------------------------------------------
_IG_SHORTCODE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:p|tv|reels?)/([^/?#&]+)", re.I)
_TWEET_ID = re.compile(r"(?:twitter|x)\.com/(?:i/web|[^/]+)/status(?:es)?/(\d+)", re.I)


def images_for(url: str, browser: str | None = None) -> list[dict[str, Any]]:
    """Every still image in a post. Empty list when there are none."""
    from . import downloader

    platform = downloader.platform_of(url)
    if platform == "instagram":
        m = _IG_SHORTCODE.search(url)
        return instagram_images(m.group(1), browser) if m else []
    if platform == "twitter":
        m = _TWEET_ID.search(url)
        return twitter_images(m.group(1), browser) if m else []
    if platform == "linkedin":
        return linkedin_images(url, browser)
    return []


_MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"RIFF": ".webp", b"GIF8": ".gif"}

_LI_POST_ID = re.compile(r"-(\d{10,})-\w{4}/?(?:[?#]|$)|urn:li:activity:(\d+)")


def post_id_of(url: str) -> str | None:
    """The site's own identifier for a post, used to name its folder."""
    from . import downloader

    platform = downloader.platform_of(url)
    if platform == "instagram":
        m = _IG_SHORTCODE.search(url)
        return m.group(1) if m else None
    if platform == "twitter":
        m = _TWEET_ID.search(url)
        return m.group(1) if m else None
    if platform == "linkedin":
        m = _LI_POST_ID.search(url)
        return (m.group(1) or m.group(2)) if m else None
    return None


def folder_for_post(url: str, title: str | None) -> str:
    """Folder name for a multi-image post, matching how carousel videos are
    named by yt-dlp -- "<title> [<id>]" -- so both halves of one carousel end
    up looking like they belong together."""
    stem = _safe_name(title or "Post") or "Post"
    post_id = post_id_of(url)
    return f"{stem[:80]} [{post_id}]" if post_id else stem[:100]


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", text)[:100].strip()


def save_images(images: list[dict[str, Any]], stem: str,
                folder: str | list[str] | None = None,
                browser: str | None = None) -> list[str]:
    """Write images to the download folder. Returns the paths written.

    `folder` may be several nested names -- a profile download of a carousel
    wants the post's folder inside the account's folder, not beside it.
    """
    from . import downloader

    ydl = _ydl(browser)
    target = config.DOWNLOAD_DIR
    segments = [folder] if isinstance(folder, str) else (folder or [])
    for segment in segments:
        # Each name is checked on its own: the guard rejects a slash, so a
        # path is only ever built from names that individually passed it.
        if not downloader._SAFE_FOLDER.match(segment):
            raise ValueError(f"Unsafe folder name: {segment!r}")
        target = target / segment
    target.mkdir(parents=True, exist_ok=True)

    safe = _safe_name(stem) or "image"
    multiple = len(images) > 1
    paths = []
    for image in images:
        data = _get(ydl, image["url"])
        ext = next((e for magic, e in _MAGIC.items() if data.startswith(magic)), None)
        if not ext:
            continue  # an error page, not a picture
        # Numbered first so the folder sorts in post order, like the videos.
        name = f"{image['index']:02d} - {safe}{ext}" if multiple else f"{safe}{ext}"
        path = target / name
        path.write_bytes(data)
        paths.append(str(path))
    if not paths:
        raise ValueError("None of those links returned an image.")
    return paths

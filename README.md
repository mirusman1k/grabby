# Grabby

A small desktop app for macOS that saves videos to your own machine, built on
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg.

Everything runs locally. Nothing is uploaded anywhere, there is no account, and
the server listens on `127.0.0.1` only.

![Grabby](screenshot.png)

## What it does

- Paste a link, see the title, channel, length and thumbnail before committing
- Pick a quality from the resolutions that video actually offers
- **Playlists** — get a checklist of every video, untick the ones you don't want
- Three downloads run at once; the rest queue automatically
- Audio-only mode extracts an mp3
- Live progress with speed and time remaining
- Files are named `Title [videoid].mp4`, so nothing silently overwrites

## Requirements

- macOS 11 or later (Apple Silicon or Intel)
- Python 3.10+
- [ffmpeg](https://ffmpeg.org) — `brew install ffmpeg`

ffmpeg is not optional: YouTube serves video and audio as separate streams for
anything above 720p, and they have to be merged.

## Install

```bash
git clone https://github.com/YOUR-USERNAME/grabby.git
cd grabby
./build/make_app.sh
```

That puts **Grabby.app** in your Applications folder. Launchpad, Spotlight and
the Dock all find it, and it opens in its own window — no Terminal, no browser
tab. First launch sets up a virtualenv automatically.

The app records the path it was built from, so **re-run `./build/make_app.sh`
if you ever move the folder.**

### Prefer a browser tab?

```bash
./run.sh
```

Serves at <http://127.0.0.1:8000> and opens it for you.

### Always-on

```bash
./install-autostart.sh
```

Runs the server quietly from every login, so the address always works. Undo
with `./uninstall-autostart.sh`.

## A note on quality and playability

By default Grabby asks for **H.264 video with AAC audio**, because that is what
QuickTime, Photos, iMovie and iOS can actually decode. YouTube's highest-quality
streams use VP9 or AV1 with Opus audio — better compression, but they produce an
`.mp4` that no Apple player will open.

H.264 stops at 1080p on YouTube. To go higher, tick **Max quality (needs VLC)**
and play the result in [VLC](https://www.videolan.org) or
[IINA](https://iina.io).

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `DOWNLOAD_DIR` | `~/Downloads/yt-downloader` | Where files land |
| `PORT` | `8000` | Server port |
| `MAX_CONCURRENT` | `3` | Simultaneous downloads |

```bash
DOWNLOAD_DIR=~/Movies MAX_CONCURRENT=5 ./run.sh
```

## How it works

| Path | Role |
|---|---|
| `backend/app.py` | HTTP routes, host guard, serves the UI |
| `backend/downloader.py` | yt-dlp wrapper, worker threads, progress state |
| `backend/config.py` | Paths, port, concurrency, ffmpeg discovery |
| `frontend/` | Single-page UI — no build step, no framework |
| `desktop.py` | Native-window entry point used by the app bundle |
| `build/` | Icon generator and `.app` bundler |

A download runs on a background thread. yt-dlp calls a progress hook several
times a second, the hook writes into an in-memory job dict, and the UI polls
`GET /api/jobs` once a second — then stops polling once nothing is active.
A `threading.Semaphore` caps concurrency, so extra jobs simply wait their turn.
Each video is its own job with its own error handling, so one dead link in a
playlist of 90 doesn't take the other 89 down with it.

Playlists are read with `extract_flat`, which pulls titles and ids in a single
request instead of one round-trip per video — an 87-item playlist lists in about
a second.

### Security

The server binds to loopback and rejects any request whose `Host` header isn't
a loopback name, which blocks DNS-rebinding attempts from web pages you visit.
Don't change `HOST` to `0.0.0.0` — that would let anyone on your network queue
downloads and write files to your disk.

## Legal

Downloading videos is against YouTube's Terms of Service, and redistributing
copyrighted material is infringement. This tool exists for your own uploads,
Creative Commons and public-domain material, and content you otherwise hold the
rights to keep offline. What you do with it is your responsibility.

## License

MIT — see [LICENSE](LICENSE).

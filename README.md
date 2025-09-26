# Accessible IPTV Client

A vibe coded, accessible, keyboard-first IPTV player that works well with screen readers. Runs on Windows and Linux. — Designed for everyday use: simple setup, fast search, and optional EPG (TV guide) so you can see what’s on now and next.

## What It Can Do

- Works with popular screen readers: NVDA, JAWS, Narrator, and Orca
- Opens standard M3U and M3U Plus playlists, and also supports Stalker Portal and XtreamCodes.
- Lets you add or remove playlist sources at any time
- Groups channels by category for quicker browsing
- Built‑in search across channel names (and EPG when available)
- Plays streams in your preferred external media player (VLC, MPC‑HC, MPV, etc.)
- Optional XMLTV EPG support (direct .xml or compressed .xml.gz)

## What You Need

- Python 3.8 or newer
- wxPython (install with: `pip install wxpython`)
- At least one playlist URL or file
- Optional: one or more XMLTV EPG sources (`.xml` or `.xml.gz`)

## The Quickest Way

- Windows or Linux: download a ready‑to‑run build from the project’s releases page (GitHub). If you prefer to run from source or make changes, follow the steps below.

## Run From Source (Windows or Linux)

1) Install Python if you don’t already have it.

2) Open a terminal and run:

   - `pip3 install --upgrade pip`
   - `pip3 install wxpython`

3) Clone this repository and switch into the project folder. Make sure your terminal is in that folder, then start the app:

   - `python3 ./main.py`

## Build A Standalone App (Windows or Linux)

If you want a single executable you can copy around:

1) From the project folder, install the bundler and build:

   - `pip install pyinstaller`
   - `pyinstaller --noconsole --onefile main.py`

2) Your executable will be in the `./dist` folder when the build finishes.

## First‑Time Setup In The App

- Add your playlist(s): use the Playlist Manager to add a URL or a local M3U file.
- Optional: add EPG source(s) in the EPG Manager. You can paste an XMLTV URL or choose a local `.xml`/`.xml.gz` file.
- Choose your external player (VLC is a good default), then pick a channel and press Enter to play.

## Helpful Tips

- Searching: type in the search box to filter channel names. The app will also look up matching shows in the EPG and append them to your results without jumping your list position.
- EPG downloads: `.xml.gz` guides are handled reliably (downloaded to a temp file, automatically verified, and then cleaned up). If a server sends an incomplete file, the app retries automatically.
- Logs (for troubleshooting): a detailed log is written to your system’s temp folder as `iptvclient_epg_debug.log` while EPG is importing. This does not affect normal playback.

## Platform Notes

- Windows and Linux are supported.
- macOS is not supported at this time.

That’s it — add a playlist, optionally add an EPG, and enjoy.

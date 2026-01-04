# Accessible IPTV Client

Accessible IPTV Client is an accessible, keyboard-first IPTV player that works well with screen readers. It runs on Windows and Linux and supports playlists, EPG, catch-up, and casting.

## Features

- Screen reader friendly (NVDA, JAWS, Narrator, Orca)
- M3U / M3U+ playlists, Xtream Codes, and Stalker Portal sources
- Built-in player (libVLC via python-vlc) or external player support (VLC, MPC-HC, MPV, etc.)
- Channel groups, fast search, and EPG search
- XMLTV EPG support (`.xml` and `.xml.gz`)
- Catch-up/timeshift playback for supported channels
- Optional system tray minimize
- Casting support

## Quick Start (Recommended)

1. Download the latest release build from GitHub Releases.
2. Unzip it somewhere like `C:\Apps\AccessibleIPTVClient\`.
3. Run `IPTVClient.exe`.
4. Add a playlist: **Ctrl+M** (Playlist Manager).
5. Optional: add EPG sources: **Ctrl+E** (EPG Manager), then **Ctrl+I** to import.
6. Pick a channel and press **Enter** to play.

## Requirements (Run From Source)

- Python **3.11+**
- `pip` packages from `requirements.txt`
- If you want the built-in player: **VLC 3.0+ installed** (python-vlc loads libVLC from your VLC install)

### Install + Run (Windows or Linux)

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

## Keyboard Shortcuts

### Main Window

- **Ctrl+M** - Playlist Manager
- **Ctrl+E** - EPG Manager
- **Ctrl+I** - Import EPG to database
- **Ctrl+Q** - Exit
- **Enter** - Play selected channel
- **Context Menu / Apps Key** - Channel options (including Catch-up if available)

### Built-in Player

- **Space** - Play/Pause
- **Up / Down** - Adjust volume (2% steps)
- **Ctrl+Up / Ctrl+Down** - Adjust volume (5% steps)
- **F11** - Toggle fullscreen
- **Escape** - Exit fullscreen
- **Tab** - Navigate between controls

## EPG Notes

- During EPG import, a detailed log is written to your temp directory as `iptvclient_epg_debug.log`.
- `.xml.gz` guides are supported and are handled via a safe download/verify workflow.

## Built-in Player Buffering

The internal player sizes its network buffer dynamically. You can tune it in `iptvclient.conf`:

- `internal_player_buffer_seconds` (default ~2s)
- `internal_player_max_buffer_seconds` (default ~18s)
- `internal_player_variant_max_mbps` (HLS quality cap in Mbps, 0 = no cap)

Lower values start faster; higher values are more tolerant of jitter.

## Build a Standalone App

### Windows

Run:

```bat
build.bat build
```

Output:

- App folder: `dist\iptvclient`
- Release assets: `dist\release\`

### Linux (or manual Windows build)

```bash
python -m pip install pyinstaller
pyinstaller --clean main.spec
```

## Release + Auto-Update (Windows)

### Release pipeline

- `build.bat dry-run` - show the next version bump and release notes
- `build.bat release` - bump version, build, sign, zip, tag, push, and create a GitHub Release

Prerequisites:

- `gh` CLI authenticated (`gh auth login`)
- Code signing certificate installed and `signtool.exe` available (or set `SIGNTOOL_PATH`)

### Auto-update behavior

- Updates are supported on **Windows packaged builds** (not source runs).
- Releases include a zip plus a manifest (`AccessibleIPTVClient-update.json`) with SHA-256 + signing thumbprint.

## Manual Updater Test (Recommended)

1. Install/unzip an older build into a test folder.
2. Create a new release with `build.bat release`.
3. Launch the older build and use Options -> Check for Updates.
4. Accept the update, then confirm the app restarts at the new version and a backup folder remains next to the install folder.

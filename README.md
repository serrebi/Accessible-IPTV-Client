# Accessible IPTV Client

A vibe coded, accessible, keyboard-first IPTV player that works well with screen readers. Runs on Windows and Linux. Designed for everyday use: simple setup, fast search, EPG, and casting support.

## What It Can Do

- Works with popular screen readers: NVDA, JAWS, Narrator, and Orca
- Opens standard M3U and M3U Plus playlists, and also supports Stalker Portal and XtreamCodes
- Lets you add or remove playlist sources at any time
- Groups channels by category for quicker browsing
- Built-in search across channel names (and EPG when available)
- Plays streams in the built-in IPTV player with adaptive buffering, or hand off to your preferred external media player (VLC, MPC-HC, MPV, etc.)
- Supports casting.
- Catch-up/timeshift playback for channels that support it
- XMLTV EPG support (direct .xml or compressed .xml.gz)
- Minimize to system tray option

## What You Need

- Python 3.8 or newer
- wxPython (install with: `pip install wxpython`)
- python-vlc (install with: `pip install python-vlc`) if you want to use the built-in player
- VLC media player 3.0+ installed on the system so libVLC is available to python-vlc (the client will download a matching portable build, or fall back to `winget`, if it cannot find one)
- At least one playlist URL or file
- Optional: one or more XMLTV EPG sources (`.xml` or `.xml.gz`)

## The Quickest Way

- Windows or Linux: download a ready-to-run build from the project's releases page (GitHub). If you prefer to run from source or make changes, follow the steps below.

## Run From Source (Windows or Linux)

1. Install Python if you don't already have it.

2. Open a terminal and run:

   - `pip3 install --upgrade pip`
   - `pip3 install -r requirements.txt`

3. Clone this repository and switch into the project folder. Make sure your terminal is in that folder, then start the app:

   - `python3 ./main.py`

## Build A Standalone App (Windows or Linux)

If you want a standalone build you can distribute:

### Windows
1. Run `build.bat build` (or `build_exe.bat build`) in the project folder. This builds using `main.spec`, signs the exe, and creates a zip in `dist\release`.
2. Your build folder will be in `dist\iptvclient`.

### Linux (or Manual Windows Build)
1. From the project folder, install the bundler and build:

   - `pip install pyinstaller`
   - `pyinstaller --clean main.spec`

2. Your build folder (containing the executable and all dependencies) will be in the `dist\iptvclient` folder when the build finishes. You should distribute the entire folder.

## Release Pipeline (Windows)

Prerequisites:
- Python 3.x with PyInstaller available (`pip install pyinstaller`)
- `gh` CLI authenticated (`gh auth login`)
- Code signing certificate installed
- `signtool.exe` available (default path is used, or set `SIGNTOOL_PATH`)

Commands:
- `build.bat build` - build + sign + zip locally (no git tag/release)
- `build.bat release` - compute next version, build, sign, zip, tag, push, and create a GitHub release
- `build.bat dry-run` - show what would happen without changing git or creating a release

Versioning rules:
- Latest tag is parsed as semver (vMAJOR.MINOR[.PATCH], missing patch defaults to 0).
- BREAKING CHANGE or `!:` in commits bumps MAJOR.
- `feat` or `feature` in commits bumps MINOR.
- Otherwise, PATCH is bumped.
- If no prior semver tag exists, base version starts at `v1.4.2`.

## Auto-Update (Windows)

- The app checks GitHub Releases for updates (default auto-check on startup).
- Manual check: Options menu -> Check for Updates.
- Release assets include:
  - `AccessibleIPTVClient-vX.Y.Z.zip` (full build)
  - `AccessibleIPTVClient-update.json` (manifest with SHA-256 and download URL)
- Security checks:
  - SHA-256 verification of the downloaded zip
  - Authenticode verification of the new exe before install
- Updates install via a staging folder, create a backup, and restart the app.

## First-Time Setup In The App

- Add your playlist(s): use the Playlist Manager to add a URL or a local M3U file.
- Optional: add EPG source(s) in the EPG Manager. You can paste an XMLTV URL or choose a local `.xml`/`.xml.gz` file.
- Choose your media player (Built-in Player is a good default), then pick a channel and press Enter to play.

## Keyboard Shortcuts

### Main Window
- **Ctrl+M** - Open Playlist Manager
- **Ctrl+E** - Open EPG Manager
- **Ctrl+I** - Import EPG to database
- **Ctrl+Q** - Exit
- **Enter** - Play selected channel
- **Context Menu / Apps Key** - Show channel options (including Catch-up if available)

### Built-in Player
- **Space** - Play/Pause
- **Ctrl+Up / Ctrl+Down** - Adjust volume (5% steps)
- **F11** - Toggle fullscreen
- **Escape** - Exit fullscreen
- **Tab** - Navigate between controls

## Helpful Tips

- Searching: type in the search box to filter channel names. The app will also look up matching shows in the EPG and append them to your results without jumping your list position.
- EPG downloads: `.xml.gz` guides are handled reliably (downloaded to a temp file, automatically verified, and then cleaned up). If a server sends an incomplete file, the app retries automatically.
- Logs (for troubleshooting): a detailed log is written to your system's temp folder as `iptvclient_epg_debug.log` while EPG is importing. This does not affect normal playback.
- Need an external player? Pick it in the Options menu and the app will auto-install it via winget (Windows), pkexec/apt-get (Debian/Ubuntu), or Homebrew when it can; otherwise you'll get a gentle reminder to install it manually.

## Built-in Player Buffering

The internal player is powered by libVLC and sizes its network buffer dynamically, aiming for uninterrupted playback on links that range from about 1 Mbps up to gigabit broadband speeds. You can raise or lower the baseline buffer (default 2 seconds) by editing the `internal_player_buffer_seconds` field in `iptvclient.conf`, and you can lift the ceiling for how deep the cache is allowed to grow via `internal_player_max_buffer_seconds` (default 18 seconds). Lower values reduce startup latency, while higher values add extra headroom during shaky periods - values around 8-12 seconds are a good compromise for slower links. If your provider exposes an HLS master playlist you can also cap the quality by setting `internal_player_variant_max_mbps` to the highest bitrate (in Mbps) you want the built-in player to select; keeping this around 2-3 Mbps often smooths playback on constrained connections. Make sure the desktop VLC player is installed using the same architecture as Python (32-bit vs 64-bit) so python-vlc can load libVLC. On Windows, if a compatible libVLC build is not found the client will first download a matching portable VLC bundle under your profile, then fall back to `winget` if available.

## Configuration File

The app stores settings in `iptvclient.conf` (JSON format). Key options:

- `playlists` - List of playlist URLs or file paths
- `epgs` - List of EPG source URLs or file paths
- `media_player` - Selected player ("Built-in Player", "VLC", "MPV", etc.)
- `internal_player_buffer_seconds` - Baseline buffer for built-in player
- `internal_player_max_buffer_seconds` - Maximum buffer ceiling
- `internal_player_variant_max_mbps` - HLS quality cap in Mbps
- `minimize_to_tray` - Whether to minimize to system tray
- `auto_check_updates` - Whether to auto-check GitHub releases on startup (Windows)
- `epg_enabled` - Whether EPG features are active

## Platform Notes

- Windows and Linux are supported.
- macOS is not supported at this time.

## Manual Test Plan (Updater)

1. Install or unzip a previous build (for example, from the v1.92 release) into a test folder.
2. Run `build.bat release` to create a new GitHub release with the latest zip + manifest.
3. Launch the older build and use Options -> Check for Updates (or wait for auto-check).
4. Accept the update and confirm the app restarts at the new version.
5. Verify the backup folder remains next to the install folder for rollback.

That's it - add a playlist, optionally add an EPG, and enjoy.

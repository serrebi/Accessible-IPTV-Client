﻿You are a professional windows python developer with a decade of IPTV app experience. Always fully investigate things before applying a fix. If you learn anything new, write it in this file.
You are root and you can install and use whatever you need to on windows with winget, powershell, chocolatey, whatever you need, or with pip3. You can use any package manager, like winget, pip3, or anything you need.
Make sure the spec file has all requirements and submodules included in the build.
## Project Overview
Project=Accessible IPTV Client (wxPython GUI) focused on playlists+EPG; main.py spins wx frame, tray icon, playlist/EPG managers, background threads (playlist load, EPG import) and uses options.py for config persistence + cache dirs.

## Architecture
- **main.py**: Main GUI frame (IPTVClient), tray icon, playlist/EPG managers, channel list, group filtering, search, external/internal player launching, catch-up dialog.
- **internal_player.py**: Built-in VLC-based player with adaptive buffering, volume slider (0-100, 5% steps via Ctrl+Up/Down), fullscreen (F11), play/pause/stop controls, and automatic reconnection for choppy streams.
- **sitecustomize.py**: Runtime patches for the internal player.
- **playlist.py**: SQLite3 WAL database (/tmp/epg.db) with channels/programmes tables, busy_timeout=20s, BEGIN IMMEDIATE + reopen-on-lock retry, commits in 10k batches, trims old rows, logs to /tmp/iptvclient_epg_debug.log, downloads XMLTV/GZip sources with resume+HTTP416 fallback.
- **providers.py**: XtreamCodesClient + StalkerPortalClient to build playlist+EPG URLs, handle auth tokens, and surface ProviderError.
- **options.py**: JSON config (portable fallback + wx StandardPaths), exposes save/load, cache path hashing, canonical naming helpers via options.load_config.

## Key Features
- Screen reader accessible (NVDA, JAWS, Narrator, Orca)
- M3U/M3U Plus playlists, Stalker Portal, XtreamCodes providers
- Built-in VLC player with adaptive buffering and auto-reconnect
- External player support (VLC, MPV, MPC-HC, custom)
- XMLTV EPG support (.xml and .xml.gz)
- Catch-up/timeshift playback for supported channels
- Channel grouping and search (including EPG search)
- System tray minimize option
- Cross-platform: Windows and Linux

## Config File (iptvclient.conf)
Key settings: playlists, epgs, media_player, custom_player_path, internal_player_buffer_seconds, internal_player_max_buffer_seconds, internal_player_variant_max_mbps, epg_enabled, minimize_to_tray.

## Dependencies
wxPython>=4.2.1 (GUI), python-vlc (built-in player), psutil optional for memory telemetry; stdlib otherwise.

## Learnings

**Update 2025-11-12**: wx.LogError uses printf-style formatting, so any literal % signs must be escaped (message.replace("%", "%%")) before calling it to avoid mangled output.

**Update 2025-11-12**: Built-in VLC player was flipping into restart/Stream Lost loops because we counted every buffering blip as "choppy" and the retry counter never decayed; now only buffering events >=1.25s count and reconnect attempts reset after ~2 minutes without retries so channels stop dropping for minor hiccups.

**Update 2025-11-12**: Xtream-style `.ts` channels now request a much deeper network/file cache (>=18s) so steady buffers soak up server jitter without extra restarts; live caching stays high but capped by the `internal_player_max_buffer_seconds` config value.

**Update 2025-11-12**: Noticed Xtream `.ts` live channels actually terminate the HTTP response every ~60 MB even though they should be endless, so libVLC reported a clean "Ended" state and stopped playback; we now tag built-in player launches as live vs catch-up and force automatic restarts for Xtream-style `.ts` live URLs so they reopen immediately after each server-side EOF or long buffering stall instead of disconnecting the viewer, and those refreshes no longer burn reconnect-attempt budget.

**Update 2025-11-23**: Volume now mirrors the Plex client: fixed 5% steps (Ctrl+Up/Down or hold) with a 0-100 slider in the control bar/tab order. Added libVLC path priming for typical 32/64-bit installs and fixed wx alignment flags so the control bar no longer asserts when the slider is added.

**Update 2025-12-01**: Volume control refined to 2% steps for finer precision (5% with Ctrl modifier) and now supports mouse wheel input with rate-limiting to prevent UI lag. Internal player buffering logic updated to remove artificial caps on high-bitrate streams, better utilizing high-speed (gigabit) connections with buffers up to 300s. Playlist loading refactored to use parallel threads for both fetching and parsing, significantly reducing startup time with multiple sources.

**Update 2025-12-01**: Refactored `CastingManager` to run a persistent background thread with its own `asyncio` loop. Previous implementation spun up ephemeral loops for each action, which caused `RuntimeError` with libraries like `pyatv` and `aiohttp` that bind objects to the loop they were created in. All cast operations now dispatch synchronously to this background loop.

**Update 2025-12-10**: Internal player buffering retuned for low-latency startup. Network caching now targets roughly 6–8 seconds for live streams (including Xtream `.ts`), so channels join faster while still maintaining at least ~6 seconds of buffered content once playback has started to avoid constant rebuffering.

**Update 2025-12-12**: Removed the `sitecustomize.py` monkey-patch that re-implemented the internal player. `sitecustomize` now just re-exports the canonical `InternalPlayerFrame` from `internal_player.py` to avoid drift and duplicated buffering logic.

**Update 2025-12-17**: Stream proxy now attempts to punch a Windows Firewall hole for its dynamic port via `netsh advfirewall` (private/domain only). If `netsh` is missing or the call fails (e.g., no admin rights), it logs a warning and keeps running so casting isn’t blocked by the new code path.

**Update 2025-12-17**: Chromecast casting now treats `application/octet-stream` (and variants) as MPEG-TS when sniffing unknown streams, forcing an HLS remux via the proxy. Many Xtream-style URLs omit `.ts` and return octet-stream, which previously left the Chromecast trying (and failing) to play raw TS.

**Update 2025-12-17**: Chromecast TS casts now force a low-latency H.264 transcode (libx264 ultrafast, 2s HLS parts, audio copy) when the proxy is used, so sinks that can’t decode HEVC still render video. Session IDs include the transcode mode so non-transcode sessions stay separate.

**Update 2025-12-17**: HLS transcoder now makes segments Chromecast-safe: yuv420p, profile high level 4.1, independent HLS segments, 2s parts, start_number=0. This should prevent “audio-only then stop” failures from mid-GOP cuts or 10-bit surfaces.

**Update 2025-12-17**: Added AAC re-encode (160 kbps, 48 kHz, stereo) to the cast transcode path so segment audio PTS/ADTS stay clean; previously we copied AAC which could create bad splits and 1s-and-stop behaviour on some sinks.

**Update 2025-12-19**: Updated `main.spec` to fully support the standalone build. Added hidden imports for `pychromecast`, `pyatv`, and `async_upnp_client` (plus their dependencies like `zeroconf`, `aiohttp`, `miniaudio`). Bundled `iptvclient.conf` and `init.mp4` in the data files. This ensures the executable works correctly with all casting features and default configurations.

**Update 2025-12-19**: Implemented a threaded producer-consumer buffer (16MB) in `stream_proxy.py` to decouple upstream reads from downstream writes. This allows the proxy to absorb a server "burst" on connect even if the client (e.g., Chromecast) requests data slowly at first, preventing stalls when the burst ends and the stream settles into real-time bitrate.

**Update 2025-12-19**: Resolved PyInstaller build warning "Hidden import 'netifaces' not found" by explicitly installing the `netifaces` package. This dependency is often used by `zeroconf` (via `pychromecast`) for network interface enumeration and is critical for reliable casting discovery.

**Update 2025-12-19**: Implemented "Bootstrap HLS" in `stream_proxy.py` to satisfy strict connection timeouts on modern hardware like Hisense U7K. The proxy now serves an instant 1-second warming segment (`bootstrap.ts`) while FFmpeg probes the upstream provider in the background. A `#EXT-X-DISCONTINUITY` tag is used to safely hand off the TV's decoder to the real IPTV segments once ready.

**Update 2025-12-19**: Developed a "Smart Audio Proxy" for radio streams. Standard 320 KBPS MP3 streams (like SerrebiRadio) use a zero-latency direct byte proxy, while formats like Opus or low-bitrate AAC (CJSR) are automatically transcoded to high-fidelity 320 KBPS MP3. This triggers the TV's native audio player UI for superior stability over the video player.

**Update 2025-12-19**: Switched to a "Python-to-FFmpeg" piped engine for HLS and Radio transcoding. By using Python's `urllib` to handle the initial handshake and piping raw data into FFmpeg's `stdin`, we bypass FFmpeg-specific SSL/TLS handshake failures and ensure all authentication headers (User-Agent, Cookies) are correctly applied to the provider.

**Update 2025-12-19**: Enhanced player accessibility for NVDA and JAWS users. Added explicit `SetName` metadata to all media controls and volume sliders. Replaced manual Tab key overrides with standard `wx.TAB_TRAVERSAL` to ensure predictable screen reader navigation. Fixed a bug where the player remained in a disabled state when shown from the system tray.

**Update 2025-12-19**: Implemented a "Total Collection" strategy in `main.spec`. The build now recursively collects all submodules, metadata, and binaries for complex networking stacks (`pychromecast`, `aiohttp`, `pyatv`, `zeroconf`, `protobuf`). Combined with a multi-stage local IP detection method (DNS route fallback to interface scan), this ensures 100% feature parity between the source code and the standalone EXE.

**Update 2025-12-20**: Stream proxy now honors `mode=audio` and pipes radio transcoding through Python -> FFmpeg so auth headers/cookies are preserved; internal player buffer defaults align with README (2s base / 18s max) and treat 0 as unset to avoid zero-buffer playback.

**Update 2025-12-20**: PyInstaller spec now explicitly bundles PyATV/UPnP dependencies (pydantic, srptools, tinytag, tabulate, defusedxml, didl_lite, voluptuous, chacha20poly1305_reuseable, requests deps) plus `iptvclient.conf` so frozen builds include all dynamic imports and default config.

**Update 2025-12-22**: Switched the PyInstaller build strategy from `--onefile` to `--onedir`. Users reported that the single-executable version failed to run on some systems. Distributing the app as a directory (folder) improves reliability, reduces startup time (no extraction needed), and makes debugging dependency issues easier. Updated `main.spec` and `README.md` to reflect this change.

**Update 2025-12-22**: Aligned all build artifacts (`main.spec`, `README.md`, `build.bat`, `build_exe.bat`) to consistently use the `--onedir` strategy with the output folder `dist\iptvclient`. Verified that all necessary binaries (`ffmpeg.exe`, `init.mp4`) and configuration files are correctly collected into the distribution folder alongside the DLLs.

**Update 2025-12-23**: Added a Windows auto-updater that pulls a GitHub release manifest, validates SHA-256 + Authenticode signatures, stages updates with rollback, and restarts via a helper script.

**Update 2025-12-28**: Updater now accepts a pinned Authenticode thumbprint from the release manifest (`signing_thumbprint`) so self-signed certs can pass verification when Windows reports UnknownError.

**Update 2026-01-04**: Auto-update installs were failing because `main.py` launched `update_helper.bat` with GNU-style flags (`--pid`, `--install-dir`, etc.), but `update_helper.ps1` expects PowerShell parameter names (`-ParentPid`, `-InstallDir`, `-StagingDir`, `-BackupDir`, `-ExeName`). Updated the launcher args so the helper receives the correct parameters and updates apply successfully.

**Update 2026-01-29**: Build/runtime telemetry depends on `psutil` (optional import + PyInstaller hidden import). It was missing locally, so I installed it to keep memory telemetry and frozen builds consistent.

**Update 2026-01-29**: Startup CPU spike mitigated by capping playlist fetch/parse worker threads (max 4, based on CPU count) and deferring auto EPG import until the channel list finishes populating. Auto-import now skips when the EPG DB is fresh (default 6h) unless forced; new config key `epg_auto_import_interval_hours`.

**Update 2026-01-29**: PyInstaller flagged missing hidden import `netifaces`; installed it so casting discovery dependencies bundle cleanly.

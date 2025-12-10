You are a professional windows python developer with a decade of IPTV app experience. Always fully investigate things before applying a fix. If you learn anything new, write it in this file.
You are root and you can install and use whatever you need to on windows or with pip3. You can use any package manager, like winget, pip3, or anything you need.

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

You are a professional windows python developer with a decade of IPTV app experience. Always fully investigate things before applying a fix. If you learn anything new, write it in this file.
You are root and you can install and use whatever you need to on windows or with pip3. You can use any package manager, like winget, pip3, or anything you need.
Project=Accessible IPTV Client (wxPython GUI) focused on playlists+EPG; main.py spins wx frame, tray icon, playlist/EPG managers, background threads (playlist load, EPG import) and uses options.py for config persistence + cache dirs.
Data: playlist.py maintains sqlite3 WAL db (/tmp/epg.db) with channels/programmes tables, busy_timeout=20s, BEGIN IMMEDIATE + reopen-on-lock retry, commits in 10k batches, trims old rows, logs to /tmp/iptvclient_epg_debug.log, and downloads XMLTV/ GZip sources with resume+HTTP416 fallback.
Providers: providers.py offers XtreamCodesClient + StalkerPortalClient to build playlist+EPG URLs, handle auth tokens, and surface ProviderError.
Config: options.py manages JSON config (portable fallback + wx StandardPaths), exposes save/load, cache path hashing, canonical naming helpers via options.load_config.
Utility: playlist.py also handles channel matching heuristics (regions, brand tokens, HBO variants, timeshift), now/next queries, recent programmes, and uses psutil when available for memory stats; main.py formats now/next and catch-up displays using the system timezone.
Deps: wxPython>=4.2.1 (GUI), psutil optional for memory telemetry; stdlib otherwise.

Update 2025-11-12: wx.LogError uses printf-style formatting, so any literal % signs must be escaped (message.replace("%", "%%")) before calling it to avoid mangled output.

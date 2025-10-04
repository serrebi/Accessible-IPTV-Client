Project=Accessible IPTV Client (wxPython GUI) focused on playlists+EPG; main.py spins wx frame, tray icon, playlist/EPG managers, background threads (playlist load, EPG import) and uses options.py for config persistence + cache dirs.
Data: playlist.py maintains sqlite3 WAL db (/tmp/epg.db) with channels/programmes tables, busy_timeout=20s, BEGIN IMMEDIATE + reopen-on-lock retry, commits in 10k batches, trims old rows, logs to /tmp/iptvclient_epg_debug.log, and downloads XMLTV/ GZip sources with resume+HTTP416 fallback.
Providers: providers.py offers XtreamCodesClient + StalkerPortalClient to build playlist+EPG URLs, handle auth tokens, and surface ProviderError.
Config: options.py manages JSON config (portable fallback + wx StandardPaths), exposes save/load, cache path hashing, canonical naming helpers via options.load_config, etc.
Utility: playlist.py also handles channel matching heuristics (regions, brand tokens, HBO variants, timeshift), now/next queries, recent programmes, and uses psutil when available for memory stats.
Deps: wxPython>=4.2.1 (GUI), psutil optional for memory telemetry; stdlib otherwise.

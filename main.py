import os
import sys
import json
import socket
import tempfile
import ctypes
import urllib.request
import urllib.parse
import gzip
import sqlite3
import threading
from typing import Dict, List, Optional
import wx
import xml.etree.ElementTree as ET
import datetime
import re
import shutil
import platform
import time
import signal
import subprocess
import hashlib

import wx.adv

from options import (
    load_config, save_config, get_cache_path_for_url, get_cache_dir,
    get_db_path, canonicalize_name, relaxed_name, extract_group, utc_to_local,
    CustomPlayerDialog
)
from playlist import (
    EPGDatabase, EPGImportDialog, EPGManagerDialog, PlaylistManagerDialog
)
from providers import (
    XtreamCodesClient, XtreamCodesConfig,
    StalkerPortalClient, StalkerPortalConfig,
    ProviderError, generate_provider_id
)

try:
    from internal_player import (
        InternalPlayerFrame,
        InternalPlayerUnavailableError,
        _VLC_IMPORT_ERROR,
    )
except Exception as _internal_player_import_error:  # pragma: no cover - import guard
    InternalPlayerFrame = None  # type: ignore[assignment]
    _VLC_IMPORT_ERROR = _internal_player_import_error  # type: ignore[assignment]

    class InternalPlayerUnavailableError(RuntimeError):
        """Fallback error when internal player cannot load."""


_M3U_ATTR_RE = re.compile(r'([A-Za-z0-9_\-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^",\s]+))')

def set_linux_env():
    if platform.system() != "Linux":
        return

    os.environ["UBUNTU_MENUPROXY"] = "0"
    distro = "unknown"
    try:
        with open("/etc/os-release") as f:
            os_release = f.read().lower()
        if "ubuntu" in os_release:
            distro = "ubuntu"
        elif "debian" in os_release:
            distro = "debian"
        elif "arch" in os_release and "manjaro" not in os_release:
            distro = "arch"
        elif "manjaro" in os_release:
            distro = "manjaro"
        elif "fedora" in os_release:
            distro = "fedora"
        elif "centos" in os_release:
            distro = "centos"
        elif "rhel" in os_release or "red hat" in os_release:
            distro = "rhel"
        elif "opensuse" in os_release or "suse" in os_release:
            distro = "opensuse"
        elif "mint" in os_release:
            distro = "mint"
        elif "pop" in os_release and "pop_os" in os_release:
            distro = "popos"
    except Exception:
        pass

    os.environ["MYAPP_DISTRO"] = distro

    if distro == "ubuntu":
        os.environ["UBUNTU_MENUPROXY"] = "0"
        os.environ["GTK_MODULES"] = os.environ.get("GTK_MODULES", "")
    elif distro == "debian":
        os.environ["GTK_OVERLAY_SCROLLING"] = "0"
    elif distro == "arch":
        os.environ["NO_AT_BRIDGE"] = "0"
    elif distro == "manjaro":
        os.environ["NO_AT_BRIDGE"] = "0"
    elif distro == "fedora":
        os.environ["GTK_USE_PORTAL"] = "1"
    elif distro == "centos":
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    elif distro == "rhel":
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    elif distro == "opensuse":
        os.environ["XDG_CURRENT_DESKTOP"] = os.environ.get("XDG_CURRENT_DESKTOP", "KDE")
    elif distro == "mint":
        os.environ["XDG_CURRENT_DESKTOP"] = os.environ.get("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    elif distro == "popos":
        os.environ["GDK_BACKEND"] = os.environ.get("GDK_BACKEND", "x11")

set_linux_env()

class TrayIcon(wx.adv.TaskBarIcon):
    TBMENU_RESTORE = wx.NewIdRef()
    TBMENU_EXIT = wx.NewIdRef()

    def __init__(self, parent, on_restore, on_exit):
        super().__init__()
        self.parent = parent
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_taskbar_activate)
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, self.on_taskbar_activate)
        self.Bind(wx.EVT_MENU, self.on_menu_select)
        self.set_icon()

    def set_icon(self):
        icon = wx.Icon(wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_TOOLBAR, (16, 16)))
        self.SetIcon(icon, "Accessible IPTV Client")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        menu.Append(self.TBMENU_RESTORE, "Restore")
        menu.AppendSeparator()
        menu.Append(self.TBMENU_EXIT, "Exit")
        return menu

    def on_taskbar_activate(self, event):
        parent = self.parent
        if not getattr(parent, "_tray_allow_restore", True):
            return
        is_shown = parent.IsShownOnScreen() if hasattr(parent, "IsShownOnScreen") else parent.IsShown()
        if is_shown:
            return
        self.on_restore()

    def on_menu_select(self, event):
        eid = event.GetId()
        if eid == self.TBMENU_RESTORE:
            self.on_restore()
        elif eid == self.TBMENU_EXIT:
            self.on_exit()

class IPTVClient(wx.Frame):
    PLAYER_KEYS = [
        ("Built-in Player", "player_Internal"),
        ("VLC", "player_VLC"),
        ("MPC", "player_MPC"),
        ("MPC-BE", "player_MPCBE"),
        ("Kodi", "player_Kodi"),
        ("Winamp", "player_Winamp"),
        ("Foobar2000", "player_Foobar2000"),
        ("MPV", "player_MPV"),
        ("SMPlayer", "player_SMPlayer"),
        ("Totem", "player_Totem"),
        ("QuickTime", "player_QuickTime"),
        ("iTunes/Apple Music", "player_iTunes"),
        ("PotPlayer", "player_PotPlayer"),
        ("KMPlayer", "player_KMPlayer"),
        ("AIMP", "player_AIMP"),
        ("QMPlay2", "player_QMPlay2"),
        ("GOM Player", "player_GOMPlayer"),
        ("Audacious", "player_Audacious"),
        ("Fauxdacious", "player_Fauxdacious"),
        ("Clementine", "player_Clementine"),
        ("Strawberry", "player_Strawberry"),
        ("Amarok", "player_Amarok"),
        ("Rhythmbox", "player_Rhythmbox"),
        ("Pragha", "player_Pragha"),
        ("Lollypop", "player_Lollypop"),
        ("Exaile", "player_Exaile"),
        ("Quod Libet", "player_QuodLibet"),
        ("Gmusicbrowser", "player_Gmusicbrowser"),
        ("Xmms", "player_Xmms"),
        ("Vocal", "player_Vocal"),
        ("Haruna", "player_Haruna"),
        ("Celluloid", "player_Celluloid"),
    ]
    PLAYER_MENU_ATTRS = dict(PLAYER_KEYS)

    _CACHE_SHOW_STALE_SECS = 600
    _CACHE_REFRESH_AFTER_SECS = 180

    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))
        self.config = load_config()
        self.playlist_sources = self.config.get("playlists", [])
        self.epg_sources = self.config.get("epgs", [])
        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.displayed: List[Dict[str, str]] = []
        self.current_group = "All Channels"
        self.default_player = self.config.get("media_player", "Built-in Player")
        self.custom_player_path = self.config.get("custom_player_path", "")
        self.epg_importing = False
        self.epg_cache = {}
        self.epg_cache_lock = threading.Lock()
        self.refresh_timer = None
        self.minimize_to_tray = bool(self.config.get("minimize_to_tray", False))
        self.tray_icon = None
        self._tray_allow_restore = False
        self._tray_ready_timer: Optional[wx.CallLater] = None
        self.provider_clients: Dict[str, object] = {}
        self.provider_epg_sources: List[str] = []
        self._internal_player_frame: Optional[InternalPlayerFrame] = None

        # batch-population state to avoid UI hangs
        self._populate_token = 0

        # Timer for polling DB during EPG import so UI shows incoming data.
        self._epg_poll_timer: Optional[wx.Timer] = None
        # Track in-flight EPG fetches to avoid hammering get_now_next while importer is busy
        self._epg_fetch_inflight = set()
        self._epg_inflight_lock = threading.Lock()

        self._ensure_db_tuned()
        self._build_ui()
        self.Centre()

        self.group_list.Append("Loading playlists...")
        self.Show()

        # Defer all loading. This call starts ONLY the playlist loading thread.
        wx.CallAfter(self.start_playlist_load)
        
        self.Bind(wx.EVT_ICONIZE, self.on_minimize)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def _channel_is_epg_exempt(self, channel: Dict[str, str]) -> bool:
        """Detect channels that typically have no EPG (e.g., 24/7 loops).
        We do NOT modify names; this only avoids unnecessary DB lookups/logs.
        Rule: tvg-id empty AND name/group contains '24/7' or '24x7'.
        """
        try:
            tvg_id = (channel.get("tvg-id") or channel.get("tvg_id") or "").strip()
            if tvg_id:
                return False
            name = (channel.get("tvg-name") or channel.get("name") or "").lower()
            group = (channel.get("group-title") or channel.get("group") or "").lower()
            if "24/7" in name or "24x7" in name or "24/7" in group or "24x7" in group:
                return True
        except Exception:
            pass
        return False

    def start_playlist_load(self):
        """Kicks off ONLY the playlist loading thread."""
        threading.Thread(target=self._do_playlist_refresh, daemon=True).start()

    def _ensure_db_tuned(self):
        """Enable WAL and indices so read lookups don’t stall behind imports."""
        try:
            path = get_db_path()
            uri = f"file:{path}?cache=shared"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute("PRAGMA mmap_size=268435456;")
            cur.execute("PRAGMA cache_size=-65536;")
            cur.execute("PRAGMA wal_autocheckpoint=0;")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_end ON programmes(channel_id, end);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start ON programmes(channel_id, start);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start_end ON programmes(channel_id, start, end);")
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _sync_player_menu_from_config(self):
        defplayer = self.config.get("media_player", "Built-in Player")
        self.default_player = defplayer
        for key, attr in self.PLAYER_MENU_ATTRS.items():
            if hasattr(self, attr):
                getattr(self, attr).Check(key == defplayer)
        if hasattr(self, "player_Custom"):
            self.player_Custom.Check(defplayer == "Custom")
        if hasattr(self, "_player_radio_items"):
            for label, item in self._player_radio_items.items():
                item.Check(label == defplayer)

    def on_menu_open(self, event):
        from options import load_config
        self.config = load_config()
        self._sync_player_menu_from_config()
        if hasattr(self, "min_to_tray_item"):
            self.minimize_to_tray = bool(self.config.get("minimize_to_tray", False))
            self.min_to_tray_item.Check(self.minimize_to_tray)
        event.Skip()

    def start_refresh_timer(self):
        if self.refresh_timer:
            self.refresh_timer.Stop()
        self.refresh_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer_refresh, self.refresh_timer)
        self.refresh_timer.Start(3 * 60 * 60 * 1000, wx.TIMER_CONTINUOUS)

    def on_timer_refresh(self, event):
        # This full cycle can be triggered by the timer.
        self.start_playlist_load()

    def _do_playlist_refresh(self):
        """
        Loads playlists from cache for a fast UI update, then refreshes from the network.
        Crucially, it only starts the EPG import *after* playlists are loaded.
        """
        playlist_sources = self.config.get("playlists", [])
        # Ensure all provider entries have persistent IDs so we can track clients.
        mutated = False
        for src in playlist_sources:
            if isinstance(src, dict) and not src.get("id"):
                src["id"] = generate_provider_id()
                mutated = True
        if mutated:
            self.config["playlists"] = playlist_sources
            save_config(self.config)

        channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        all_channels: List[Dict[str, str]] = []
        valid_caches = set()
        seen_channel_keys = set()
        results: List[Optional[Dict]] = [None] * len(playlist_sources)
        provider_clients_local: Dict[str, object] = {}
        provider_epg_sources: List[str] = []
        provider_lock = threading.Lock()

        def fetch_playlist(idx, src):
            try:
                if isinstance(src, dict):
                    stype = (src.get("type") or "").lower()
                    provider_id = src.get("id") or src.get("provider_id")
                    if stype == "xtream":
                        cfg = XtreamCodesConfig(
                            base_url=src.get("base_url") or src.get("url") or "",
                            username=src.get("username", ""),
                            password=src.get("password", ""),
                            stream_type=src.get("stream_type", "m3u_plus"),
                            output=src.get("output", "ts"),
                            name=src.get("name"),
                            auto_epg=bool(src.get("auto_epg", True)),
                            provider_id=provider_id
                        )
                        client = XtreamCodesClient(cfg)
                        text = client.fetch_playlist()
                        text_hash = self._playlist_text_hash(text)
                        cache_key = provider_id or f"xtream:{cfg.base_url}:{cfg.username}"
                        parsed_cache = self._parsed_cache_path_for_key(f"provider:{cache_key}")
                        provider_meta = {"provider-type": "xtream", "provider-id": provider_id}
                        results[idx] = {
                            "kind": "m3u",
                            "text": text,
                            "provider": provider_meta,
                            "hash": text_hash,
                            "cache_path": parsed_cache
                        }
                        with provider_lock:
                            provider_clients_local[provider_id] = client
                            if cfg.auto_epg:
                                for epg in client.epg_urls():
                                    if epg and epg not in provider_epg_sources:
                                        provider_epg_sources.append(epg)
                    elif stype == "stalker":
                        cfg = StalkerPortalConfig(
                            base_url=src.get("base_url") or src.get("url") or "",
                            username=src.get("username", ""),
                            password=src.get("password", ""),
                            mac=src.get("mac", ""),
                            name=src.get("name"),
                            auto_epg=bool(src.get("auto_epg", True)),
                            provider_id=provider_id
                        )
                        client = StalkerPortalClient(cfg)
                        channels, epgs = client.fetch_channels()
                        for ch in channels:
                            ch.setdefault("provider-id", provider_id)
                            ch.setdefault("provider-type", "stalker")
                        results[idx] = {"kind": "channels", "channels": channels}
                        with provider_lock:
                            provider_clients_local[provider_id] = client
                            for epg in epgs:
                                if epg and epg not in provider_epg_sources:
                                    provider_epg_sources.append(epg)
                    else:
                        results[idx] = None
                    return

                # Plain playlist path or URL
                if isinstance(src, str) and src.startswith(("http://", "https://")):
                    cache_path = get_cache_path_for_url(src)
                    parsed_cache = self._parsed_cache_path_for_key(src)
                    valid_caches.add(cache_path)
                    download = True
                    if os.path.exists(cache_path):
                        age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(cache_path))).total_seconds()
                        if age < 15 * 60:
                            download = False

                    if download:
                        with urllib.request.urlopen(
                            urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"}), timeout=60
                        ) as resp:
                            raw = resp.read()
                            try:
                                text = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                text = raw.decode("latin-1", "ignore")
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(text)
                    else:
                        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    text_hash = self._playlist_text_hash(text)
                    results[idx] = {
                        "kind": "m3u",
                        "text": text,
                        "provider": None,
                        "hash": text_hash,
                        "cache_path": parsed_cache
                    }
                elif isinstance(src, str) and os.path.exists(src):
                    with open(src, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    text_hash = self._playlist_text_hash(text)
                    cache_key = f"file:{os.path.abspath(src)}"
                    parsed_cache = self._parsed_cache_path_for_key(cache_key)
                    results[idx] = {
                        "kind": "m3u",
                        "text": text,
                        "provider": None,
                        "hash": text_hash,
                        "cache_path": parsed_cache
                    }
                else:
                    results[idx] = None
            except Exception as e:
                results[idx] = {"error": str(e)}

        threads = []
        for idx, src in enumerate(playlist_sources):
            t = threading.Thread(target=fetch_playlist, args=(idx, src), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        # Build channel aggregates
        for result in results:
            if not result:
                continue
            if result.get("error"):
                continue
            if result.get("kind") == "channels":
                channels = result.get("channels", [])
            else:
                text = result.get("text")
                if not text:
                    continue
                cache_path = result.get("cache_path")
                text_hash = result.get("hash")
                provider_meta = result.get("provider")
                channels = None
                if cache_path and text_hash:
                    # Reuse a parsed playlist snapshot when the raw text hasn't changed.
                    channels = self._load_cached_playlist(cache_path, text_hash, provider_meta)
                if channels is None:
                    channels = self._parse_m3u_return(text, provider_info=provider_meta)
                    if cache_path and text_hash:
                        self._store_cached_playlist(cache_path, text_hash, channels, provider_meta)

            for ch in channels or []:
                key = (ch.get("name", ""), ch.get("url", ""), ch.get("provider-id", ""))
                if key in seen_channel_keys:
                    continue
                seen_channel_keys.add(key)
                grp = ch.get("group") or "Uncategorized"
                channels_by_group.setdefault(grp, []).append(ch)
                all_channels.append(ch)

        # Replace provider mappings atomically after successful refresh
        self.provider_clients = provider_clients_local
        self.provider_epg_sources = provider_epg_sources

        def finish_playlist_load_and_start_background_tasks():
            self.channels_by_group = channels_by_group
            self.all_channels = all_channels
            self._refresh_group_ui()
            self._cleanup_cache_and_channels(valid_caches)

            # Now that playlists are loaded, start the other processes.
            self.reload_epg_sources()
            self.start_refresh_timer()
            wx.CallLater(2000, self.start_epg_import_background)

        wx.CallAfter(finish_playlist_load_and_start_background_tasks)


    def _cleanup_cache_and_channels(self, valid_caches):
        cache_dir = get_cache_dir()
        try:
            files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.endswith(".m3u")]
        except Exception:
            return
        for f in files:
            if f not in valid_caches:
                try:
                    os.remove(f)
                except Exception:
                    pass

    def _build_ui(self):
        p = wx.Panel(self)
        hs = wx.BoxSizer(wx.HORIZONTAL)
        vs_l = wx.BoxSizer(wx.VERTICAL)
        vs_r = wx.BoxSizer(wx.VERTICAL)
        self.group_list = wx.ListBox(p, style=wx.LB_SINGLE)
        self.group_list.Bind(wx.EVT_CHAR_HOOK, self.on_group_key)
        vs_l.Add(self.group_list, 1, wx.EXPAND | wx.ALL, 5)
        self.filter_box = wx.TextCtrl(p, style=wx.TE_PROCESS_ENTER)
        self.channel_list = wx.ListBox(p, style=wx.LB_SINGLE)
        # Key bindings (original + added robust handlers)
        self.channel_list.Bind(wx.EVT_CHAR_HOOK, self.on_channel_key)  # original
        self.channel_list.Bind(wx.EVT_KEY_DOWN, self._on_channel_key_down)  # reliable Enter on all platforms
        # Mouse activation (original + added wx generic left double click)
        self.channel_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_highlight())
        self.channel_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _: self.play_selected())  # original
        self.channel_list.Bind(wx.EVT_LEFT_DCLICK, self._on_lb_activate)  # GTK/mac fallback
        self.channel_list.Bind(wx.EVT_CONTEXT_MENU, self._on_channel_context_menu)

        self.epg_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.url_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        vs_r.Add(self.filter_box, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.channel_list, 1, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.epg_display, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.url_display, 0, wx.EXPAND | wx.ALL, 5)
        hs.Add(vs_l, 1, wx.EXPAND)
        hs.Add(vs_r, 2, wx.EXPAND)

        if platform.system() == "Linux":
            self.menu_button = wx.Button(p, label="Menu")
            self._player_radio_items = {}
            def on_menu_btn(evt):
                menu = wx.Menu()
                menu.Append(1001, "Playlist Manager\tCtrl+M")
                menu.Append(1002, "EPG Manager\tCtrl+E")
                menu.Append(1003, "Import EPG to DB\tCtrl+I")
                menu.AppendSeparator()
                player_menu = wx.Menu()
                for idx, (label, attr) in enumerate(self.PLAYER_KEYS):
                    itemid = 2000 + idx
                    item = player_menu.AppendRadioItem(itemid, label)
                    self._player_radio_items[label] = item
                    self.Bind(wx.EVT_MENU, lambda evt, pl=label: self._select_player(pl), id=itemid)
                    if self.default_player == label:
                        item.Check(True)
                customid = 2999
                customitem = player_menu.AppendRadioItem(customid, "Custom Player...")
                self.Bind(wx.EVT_MENU, self._select_custom_player, id=customid)
                if self.default_player == "Custom":
                    customitem.Check(True)
                menu.AppendSubMenu(player_menu, "Media Player to Use")
                min_to_tray_id = 1101
                min_item = menu.AppendCheckItem(min_to_tray_id, "Minimize to System Tray")
                min_item.Check(self.minimize_to_tray)
                self.Bind(wx.EVT_MENU, self.on_toggle_min_to_tray, id=min_to_tray_id)
                menu.AppendSeparator()
                menu.Append(1004, "Exit\tCtrl+Q")
                self.Bind(wx.EVT_MENU, self.show_manager, id=1001)
                self.Bind(wx.EVT_MENU, self.show_epg_manager, id=1002)
                self.Bind(wx.EVT_MENU, self.import_epg, id=1003)
                self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=1004)
                self.menu_button.PopupMenu(menu)
            self.menu_button.Bind(wx.EVT_BUTTON, on_menu_btn)
            sizer_with_menu = wx.BoxSizer(wx.VERTICAL)
            sizer_with_menu.Add(self.menu_button, 0, wx.EXPAND | wx.ALL, 5)
            sizer_with_menu.Add(hs, 1, wx.EXPAND)
            p.SetSizerAndFit(sizer_with_menu)
        else:
            p.SetSizerAndFit(hs)
            mb = wx.MenuBar()
            fm = wx.Menu()
            m_mgr = fm.Append(wx.ID_ANY, "Playlist Manager\tCtrl+M")
            m_epg = fm.Append(wx.ID_ANY, "EPG Manager\tCtrl+E")
            m_imp = fm.Append(wx.ID_ANY, "Import EPG to DB\tCtrl+I")
            fm.appendSeparator = fm.AppendSeparator()
            m_exit = fm.Append(wx.ID_EXIT, "Exit\tCtrl+Q")
            mb.Append(fm, "File")
            om = wx.Menu()
            player_menu = wx.Menu()
            self.player_menu_items = []
            for label, attr in self.PLAYER_KEYS:
                item = player_menu.AppendRadioItem(wx.ID_ANY, label)
                setattr(self, attr, item)
                self.player_menu_items.append((item, label))
            self.player_Custom = player_menu.AppendRadioItem(wx.ID_ANY, "Custom Player...")
            om.AppendSubMenu(player_menu, "Media Player to Use")
            self.min_to_tray_item = om.AppendCheckItem(wx.ID_ANY, "Minimize to System Tray")
            om.AppendSeparator()
            mb.Append(om, "Options")
            self.SetMenuBar(mb)
            self.Bind(wx.EVT_MENU, self.show_manager, m_mgr)
            self.Bind(wx.EVT_MENU, self.show_epg_manager, m_epg)
            self.Bind(wx.EVT_MENU, self.import_epg, m_imp)
            self.Bind(wx.EVT_MENU, lambda _: self.Close(), m_exit)
            for item, key in self.player_menu_items:
                self.Bind(wx.EVT_MENU, lambda evt, attr=key: self._select_player(attr), item)
            self.Bind(wx.EVT_MENU, self._select_custom_player, self.player_Custom)
            self.Bind(wx.EVT_MENU, self.on_toggle_min_to_tray, self.min_to_tray_item)
            self.Bind(wx.EVT_MENU_OPEN, self.on_menu_open)
            self._sync_player_menu_from_config()
            self.min_to_tray_item.Check(self.minimize_to_tray)

        self.group_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_group_select())
        self.filter_box.Bind(wx.EVT_TEXT_ENTER, lambda _: self.apply_filter())

        entries = [
            (wx.ACCEL_CTRL, ord('M'), 4001),
            (wx.ACCEL_CTRL, ord('E'), 4002),
            (wx.ACCEL_CTRL, ord('I'), 4003),
            (wx.ACCEL_CTRL, ord('Q'), 4004),
        ]
        atable = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(atable)
        self.Bind(wx.EVT_MENU, self.show_manager, id=4001)
        self.Bind(wx.EVT_MENU, self.show_epg_manager, id=4002)
        self.Bind(wx.EVT_MENU, self.import_epg, id=4003)
        self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=4004)

    def _on_lb_activate(self, event):
        # Called on generic left double click to ensure activation on GTK/mac too
        self.play_selected()
        # Intentionally do not event.Skip() to avoid duplicate handling.

    def _on_channel_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.play_selected()
            return  # swallow to prevent beep/focus issues
        event.Skip()

    def _on_channel_context_menu(self, event):
        if not self.displayed:
            return
        pos = event.GetPosition()
        idx = self.channel_list.GetSelection()
        if pos != wx.DefaultPosition and not (pos.x == -1 and pos.y == -1):
            try:
                local = self.channel_list.ScreenToClient(pos)
                hit = self.channel_list.HitTest(local) if hasattr(self.channel_list, "HitTest") else (-1,)
                hit_idx = hit[0] if isinstance(hit, tuple) else hit
                if hit_idx not in (None, -1) and 0 <= hit_idx < len(self.displayed):
                    self.channel_list.SetSelection(hit_idx)
                    self.channel_list.SetFocus()
                    idx = hit_idx
            except Exception:
                pass
        if idx == wx.NOT_FOUND and self.channel_list.GetCount():
            self.channel_list.SetSelection(0)
            idx = 0
        if idx == wx.NOT_FOUND or idx >= len(self.displayed):
            return
        item = self.displayed[idx]
        channel = None
        if item.get("type") == "channel":
            channel = item.get("data")
        elif item.get("type") == "epg":
            channel = self._find_channel_for_epg(item.get("data", {}))
        if not channel:
            return
        menu = wx.Menu()
        play_item = menu.Append(wx.ID_ANY, "Play")
        menu.Bind(wx.EVT_MENU, lambda evt: self.play_selected(), play_item)
        if self._channel_has_catchup(channel):
            catch_item = menu.Append(wx.ID_ANY, "Play Catch-up…")
            menu.Bind(wx.EVT_MENU, lambda evt, ch=channel: self._open_catchup_dialog(ch), catch_item)
        try:
            self.channel_list.PopupMenu(menu)
        finally:
            menu.Destroy()

    def on_toggle_min_to_tray(self, event):
        if platform.system() == "Linux":
            self.minimize_to_tray = not self.minimize_to_tray
        else:
            self.minimize_to_tray = self.min_to_tray_item.IsChecked()
        self.config["minimize_to_tray"] = self.minimize_to_tray
        save_config(self.config)

    def show_tray_icon(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if not self.tray_icon:
            self.tray_icon = TrayIcon(
                self,
                on_restore=self.restore_from_tray,
                on_exit=self.exit_from_tray
            )
        self.Hide()
        self._tray_ready_timer = wx.CallLater(250, self._enable_tray_restore)

    def restore_from_tray(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if self.tray_icon:
            try:
                self.tray_icon.RemoveIcon()
            except Exception:
                pass
            self.tray_icon.Destroy()
            self.tray_icon = None
        self.Show()
        self.Raise()
        self.Iconize(False)

    def exit_from_tray(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if self.tray_icon:
            try:
                self.tray_icon.RemoveIcon()
            except Exception:
                pass
            self.tray_icon.Destroy()
            self.tray_icon = None
        self.Destroy()

    def _enable_tray_restore(self):
        self._tray_ready_timer = None
        if self.tray_icon:
            self._tray_allow_restore = True

    def _cancel_tray_ready_timer(self):
        if self._tray_ready_timer:
            try:
                self._tray_ready_timer.Stop()
            except Exception:
                pass
            self._tray_ready_timer = None

    def on_minimize(self, event):
        if self.minimize_to_tray and event.IsIconized():
            wx.CallAfter(self.show_tray_icon)
        else:
            event.Skip()

    def on_close(self, event):
        if self.minimize_to_tray:
            wx.CallAfter(self.show_tray_icon)
            event.Veto()
        else:
            # Ensure poll timer stopped on exit
            try:
                self._stop_epg_poll_timer()
            except Exception:
                pass
            if self.tray_icon:
                try:
                    self.tray_icon.RemoveIcon()
                except Exception:
                    pass
                self.tray_icon.Destroy()
                self.tray_icon = None
            frame = getattr(self, "_internal_player_frame", None)
            if frame is not None:
                try:
                    frame.Destroy()
                except Exception:
                    pass
                self._internal_player_frame = None
            self.Destroy()

    def _select_player(self, player):
        self.default_player = player
        self.config["media_player"] = player
        save_config(self.config)
        self._sync_player_menu_from_config()

    def _select_custom_player(self, _):
        dlg = CustomPlayerDialog(self, self.config.get("custom_player_path", ""))
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath().strip()
            if path:
                self.custom_player_path = path
                self.default_player = "Custom"
                self.config["media_player"] = "Custom"
                self.config["custom_player_path"] = path
                save_config(self.config)
        dlg.Destroy()
        self._sync_player_menu_from_config()

    def on_channel_key(self, event):
        # Kept for compatibility; EVT_KEY_DOWN handler above is the reliable path
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.play_selected()
        elif key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            return
        else:
            event.Skip()

    def on_group_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            return
        else:
            event.Skip()

    def apply_filter(self):
        txt = self.filter_box.GetValue().strip().lower()
        self._populate_token += 1
        self.displayed = []
        self.channel_list.Freeze()
        try:
            self.channel_list.Clear()
            source = (self.all_channels if self.current_group == "All Channels"
                      else self.channels_by_group.get(self.current_group, []))
            if not txt:
                # Rebuild current group quickly without blocking the UI
                self.channel_list.Thaw()
                self.on_group_select()
                return

            items = []
            for ch in source:
                name = (ch.get("name") or "")
                if txt and txt not in name.lower():
                    continue
                self.displayed.append({"type": "channel", "data": ch})
                items.append(name)

            if items:
                self.channel_list.AppendItems(items)

            # Kick off EPG search in background and append results later
            if not hasattr(self, "_search_token"):
                self._search_token = 0
            self._search_token += 1
            my_token = self._search_token

            def epg_search(token):
                try:
                    db = EPGDatabase(get_db_path(), readonly=True)
                    try:
                        if hasattr(db, "conn"):
                            db.conn.execute("PRAGMA busy_timeout=2000;")
                            db.conn.execute("PRAGMA read_uncommitted=1;")
                    except Exception:
                        pass
                    results = db.get_channels_with_show(txt)
                    try:
                        if hasattr(db, "close"):
                            db.close()
                        elif hasattr(db, "conn"):
                            db.conn.close()
                    except Exception:
                        pass
                except Exception:
                    results = []
                def update_ui():
                    if getattr(self, "_search_token", 0) != token:
                        return
                    if txt != self.filter_box.GetValue().strip().lower():
                        return
                    # Preserve current selection to avoid scroll jumps while appending
                    try:
                        cur_sel = self.channel_list.GetSelection()
                        cur_count = self.channel_list.GetCount()
                    except Exception:
                        cur_sel, cur_count = wx.NOT_FOUND, 0
                    if results:
                        add_items = []
                        for r in results:
                            chan_name = r.get('channel_name') or ""
                            show_name = r.get('show_title') or ""
                            chan_lower = chan_name.lower()
                            show_lower = show_name.lower()
                            if txt and txt not in chan_lower and txt not in show_lower:
                                continue
                            label = f"{r['channel_name']} - {r['show_title']} ({self._fmt_time(r['start'])}–{self._fmt_time(r['end'])})"
                            self.displayed.append({"type": "epg", "data": r})
                            add_items.append(label)
                        if add_items:
                            self.channel_list.AppendItems(add_items)
                    # Only auto-select the first item if the list was previously empty
                    # and nothing is selected. Do NOT steal focus or jump the list.
                    try:
                        if cur_count == 0 and cur_sel in (-1, wx.NOT_FOUND) and self.channel_list.GetCount() > 0:
                            # Leave selection empty to avoid scroll jump; user can choose.
                            # If desired later, we can make this opt-in via a setting.
                            pass
                        elif cur_sel not in (-1, wx.NOT_FOUND) and cur_sel < self.channel_list.GetCount():
                            # Reinstate prior selection to keep view position stable.
                            self.channel_list.SetSelection(cur_sel)
                    except Exception:
                        pass
                wx.CallAfter(update_ui)
            threading.Thread(target=lambda: epg_search(my_token), daemon=True).start()
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass

    def _refresh_group_ui(self):
        self.group_list.Freeze()
        try:
            self.group_list.Clear()
            self.channel_list.Clear()

            if not self.all_channels:
                self.group_list.Append("No channels found.")
                return

            self.group_list.Append(f"All Channels ({len(self.all_channels)})")
            for grp in sorted(self.channels_by_group):
                self.group_list.Append(f"{grp} ({len(self.channels_by_group[grp])})")
            
            try:
                current_idx = self.group_list.FindString(self.current_group)
                if current_idx != wx.NOT_FOUND and self.current_group != "All Channels":
                    for i in range(self.group_list.GetCount()):
                        if self.group_list.GetString(i).startswith(f"{self.current_group} ("):
                            current_idx = i
                            break
                self.group_list.SetSelection(current_idx if current_idx != wx.NOT_FOUND else 0)
            except Exception:
                self.group_list.SetSelection(0)
        finally:
            try:
                self.group_list.Thaw()
            except Exception:
                pass
        
        self.on_group_select()

    def reload_epg_sources(self):
        base = list(self.config.get("epgs", []))
        for epg in getattr(self, "provider_epg_sources", []):
            if epg not in base:
                base.append(epg)
        self.epg_sources = base

    def start_epg_import_background(self):
        sources = list(self.epg_sources)
        if not sources or not self.config.get("epg_enabled", True):
            return
        if self.epg_importing:
            return
        self.epg_importing = True

        # Start a short poll timer so UI can show EPG as it arrives for the selected channel.
        wx.CallAfter(self._start_epg_poll_timer)

        def do_import():
            try:
                db = EPGDatabase(get_db_path(), for_threading=True)
                # Pass a coarse progress callback (per-source). The DB importer writes as it streams,
                # so readers can pick up newly inserted rows during import.
                db.import_epg_xml(sources)
                try:
                    if hasattr(db, "close"):
                        db.close()
                    elif hasattr(db, "conn"):
                        db.conn.close()
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                wx.CallAfter(self.finish_import_background)
        threading.Thread(target=do_import, daemon=True).start()

    def finish_import_background(self):
        self.epg_importing = False
        # Stop import-specific polling and restart steady refresh timer
        self._stop_epg_poll_timer()
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()
        self._start_epg_poll_timer()

    def show_manager(self, _):
        dlg = PlaylistManagerDialog(self, self.playlist_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.playlist_sources = dlg.GetResult()
            self.config["playlists"] = self.playlist_sources
            save_config(self.config)
            self.start_playlist_load() # Reload everything after changes
        dlg.Destroy()

    def show_epg_manager(self, _):
        dlg = EPGManagerDialog(self, self.epg_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.epg_sources = dlg.GetResult()
            self.config["epgs"] = self.epg_sources
            save_config(self.config)
            self.reload_epg_sources()
            wx.CallLater(1000, self.start_epg_import_background) # Start import after dialog closes
        dlg.Destroy()

    def import_epg(self, _):
        if self.epg_importing:
            wx.MessageBox("EPG import is already in progress.", "In Progress", wx.OK | wx.ICON_INFORMATION)
            return
        
        if not self.epg_sources:
            wx.MessageBox("No EPG sources configured. Please add one in File > EPG Manager.", "No Sources", wx.OK | wx.ICON_WARNING)
            return

        wx.MessageBox("EPG import will start in the background.", "Import Started", wx.OK | wx.ICON_INFORMATION)
        self.start_epg_import_background()

    def finish_import(self):
        # legacy-sounding API; ensure poll timer is stopped here too.
        self.epg_importing = False
        self._stop_epg_poll_timer()
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()
        self._start_epg_poll_timer()

    def _parse_m3u_return(self, text, provider_info=None):
        provider_info = provider_info or {}
        provider_id = provider_info.get("provider-id")
        provider_type = provider_info.get("provider-type")

        out: List[Dict[str, str]] = []
        append = out.append
        extract_group_local = extract_group
        stream_id_for = self._extract_stream_id
        attr_iter = _M3U_ATTR_RE.finditer

        # Per-channel metadata, reset after each URL
        name = ""
        group = ""
        tvg_id = ""
        tvg_name = ""
        tvg_logo = ""
        tvg_rec = ""
        timeshift = ""
        catchup = ""
        catchup_type = ""
        catchup_days = ""
        catchup_source = ""
        catchup_offset = ""
        http_user_agent = ""

        for raw_line in text.splitlines():
            s = raw_line.strip()
            if not s:
                continue

            if s[0] == '#':
                upper_prefix = s[:10].upper()
                if upper_prefix.startswith("#EXTINF"):
                    name = ""
                    group = ""
                    tvg_id = ""
                    tvg_name = ""
                    tvg_logo = ""
                    tvg_rec = ""
                    timeshift = ""
                    catchup = ""
                    catchup_type = ""
                    catchup_days = ""
                    catchup_source = ""
                    catchup_offset = ""
                    http_user_agent = ""

                    comma_idx = s.find(',')
                    info_part = s if comma_idx == -1 else s[:comma_idx]
                    if comma_idx != -1:
                        name = s[comma_idx + 1:].strip()

                    colon_idx = info_part.find(':')
                    attr_segment = info_part[colon_idx + 1:] if colon_idx != -1 else ""
                    if attr_segment:
                        attrs: Dict[str, str] = {}
                        for match in attr_iter(attr_segment):
                            key = match.group(1).lower()
                            value = match.group(2) or match.group(3) or match.group(4) or ""
                            if key not in attrs:
                                attrs[key] = value.strip()
                        if attrs:
                            group = attrs.get("group-title", "")
                            tvg_id = attrs.get("tvg-id", "")
                            tvg_name = attrs.get("tvg-name", "")
                            tvg_logo = attrs.get("tvg-logo") or attrs.get("logo") or ""
                            tvg_rec = attrs.get("tvg-rec", "")
                            timeshift = attrs.get("timeshift", "")
                            catchup = attrs.get("catchup", "")
                            catchup_type = attrs.get("catchup-type", "")
                            catchup_days = attrs.get("catchup-days", "")
                            catchup_source = attrs.get("catchup-source", "")
                            catchup_offset = attrs.get("catchup-offset", "")
                            http_user_agent = attrs.get("http-user-agent", "")
                    continue

                if upper_prefix.startswith("#EXTVLCOPT"):
                    colon_idx = s.find(':')
                    if colon_idx != -1:
                        data = s[colon_idx + 1:]
                        eq_idx = data.find('=')
                        if eq_idx != -1:
                            key = data[:eq_idx].strip().lower()
                            value = data[eq_idx + 1:].strip()
                            if key in {"catchup-source", "catchup_url"}:
                                catchup_source = value
                            elif key == "catchup-days":
                                catchup_days = value
                            elif key == "catchup-type":
                                catchup_type = value
                            elif key == "http-user-agent":
                                http_user_agent = value
                    continue

                if upper_prefix.startswith("#KODIPROP"):
                    colon_idx = s.find(':')
                    if colon_idx != -1:
                        data = s[colon_idx + 1:]
                        eq_idx = data.find('=')
                        if eq_idx != -1:
                            key = data[:eq_idx].strip().lower()
                            value = data[eq_idx + 1:].strip()
                            if key.endswith("catchup_days"):
                                catchup_days = value
                            elif key.endswith("catchup_source"):
                                catchup_source = value
                    continue

                # Other comment/directive lines are ignored
                continue

            url = s
            grp_value = group or extract_group_local(name)
            channel = {
                "name": name,
                "group": grp_value,
                "url": url,
                "tvg-id": tvg_id,
                "tvg-name": tvg_name,
            }
            if provider_id:
                channel["provider-id"] = provider_id
            if provider_type:
                channel["provider-type"] = provider_type
            if tvg_logo:
                channel["tvg-logo"] = tvg_logo
            if tvg_rec:
                channel["tvg-rec"] = tvg_rec
            if timeshift:
                channel["timeshift"] = timeshift
            if catchup:
                channel["catchup"] = catchup
            if catchup_type:
                channel["catchup-type"] = catchup_type
            if catchup_days:
                channel["catchup-days"] = catchup_days
            if catchup_source:
                channel["catchup-source"] = catchup_source
            if catchup_offset:
                channel["catchup-offset"] = catchup_offset
            if http_user_agent:
                channel["http-user-agent"] = http_user_agent

            if provider_type == "xtream" or catchup_source:
                stream_id = stream_id_for(url)
                if stream_id:
                    channel["stream-id"] = stream_id

            append(channel)

            # Clear state after emitting the channel entry
            name = ""
            group = ""
            tvg_id = ""
            tvg_name = ""
            tvg_logo = ""
            tvg_rec = ""
            timeshift = ""
            catchup = ""
            catchup_type = ""
            catchup_days = ""
            catchup_source = ""
            catchup_offset = ""
            http_user_agent = ""

        return out

    def _playlist_text_hash(self, text: str) -> str:
        if not text:
            return ""
        return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()

    def _parsed_cache_path_for_key(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()
        cache_dir = get_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"parsed_{digest}.json")

    def _load_cached_playlist(
        self,
        cache_path: str,
        text_hash: str,
        provider_meta: Optional[Dict[str, str]] = None,
    ) -> Optional[List[Dict[str, str]]]:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("hash") != text_hash:
                return None
            channels = data.get("channels")
            if not isinstance(channels, list):
                return None
            if provider_meta:
                pid = provider_meta.get("provider-id")
                ptype = provider_meta.get("provider-type")
                if pid or ptype:
                    for ch in channels:
                        if pid:
                            ch["provider-id"] = pid
                        if ptype:
                            ch["provider-type"] = ptype
            return channels
        except Exception:
            return None

    def _store_cached_playlist(
        self,
        cache_path: str,
        text_hash: str,
        channels: List[Dict[str, str]],
        provider_meta: Optional[Dict[str, str]] = None,
    ) -> None:
        payload = {"hash": text_hash, "channels": channels}
        if provider_meta:
            payload["provider"] = provider_meta
        tmp_path = cache_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp_path, cache_path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _extract_stream_id(self, url: str) -> str:
        try:
            path = urllib.parse.urlparse(url).path
        except Exception:
            path = ""
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ""
        last = parts[-1]
        m = re.match(r"(\d+)", last)
        if m:
            return m.group(1)
        if len(parts) >= 2:
            m = re.match(r"(\d+)", parts[-2])
            if m:
                return m.group(1)
        return ""

    def on_group_select(self):
        sel = self.group_list.GetSelection()
        label = self.group_list.GetString(sel) if sel != wx.NOT_FOUND else "All Channels"
        if label.startswith("All Channels"):
            grp = "All Channels"
        else:
            grp = label.split(" (", 1)[0]
        self.current_group = grp

        source = self.all_channels if grp == "All Channels" else self.channels_by_group.get(grp, [])
        self._populate_channel_list_chunked(source)

    def _populate_channel_list_chunked(self, source: List[Dict[str, str]]):
        self._populate_token += 1
        token = self._populate_token

        self.displayed = []
        self.channel_list.Freeze()
        try:
            self.channel_list.Clear()
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass

        total = len(source)
        if total == 0:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")
            return

        # For small lists, append in one shot for speed
        if total <= 1500:
            names = []
            for ch in source:
                self.displayed.append({"type": "channel", "data": ch})
                names.append(ch.get("name", ""))
            self.channel_list.Freeze()
            try:
                if names:
                    self.channel_list.AppendItems(names)
            finally:
                try:
                    self.channel_list.Thaw()
                except Exception:
                    pass
            self.channel_list.SetSelection(0)
            self.channel_list.SetFocus()
            self.on_highlight()
            return

        # For large lists, add in batches to keep UI responsive
        batch = 800 if total > 8000 else 500
        idx = 0

        # Provide a minimal immediate preview
        preview_end = min(200, total)
        preview_names = []
        for ch in source[:preview_end]:
            self.displayed.append({"type": "channel", "data": ch})
            preview_names.append(ch.get("name", ""))
        self.channel_list.Freeze()
        try:
            if preview_names:
                self.channel_list.AppendItems(preview_names)
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass
        self.channel_list.SetSelection(0)
        self.channel_list.SetFocus()
        self.on_highlight()
        idx = preview_end
        self.epg_display.SetValue(f"Loading channels… {idx}/{total}")

        def add_next_chunk():
            nonlocal idx
            if token != self._populate_token:
                return  # canceled due to group/filter change
            end = min(idx + batch, total)
            names = []
            for ch in source[idx:end]:
                self.displayed.append({"type": "channel", "data": ch})
                names.append(ch.get("name", ""))
            if names:
                self.channel_list.Freeze()
                try:
                    self.channel_list.AppendItems(names)
                finally:
                    try:
                        self.channel_list.Thaw()
                    except Exception:
                        pass
            idx = end
            if idx >= total or token != self._populate_token:
                if token == self._populate_token:
                    self.epg_display.SetValue("")
                return
            # update progress and schedule next chunk
            self.epg_display.SetValue(f"Loading channels… {idx}/{total}")
            wx.CallLater(30, add_next_chunk)  # 30ms between chunks keeps UI smooth

        wx.CallLater(30, add_next_chunk)

    def _fmt_time(self, s):
        # s: "YYYYMMDDHHMMSS" (UTC)
        try:
            dt = datetime.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            local = utc_to_local(dt)
            return local.strftime("%H:%M")
        except Exception:
            return "?"

    def _utc_now(self) -> datetime.datetime:
        try:
            return datetime.datetime.now(datetime.timezone.utc)
        except Exception:
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def _ensure_utc_dt(self, value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
        if not isinstance(value, datetime.datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)

    def _epg_cache_needs_refresh(self, now_show, next_show, cached_at: Optional[datetime.datetime]) -> bool:
        now_utc = self._utc_now()

        cached_utc = None
        if isinstance(cached_at, datetime.datetime):
            if cached_at.tzinfo is None:
                try:
                    # Assume legacy entries were stored as local time; best-effort convert to UTC.
                    cached_utc = cached_at.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    cached_utc = None
            else:
                cached_utc = cached_at.astimezone(datetime.timezone.utc)

        if cached_utc is None:
            return True

        if (now_utc - cached_utc).total_seconds() >= self._CACHE_REFRESH_AFTER_SECS:
            return True

        if not now_show and not next_show:
            # No guide yet; re-query soon so a subsequent provider import can populate it.
            if (now_utc - cached_utc).total_seconds() >= 30:
                return True

        if now_show:
            end_utc = self._ensure_utc_dt(now_show.get('end'))
            if end_utc and now_utc >= end_utc - datetime.timedelta(seconds=15):
                return True
        if not now_show and next_show:
            start_utc = self._ensure_utc_dt(next_show.get('start'))
            if start_utc and now_utc >= start_utc - datetime.timedelta(seconds=15):
                return True

        return False

    def on_highlight(self):
        # Allow viewing cached or currently available EPG even while an import is running.
        i = self.channel_list.GetSelection()
        if i < 0 or i >= len(self.displayed):
            self.url_display.SetValue("")
            return
        item = self.displayed[i]
        if item["type"] == "channel":
            ch = item["data"]
            self.url_display.SetValue(ch.get("url", ""))
            cname = ch.get("name", "")

            if not self.config.get("epg_enabled", True):
                self.epg_display.SetValue("EPG is disabled in configuration.")
                return

            self._start_epg_poll_timer()

            # If this channel is exempt (likely has no EPG), show a clear message and do not fetch.
            if self._channel_is_epg_exempt(ch):
                self.epg_display.SetValue("No EPG data for this channel.")
                return

            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                needs_refresh = self._epg_cache_needs_refresh(now_show, next_show, ts)
                if needs_refresh:
                    with self._epg_inflight_lock:
                        if key not in self._epg_fetch_inflight:
                            threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
                msg = self._epg_msg_from_tuple(now_show, next_show)
                if needs_refresh:
                    msg += "\n\nUpdating EPG..."
                # If an import is running, indicate that data may still be arriving.
                if self.epg_importing:
                    msg = msg + "\n\nNote: EPG import in progress — newer program data may still arrive."
                self.epg_display.SetValue(msg)
            else:
                # No cached entry: fetch what exists now (reader connection to DB).
                with self._epg_inflight_lock:
                    already = canonicalize_name(cname) in self._epg_fetch_inflight
                    if not already:
                        threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
                # Provide placeholder while we wait for DB read.
                placeholder = "Loading EPG for this channel…"
                if self.epg_importing:
                    placeholder += "\n\nEPG import in progress — displaying available data as it arrives."
                self.epg_display.SetValue(placeholder)
        elif item["type"] == "epg":
            self.url_display.SetValue("")
            r = item["data"]
            url = ""
            for ch in self.all_channels:
                if canonicalize_name(ch["name"]) == canonicalize_name(r["channel_name"]):
                    url = ch.get("url", "")
                    break
            msg = (
                f"Show: {r['show_title']} | Channel: {r['channel_name']} | "
                f"Start: {self._fmt_time(r['start'])} | End: {self._fmt_time(r['end'])}"
            )
            if self.epg_importing:
                msg = msg + "\n\nNote: EPG import in progress — data may still be updating."
            self.epg_display.SetValue(msg)
            self.url_display.SetValue(url)

    def _epg_msg_from_tuple(self, now, nxt):
        def localfmt(dt):
            local = utc_to_local(dt)
            return local.strftime('%H:%M')
        msg = ""
        if now:
            msg += f"Now: {now['title']} ({localfmt(now['start'])} – {localfmt(now['end'])})"
        elif nxt:
            msg += f"Starts at {localfmt(nxt['start'])}: {nxt['title']}"
        else:
            msg += "No program currently airing."
        if nxt:
            msg += f"\nNext: {nxt['title']} ({localfmt(nxt['start'])} – {localfmt(nxt['end'])})"
        return msg

    def _fetch_and_cache_epg(self, channel, cname):
        key = canonicalize_name(cname)
        # Deduplicate inflight fetches per canonical name to avoid heavy repeated scoring during import.
        with self._epg_inflight_lock:
            if key in self._epg_fetch_inflight:
                return
            self._epg_fetch_inflight.add(key)
        try:
            try:
                if self._channel_is_epg_exempt(channel):
                    now_next = None  # Do not query DB for exempt channels
                else:
                    db = EPGDatabase(get_db_path(), readonly=True)
                    now_next = db.get_now_next(channel)
                    db.close()
            except Exception:
                now_next = None
        finally:
            # Ensure we always remove from inflight even if DB access raised.
            with self._epg_inflight_lock:
                try:
                    self._epg_fetch_inflight.discard(key)
                except Exception:
                    pass

        if not now_next:
            now_show, next_show = None, None
        else:
            now_show, next_show = now_next
            
        with self.epg_cache_lock:
            self.epg_cache[canonicalize_name(cname)] = (now_show, next_show, self._utc_now())
        wx.CallAfter(self._update_epg_display_if_selected, channel, now_show, next_show)

    def _update_epg_display_if_selected(self, channel, now_show, next_show):
        i = self.channel_list.GetSelection()
        if 0 <= i < len(self.displayed):
            item = self.displayed[i]
            if item["type"] == "channel" and canonicalize_name(item["data"].get("name", "")) == canonicalize_name(channel.get("name", "")):
                if self._channel_is_epg_exempt(channel) and not (now_show or next_show):
                    msg = "No EPG data for this channel."
                else:
                    msg = self._epg_msg_from_tuple(now_show, next_show)
                if self.epg_importing:
                    msg = msg + "\n\nNote: EPG import in progress — newer program data may still arrive."
                self.epg_display.SetValue(msg)

    def _start_epg_poll_timer(self):
        try:
            if self._epg_poll_timer:
                return
            self._epg_poll_timer = wx.Timer(self)
            # Bind with timer as source so we can unbind cleanly later
            self.Bind(wx.EVT_TIMER, self._on_epg_poll_timer, self._epg_poll_timer)
            # Poll less aggressively to avoid repeated expensive matching while importer churns.
            self._epg_poll_timer.Start(8000, wx.TIMER_CONTINUOUS)  # 8s
        except Exception:
            self._epg_poll_timer = None

    def _stop_epg_poll_timer(self):
        try:
            if self._epg_poll_timer:
                try:
                    self._epg_poll_timer.Stop()
                except Exception:
                    pass
                # Unbind the specific handler for this timer source to avoid removing other EVT_TIMER bindings.
                try:
                    # Unbind signature: Unbind(event, source=timer, handler=callable)
                    self.Unbind(wx.EVT_TIMER, handler=self._on_epg_poll_timer, source=self._epg_poll_timer)
                except Exception:
                    # Fallback: attempt to unbind by event only (best-effort)
                    try:
                        self.Unbind(wx.EVT_TIMER, handler=self._on_epg_poll_timer)
                    except Exception:
                        pass
                self._epg_poll_timer = None
        except Exception:
            self._epg_poll_timer = None

    def _on_epg_poll_timer(self, event):
        # Only refresh the currently highlighted channel (cheap, targeted).
        try:
            i = self.channel_list.GetSelection()
            if i < 0 or i >= len(self.displayed):
                return
            item = self.displayed[i]
            if item["type"] != "channel":
                return
            ch = item["data"]
            # Skip channels that likely have no EPG to avoid repeated DB probes/log spam.
            if self._channel_is_epg_exempt(ch):
                return
            cname = ch.get("name", "")
            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                if not self._epg_cache_needs_refresh(now_show, next_show, ts):
                    return
            else:
                now_show = next_show = ts = None
            # Only spawn a refresh if one isn't already running for this channel.
            with self._epg_inflight_lock:
                already = key in self._epg_fetch_inflight
            if not already:
                threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
        except Exception:
            pass

    def _find_channel_for_epg(self, show: Dict[str, str]) -> Optional[Dict[str, str]]:
        cname = show.get("channel_name", "")
        if not cname:
            return None
        canonical = canonicalize_name(cname)
        for ch in self.all_channels:
            if canonicalize_name(ch.get("name", "")) == canonical:
                return ch
        return None

    def _channel_has_catchup(self, channel: Dict[str, str]) -> bool:
        if channel.get("catchup-source") or channel.get("catchup"):
            return True
        if channel.get("provider-type") == "stalker":
            pdata = channel.get("provider-data") or {}
            return bool(pdata.get("allow_timeshift") or pdata.get("archive"))
        return False

    def _parse_epg_time(self, value: str) -> datetime.datetime:
        dt = datetime.datetime.strptime(value, "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=datetime.timezone.utc)

    def _resolve_show_url(self, channel: Dict[str, str], show: Dict[str, str]) -> tuple:
        start_dt = self._parse_epg_time(show.get("start"))
        end_dt = self._parse_epg_time(show.get("end"))
        now = datetime.datetime.now(datetime.timezone.utc)

        if start_dt <= now <= end_dt:
            return self._resolve_live_url(channel), False

        if end_dt < now:
            if not self._channel_has_catchup(channel):
                raise ProviderError("This channel does not provide catch-up streaming.")
            if not self._within_catchup_window(channel, start_dt):
                raise ProviderError("This programme is older than the catch-up window allows.")
            url = self._resolve_catchup_url(channel, start_dt, end_dt)
            if not url:
                raise ProviderError("Unable to construct catch-up URL for this programme.")
            return url, True

        # Future programme: return live stream so playback starts when available.
        return self._resolve_live_url(channel), False

    def _within_catchup_window(self, channel: Dict[str, str], start_dt: datetime.datetime) -> bool:
        days = channel.get("catchup-days")
        if not days:
            return True
        try:
            span = int(float(days))
        except (TypeError, ValueError):
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        return start_dt >= now - datetime.timedelta(days=span)

    def _resolve_live_url(self, channel: Dict[str, str]) -> str:
        url = channel.get("url", "")
        provider_type = channel.get("provider-type")
        provider_id = channel.get("provider-id")

        if provider_type == "stalker":
            if not provider_id:
                raise ProviderError("Stalker portal entry missing provider identifier.")
            client = self.provider_clients.get(provider_id)
            if not client:
                raise ProviderError("Stalker portal client is not initialized.")
            pdata = channel.get("provider-data") or {}
            url = client.resolve_stream(pdata)
            return url

        return url

    def _resolve_catchup_url(self, channel: Dict[str, str], start_dt: datetime.datetime, end_dt: datetime.datetime) -> str:
        provider_type = channel.get("provider-type")
        provider_id = channel.get("provider-id")
        if provider_type == "stalker" and provider_id:
            client = self.provider_clients.get(provider_id)
            if not client:
                raise ProviderError("Stalker portal client is not initialized.")
            pdata = channel.get("provider-data") or {}
            start_local = utc_to_local(start_dt)
            duration = max(1, int((end_dt - start_dt).total_seconds() // 60))
            start_str = start_local.strftime("%Y-%m-%d:%H-%M")
            return client.resolve_catchup(pdata, start_str, duration)

        return self._build_generic_catchup_url(channel, start_dt, end_dt)

    def _build_generic_catchup_url(self, channel: Dict[str, str], start_dt: datetime.datetime, end_dt: datetime.datetime) -> str:
        source = channel.get("catchup-source") or ""
        if not source:
            return ""
        stream_id = channel.get("stream-id") or self._extract_stream_id(channel.get("url", ""))
        if not stream_id:
            return ""

        src = source.rstrip('/')
        if not src:
            return ""
        last_segment = src.rsplit('/', 1)[-1]
        if not last_segment.isdigit():
            src = f"{src}/{stream_id}"

        start_local = utc_to_local(start_dt)
        end_local = utc_to_local(end_dt)
        offset = channel.get("catchup-offset")
        try:
            if offset:
                hours = float(offset)
                delta = datetime.timedelta(hours=hours)
                start_local -= delta
                end_local -= delta
        except (TypeError, ValueError):
            pass

        duration = max(1, int((end_local - start_local).total_seconds() // 60))
        start_token = start_local.strftime("%Y-%m-%d:%H-%M")
        ctype = (channel.get("catchup-type") or channel.get("catchup") or "xc").lower()
        if ctype in {"", "xc", "default", "catchup"}:
            url = f"{src}/{start_token}/{duration}/"
        elif ctype == "flussonic":
            archive_stamp = start_local.strftime("%Y%m%d%H%M%S")
            url = f"{src}/{archive_stamp}-{duration}.m3u8"
        else:
            url = f"{src}/{start_token}/{duration}/"

        ua = channel.get("http-user-agent")
        if ua:
            if "|" in url:
                url = f"{url}&User-Agent={urllib.parse.quote(ua)}"
            else:
                url = f"{url}|User-Agent={urllib.parse.quote(ua)}"
        return url

    def _spawn_posix(self, argv):
        try:
            subprocess.Popen(argv, close_fds=True)
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {argv[0]}"
        except Exception as e:
            return False, str(e)

    def _spawn_windows(self, argv):
        """Launch player on Windows with a normal, visible window."""
        try:
            subprocess.Popen(argv)
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {argv[0]}"
        except Exception as e:
            return False, str(e)

    def _mpv_ipc_path(self) -> str:
        if platform.system() == "Windows":
            return r"\\.\pipe\iptvclient-mpv"
        # POSIX: use a socket in temp
        return os.path.join(tempfile.gettempdir(), "iptvclient-mpv.sock")

    def _mpv_try_send(self, url: str) -> bool:
        """If an mpv instance with our IPC is running, send loadfile and return True."""
        ipc = self._mpv_ipc_path()
        payload = (json.dumps({"command": ["loadfile", url, "replace"]}) + "\n").encode("utf-8")
        if platform.system() == "Windows":
            try:
                # Minimal win32 named pipe client via ctypes
                from ctypes import wintypes
                GENERIC_WRITE = 0x40000000
                OPEN_EXISTING = 3
                INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
                CreateFileW = ctypes.windll.kernel32.CreateFileW
                CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                CreateFileW.restype = wintypes.HANDLE
                h = CreateFileW(ipc, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
                if h == 0 or h == INVALID_HANDLE_VALUE:
                    return False
                try:
                    WriteFile = ctypes.windll.kernel32.WriteFile
                    WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
                    written = wintypes.DWORD(0)
                    ok = WriteFile(h, payload, len(payload), ctypes.byref(written), None)
                    return bool(ok and written.value == len(payload))
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                return False
        else:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.25)
                s.connect(ipc)
                s.sendall(payload)
                s.close()
                return True
            except Exception:
                return False

    def play_selected(self):
        i = self.channel_list.GetSelection()
        if not (0 <= i < len(self.displayed)):
            return
        item = self.displayed[i]

        channel = None
        show = None
        if item["type"] == "channel":
            channel = item["data"]
        elif item["type"] == "epg":
            show = item["data"]
            channel = self._find_channel_for_epg(show)
            if not channel:
                wx.MessageBox("Could not match this programme to a playlist channel.",
                              "Not Found", wx.OK | wx.ICON_WARNING)
                return
        else:
            return

        try:
            if show:
                url, _ = self._resolve_show_url(channel, show)
            else:
                url = self._resolve_live_url(channel)
        except ProviderError as err:
            wx.MessageBox(f"Provider error: {err}", "Playback Error", wx.OK | wx.ICON_ERROR)
            return
        except Exception as err:
            wx.MessageBox(f"Could not resolve stream URL:\n{err}", "Playback Error", wx.OK | wx.ICON_ERROR)
            return

        display_name = None
        if channel:
            display_name = (channel.get("name")
                            or channel.get("tvg-name")
                            or channel.get("tvg_name")
                            or channel.get("tvg-id")
                            or channel.get("tvg_id"))
        if show:
            show_title = show.get("show_title") or show.get("title")
            if show_title:
                if display_name:
                    display_name = f"{show_title} - {display_name}"
                else:
                    display_name = show_title
        if not display_name:
            display_name = "IPTV Stream"
        self._launch_stream(url, display_name)

    def _on_internal_player_closed(self) -> None:
        self._internal_player_frame = None

    def _ensure_internal_player(self) -> InternalPlayerFrame:
        if InternalPlayerFrame is None:
            detail = _VLC_IMPORT_ERROR or "Built-in player is unavailable."
            raise InternalPlayerUnavailableError(str(detail))
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            try:
                if getattr(frame, "_destroyed", False):
                    frame = None
            except Exception:
                frame = None
        if frame:
            return frame
        try:
            base_buffer = float(self.config.get("internal_player_buffer_seconds", 12.0))
        except Exception:
            base_buffer = 12.0
        frame = InternalPlayerFrame(self, base_buffer_seconds=base_buffer, on_close=self._on_internal_player_closed)
        self._internal_player_frame = frame
        return frame

    def _launch_stream(self, url: str, title: Optional[str] = None):
        if not url:
            wx.MessageBox("Could not find stream URL for this selection.", "Not Found",
                          wx.OK | wx.ICON_WARNING)
            return

        player = self.default_player
        custom_path = self.config.get("custom_player_path", "")

        if player in {"Built-in Player", "player_Internal", "internal", "Internal"}:
            player = "Built-in Player"
            try:
                frame = self._ensure_internal_player()
            except InternalPlayerUnavailableError as err:
                detail = str(err)
                wx.MessageBox(f"Built-in player unavailable:\n{detail}", "Launch Error", wx.OK | wx.ICON_ERROR)
                return
            display_title = title or "IPTV Stream"
            try:
                frame.Show()
                frame.Raise()
                frame.SetFocus()
                frame.play(url, display_title)
            except Exception as err:
                wx.MessageBox(f"Failed to start built-in player:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)
            return

        # If using mpv, try to reuse an existing instance via IPC first.
        if player == "MPV":
            try:
                if self._mpv_try_send(url):
                    return
            except Exception:
                pass

        # Guard against accidental double-invocation (applies to spawn path only)
        try:
            if not hasattr(self, "_launch_guard_lock"):
                self._launch_guard_lock = threading.Lock()
                self._last_launch_ts = 0.0
            with getattr(self, "_launch_guard_lock"):
                now = time.time()
                if (now - getattr(self, "_last_launch_ts", 0.0)) < 0.75:
                    return
                self._last_launch_ts = now
        except Exception:
            pass

        win_paths = {
            "VLC": [r"C:\Program Files\VideoLAN\VLC\vlc.exe", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"],
            "MPC": [r"C:\Program Files\MPC-HC\mpc-hc64.exe", r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe", r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe"],
            "MPC-BE": [r"C:\Program Files\MPC-BE\mpc-be64.exe", r"C:\Program Files (x86)\MPC-BE\mpc-be.exe", r"C:\Program Files\MPC-BE x64\mpc-be64.exe", r"C:\Program Files\MPC-BE\mpc-be.exe"],
            "Kodi": [r"C:\Program Files\Kodi\kodi.exe"],
            "Winamp": [r"C:\Program Files\Winamp\winamp.exe"],
            "Foobar2000": [r"C:\Program Files\foobar2000\foobar2000.exe"],
            "MPV": [r"C:\Program Files\mpv\mpv.exe", r"C:\Program Files (x86)\mpv\mpv.exe"],
            "SMPlayer": [r"C:\Program Files\SMPlayer\smplayer.exe", r"C:\Program Files (x86)\SMPlayer\smplayer.exe"],
            "QuickTime": [r"C:\Program Files\QuickTime\QuickTimePlayer.exe", r"C:\Program Files (x86)\QuickTime\QuickTimePlayer.exe"],
            "iTunes/Apple Music": [r"C:\Program Files\iTunes\iTunes.exe", r"C:\Program Files (x86)\iTunes\iTunes.exe", r"C:\Program Files\Apple\Music\AppleMusic.exe"],
            "PotPlayer": [r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe", r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe"],
            "KMPlayer": [r"C:\Program Files\KMP Media\KMPlayer\KMPlayer64.exe", r"C:\Program Files (x86)\KMP Media\KMPlayer\KMPlayer.exe"],
            "AIMP": [r"C:\Program Files\AIMP\AIMP.exe", r"C:\Program Files (x86)\AIMP\AIMP.exe"],
            "QMPlay2": [r"C:\Program Files\QMPlay2\QMPlay2.exe", r"C:\Program Files (x86)\QMPlay2\QMPlay2.exe"],
            "GOM Player": [r"C:\Program Files\GRETECH\GomPlayer\GOM.exe", r"C:\Program Files (x86)\GRETECH\GomPlayer\GOM.exe"],
            "Clementine": [r"C:\Program Files\Clementine\clementine.exe"],
            "Strawberry": [r"C:\Program Files\Strawberry\strawberry.exe"],
        }
        
        linux_players = { "VLC": "vlc", "MPV": "mpv", "Kodi": "kodi", "SMPlayer": "smplayer", "Totem": "totem", "PotPlayer": "potplayer", "KMPlayer": "kmplayer", "AIMP": "aimp", "QMPlay2": "qmplay2", "GOM Player": "gomplayer", "Audacious": "audacious", "Fauxdacious": "fauxdacious", "MPC-BE": "mpc-be", "Clementine": "clementine", "Strawberry": "strawberry", "Amarok": "amarok", "Rhythmbox": "rhythmbox", "Pragha": "pragha", "Lollypop": "lollypop", "Exaile": "exaile", "Quod Libet": "quodlibet", "Gmusicbrowser": "gmusicbrowser", "Xmms": "xmms", "Vocal": "vocal", "Haruna": "haruna", "Celluloid": "celluloid" }
        mac_paths = { "VLC": ["/Applications/VLC.app/Contents/MacOS/VLC"], "QuickTime": ["/Applications/QuickTime Player.app/Contents/MacOS/QuickTime Player"], "iTunes/Apple Music": ["/Applications/Music.app/Contents/MacOS/Music", "/Applications/iTunes.app/Contents/MacOS/iTunes"], "QMPlay2": ["/Applications/QMPlay2.app/Contents/MacOS/QMPlay2"], "Audacious": ["/Applications/Audacious.app/Contents/MacOS/Audacious"], "Fauxdacious": ["/Applications/Fauxdacious.app/Contents/MacOS/Fauxdacious"] }

        ok, err = False, ""

        def _argv_for(player_name: str, exe_or_cmd: str, is_windows: bool) -> list:
            # Build argv with best-effort single-instance/enqueue flags where supported.
            if player_name == "VLC":
                # Use single-instance flags, but do NOT enqueue so the new stream replaces playback.
                flags = ["--one-instance", "--one-instance-when-started-from-file"]
                return [exe_or_cmd, *flags, url]
            if player_name in ("MPC", "MPC-BE"):
                # Keep it simple and robust: let MPC's own single-instance setting
                # handle reuse; always pass the URL directly for predictable playback.
                return [exe_or_cmd, url]
            if player_name == "MPV":
                ipc = self._mpv_ipc_path()
                flags = [f"--input-ipc-server={ipc}", "--force-window=yes", "--idle=yes", "--no-terminal"]
                # On POSIX, if the IPC socket path exists but connect failed above, it's likely stale; try to remove it.
                if not is_windows and os.path.exists(ipc):
                    try:
                        os.unlink(ipc)
                    except Exception:
                        pass
                return [exe_or_cmd, *flags, url]
            # Default: just pass URL
            return [exe_or_cmd, url]
        if self.default_player == "Custom" and custom_path:
            exe = custom_path
            # Heuristically detect known players from custom path for better flags
            pname = os.path.basename(exe).lower()
            detected = player
            if "mpv" in pname:
                detected = "MPV"
            elif "vlc" in pname:
                detected = "VLC"
            elif "mpc-be" in pname or "mpcbe" in pname:
                detected = "MPC-BE"
            elif pname.startswith("mpc-hc") or pname.startswith("mpc"):
                detected = "MPC"
            argv = _argv_for(detected, exe, platform.system() == "Windows")
            ok, err = self._spawn_windows(argv) if platform.system() == "Windows" else self._spawn_posix(argv)
        else:
            system = platform.system()
            if system == "Windows":
                choices = win_paths.get(player, [])
                for exe in choices:
                    if os.path.exists(exe):
                        argv = _argv_for(player, exe, True)
                        ok, err = self._spawn_windows(argv)
                        if ok: break
                if not ok: err = err or f"Could not locate {player} executable."
            elif system == "Darwin":
                choices = mac_paths.get(player, [])
                for exe in choices:
                    if os.path.exists(exe):
                        argv = _argv_for(player, exe, False)
                        ok, err = self._spawn_posix(argv)
                        if ok: break
                if not ok: err = err or f"Could not locate {player} app."
            else:
                cmd = linux_players.get(player)
                if cmd:
                    argv = _argv_for(player, cmd, False)
                    ok, err = self._spawn_posix(argv)
                else: err = f"{player} is not configured for Linux."

        if not ok:
            wx.MessageBox(f"Failed to launch {self.default_player}:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)

    def _open_catchup_dialog(self, channel: Dict[str, str]):
        programmes = self._get_catchup_programmes(channel)
        if not programmes:
            wx.MessageBox("No catch-up programmes are available for this channel.",
                          "Catch-up", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = CatchupDialog(self, channel.get("name", ""), programmes)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                selected = dlg.get_selection()
                if not selected:
                    return
                show = {
                    "channel_id": selected.get("channel_id", ""),
                    "channel_name": channel.get("name", selected.get("channel_name", "")),
                    "show_title": selected.get("title", ""),
                    "start": selected.get("start", ""),
                    "end": selected.get("end", "")
                }
                try:
                    url, _ = self._resolve_show_url(channel, show)
                except ProviderError as err:
                    wx.MessageBox(f"Provider error: {err}", "Catch-up", wx.OK | wx.ICON_ERROR)
                    return
                except Exception as err:
                    wx.MessageBox(f"Unable to prepare catch-up stream:\n{err}", "Catch-up", wx.OK | wx.ICON_ERROR)
                    return
                display = (selected.get("title") or channel.get("name", "IPTV Stream"))
                self._launch_stream(url, display)
        finally:
            dlg.Destroy()

    def _get_catchup_programmes(self, channel: Dict[str, str]) -> List[Dict[str, str]]:
        try:
            db = EPGDatabase(get_db_path(), readonly=True)
            try:
                programmes = db.get_recent_programmes(channel, hours=72, limit=80)
            finally:
                db.close()
        except Exception:
            programmes = []
        return programmes


class CatchupDialog(wx.Dialog):
    def __init__(self, parent, channel_name: str, programmes: List[Dict[str, str]]):
        title = channel_name or "Catch-up"
        super().__init__(parent, title=f"Catch-up: {title}", size=(520, 360))
        self.programmes = programmes
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(panel, label="Select a programme to play from catch-up:")
        self.listbox = wx.ListBox(panel, style=wx.LB_SINGLE)
        for prog in programmes:
            self.listbox.Append(self._format_programme(prog))
        if programmes:
            self.listbox.SetSelection(0)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, id=wx.ID_OK, label="Play")
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL)
        btn_sizer.Add(ok_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(intro, 0, wx.ALL, 10)
        sizer.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self._on_listbox_activate)
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.SetMinSize((420, 320))
        self.Layout()
        self.CenterOnParent()

    def _format_programme(self, prog: Dict[str, str]) -> str:
        try:
            start = datetime.datetime.strptime(prog.get("start", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            end = datetime.datetime.strptime(prog.get("end", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            start_local = utc_to_local(start)
            end_local = utc_to_local(end)
            window = f"{start_local.strftime('%Y-%m-%d %H:%M')} – {end_local.strftime('%H:%M')}"
        except Exception:
            window = prog.get("start", "")
        title = prog.get("title", "") or "(No title)"
        return f"{window}  |  {title}"

    def _on_listbox_activate(self, _):
        if self.programmes:
            self.EndModal(wx.ID_OK)

    def _on_ok(self, event):
        if self.listbox.GetSelection() == wx.NOT_FOUND and self.programmes:
            self.listbox.SetSelection(0)
        if self.listbox.GetSelection() == wx.NOT_FOUND:
            return
        self.EndModal(wx.ID_OK)

    def get_selection(self) -> Optional[Dict[str, str]]:
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.programmes):
            return None
        return self.programmes[idx]

if __name__ == "__main__":
    app = wx.App()
    app.SetAppName("IPTVClient")
    IPTVClient()
    app.MainLoop()

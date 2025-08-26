import os
import sys
import json
import urllib.request
import gzip
import io
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

import wx.adv

from options import (
    load_config, save_config, get_cache_path_for_url, get_cache_dir,
    get_db_path, canonicalize_name, relaxed_name, extract_group, utc_to_local,
    CustomPlayerDialog
)
from playlist import (
    EPGDatabase, EPGImportDialog, EPGManagerDialog, PlaylistManagerDialog
)

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
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.on_taskbar_activate)
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
        self.on_restore()

    def on_menu_select(self, event):
        eid = event.GetId()
        if eid == self.TBMENU_RESTORE:
            self.on_restore()
        elif eid == self.TBMENU_EXIT:
            self.on_exit()

class IPTVClient(wx.Frame):
    PLAYER_KEYS = [
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
    _CACHE_REFRESH_AFTER_SECS = 30

    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))
        self.config = load_config()
        self.playlist_sources = self.config.get("playlists", [])
        self.epg_sources = self.config.get("epgs", [])
        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.displayed: List[Dict[str, str]] = []
        self.current_group = "All Channels"
        self.default_player = self.config.get("media_player", "VLC")
        self.custom_player_path = self.config.get("custom_player_path", "")
        self.epg_importing = False
        self.epg_cache = {}
        self.epg_cache_lock = threading.Lock()
        self.refresh_timer = None
        self.minimize_to_tray = bool(self.config.get("minimize_to_tray", False))
        self.tray_icon = None

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
        defplayer = self.config.get("media_player", "VLC")
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
        channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        all_channels: List[Dict[str, str]] = []
        valid_caches = set()
        seen_channel_keys = set()
        results = [None] * len(playlist_sources)

        def fetch_playlist(idx, src):
            text = None
            try:
                if src.startswith(("http://", "https://")):
                    cache_path = get_cache_path_for_url(src)
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
                            try: text = raw.decode("utf-8")
                            except UnicodeDecodeError: text = raw.decode("latin-1", "ignore")
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(text)
                    else:
                        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                else:
                    if os.path.exists(src):
                        with open(src, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                results[idx] = text
            except Exception:
                results[idx] = None
        
        threads = []
        for idx, src in enumerate(playlist_sources):
            t = threading.Thread(target=fetch_playlist, args=(idx, src), daemon=True)
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()

        for text in results:
            if not text: continue
            try:
                channels = self._parse_m3u_return(text)
                for ch in channels:
                    key = (ch.get("name", ""), ch.get("url", ""))
                    if key in seen_channel_keys: continue
                    seen_channel_keys.add(key)
                    grp = ch.get("group") or "Uncategorized"
                    channels_by_group.setdefault(grp, []).append(ch)
                    all_channels.append(ch)
            except Exception:
                continue

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

    def on_toggle_min_to_tray(self, event):
        if platform.system() == "Linux":
            self.minimize_to_tray = not self.minimize_to_tray
        else:
            self.minimize_to_tray = self.min_to_tray_item.IsChecked()
        self.config["minimize_to_tray"] = self.minimize_to_tray
        save_config(self.config)

    def show_tray_icon(self):
        if not self.tray_icon:
            self.tray_icon = TrayIcon(
                self,
                on_restore=self.restore_from_tray,
                on_exit=self.exit_from_tray
            )
        self.Hide()

    def restore_from_tray(self):
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
        if self.tray_icon:
            try:
                self.tray_icon.RemoveIcon()
            except Exception:
                pass
            self.tray_icon.Destroy()
            self.tray_icon = None
        self.Destroy()

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
                if txt in ch.get("name", "").lower():
                    self.displayed.append({"type": "channel", "data": ch})
                    items.append(ch.get("name", ""))

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
                    if results:
                        add_items = []
                        for r in results:
                            label = f"{r['channel_name']} - {r['show_title']} ({self._fmt_time(r['start'])}–{self._fmt_time(r['end'])})"
                            self.displayed.append({"type": "epg", "data": r})
                            add_items.append(label)
                        if add_items:
                            self.channel_list.AppendItems(add_items)
                    if self.displayed:
                        self.channel_list.SetSelection(0)
                        self.channel_list.SetFocus()
                        self.on_highlight()
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
        self.epg_sources = self.config.get("epgs", [])

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
        # Stop polling for incremental updates
        wx.CallAfter(self._stop_epg_poll_timer)
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()

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
        wx.CallAfter(self._stop_epg_poll_timer)
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()

    def _parse_m3u_return(self, text):
        lines = text.splitlines()
        out = []
        meta = {}
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if s.upper().startswith("#EXTINF"):
                meta = {"name": "", "group": "", "tvg-id": "", "tvg-name": ""}
                if "," in s:
                    meta["name"] = s.split(",", 1)[1].strip()
                attrs_part = s.split(":", 1)[1] if ":" in s else ""
                attr_segment = attrs_part.split(",", 1)[0]
                for key, dst in (("group-title", "group"), ("tvg-id", "tvg-id"), ("tvg-name", "tvg-name")):
                    m = re.search(r'%s="([^"]+)"' % key, attr_segment, flags=re.I)
                    if not m:
                        m = re.search(r"%s=([^,]+)" % key, attr_segment, flags=re.I)
                    if m:
                        meta[dst] = m.group(1).strip()
            elif s.startswith("#"):
                continue
            else:
                url = s
                name = meta.get("name", "")
                grp = meta.get("group", "") or extract_group(name)
                out.append({"name": name, "group": grp, "url": url, "tvg-id": meta.get("tvg-id", ""), "tvg-name": meta.get("tvg-name", "")})
                meta = {}
        return out

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

            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                # If cached data is stale, refresh in background but still show it immediately.
                if (datetime.datetime.now() - ts).total_seconds() >= self._CACHE_SHOW_STALE_SECS:
                    # Only spawn refresh if not already inflight
                    with self._epg_inflight_lock:
                        if key not in self._epg_fetch_inflight:
                            threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
                msg = self._epg_msg_from_tuple(now_show, next_show)
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
            self.epg_cache[canonicalize_name(cname)] = (now_show, next_show, datetime.datetime.now())
        wx.CallAfter(self._update_epg_display_if_selected, channel, now_show, next_show)

    def _update_epg_display_if_selected(self, channel, now_show, next_show):
        i = self.channel_list.GetSelection()
        if 0 <= i < len(self.displayed):
            item = self.displayed[i]
            if item["type"] == "channel" and canonicalize_name(item["data"].get("name", "")) == canonicalize_name(channel.get("name", "")):
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
            cname = ch.get("name", "")
            key = canonicalize_name(cname)
            # Only spawn a refresh if one isn't already running for this channel.
            with self._epg_inflight_lock:
                already = key in self._epg_fetch_inflight
            if not already:
                threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
        except Exception:
            pass

    def _spawn_posix(self, exe_path_or_cmd, url):
        try:
            subprocess.Popen([exe_path_or_cmd, url], close_fds=True)
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {exe_path_or_cmd}"
        except Exception as e:
            return False, str(e)

    def _spawn_windows(self, exe_path, url):
        """Launch player on Windows with a normal, visible window."""
        try:
            subprocess.Popen([exe_path, url])
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {exe_path}"
        except Exception as e:
            return False, str(e)

    def play_selected(self):
        i = self.channel_list.GetSelection()
        if not (0 <= i < len(self.displayed)):
            return
        item = self.displayed[i]
        url = ""
        if item["type"] == "channel":
            url = item["data"].get("url", "")
        elif item["type"] == "epg":
            chname = item["data"]["channel_name"]
            for ch in self.all_channels:
                if canonicalize_name(ch["name"]) == canonicalize_name(chname):
                    url = ch.get("url", "")
                    break
        if not url:
            wx.MessageBox("Could not find stream URL for this show.", "Not Found",
                          wx.OK | wx.ICON_WARNING)
            return

        player = self.default_player
        custom_path = self.config.get("custom_player_path", "")

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
        if self.default_player == "Custom" and custom_path:
            exe = custom_path
            ok, err = self._spawn_windows(exe, url) if platform.system() == "Windows" else self._spawn_posix(exe, url)
        else:
            system = platform.system()
            if system == "Windows":
                choices = win_paths.get(player, [])
                for exe in choices:
                    if os.path.exists(exe):
                        ok, err = self._spawn_windows(exe, url)
                        if ok: break
                if not ok: err = err or f"Could not locate {player} executable."
            elif system == "Darwin":
                choices = mac_paths.get(player, [])
                for exe in choices:
                    if os.path.exists(exe):
                        ok, err = self._spawn_posix(exe, url)
                        if ok: break
                if not ok: err = err or f"Could not locate {player} app."
            else:
                cmd = linux_players.get(player)
                if cmd: ok, err = self._spawn_posix(cmd, url)
                else: err = f"{player} is not configured for Linux."

        if not ok:
            wx.MessageBox(f"Failed to launch {self.default_player}:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)

if __name__ == "__main__":
    app = wx.App()
    app.SetAppName("IPTVClient")
    IPTVClient()
    app.MainLoop()

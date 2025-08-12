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

    # cache freshness policy
    _CACHE_SHOW_STALE_SECS = 600   # show stale up to 10 minutes
    _CACHE_REFRESH_AFTER_SECS = 30 # trigger background refresh if older than 30s

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

        # Make the DB non-blocking for reads before we start anything else
        self._ensure_db_tuned()

        self._build_ui()
        self.Centre()
        self.reload_all_sources_initial()
        self.reload_epg_sources()
        self.Show()
        self.start_epg_import_background()
        self.start_refresh_timer()
        self.Bind(wx.EVT_ICONIZE, self.on_minimize)
        self.Bind(wx.EVT_CLOSE, self.on_close)

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
        self.reload_all_sources_initial()
        self.reload_epg_sources()
        self.start_epg_import_background()

    def reload_all_sources_initial(self):
        self.playlist_sources = self.config.get("playlists", [])
        self.channels_by_group.clear()
        self.all_channels.clear()
        valid_caches = set()
        seen_channel_keys = set()
        for src in self.playlist_sources:
            try:
                if src.startswith(("http://", "https://")):
                    cache_path = get_cache_path_for_url(src)
                    valid_caches.add(cache_path)
                    if os.path.exists(cache_path):
                        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    else:
                        continue
                else:
                    if os.path.exists(src):
                        with open(src, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    else:
                        continue
                channels = self._parse_m3u_return(text)
                for ch in channels:
                    key = (ch.get("name", ""), ch.get("url", ""))
                    if key in seen_channel_keys:
                        continue
                    seen_channel_keys.add(key)
                    grp = ch.get("group") or "Uncategorized"
                    self.channels_by_group.setdefault(grp, []).append(ch)
                    self.all_channels.append(ch)
            except Exception:
                continue
        self._refresh_group_ui()
        self._cleanup_cache_and_channels(valid_caches)
        self._reload_all_sources_background()

    def _reload_all_sources_background(self):
        def load():
            playlist_sources = self.config.get("playlists", [])
            channels_by_group: Dict[str, List[Dict[str, str]]] = {}
            all_channels: List[Dict[str, str]] = []
            valid_caches = set()
            seen_channel_keys = set()
            results = [None] * len(playlist_sources)
            def fetch_playlist(idx, src):
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
                                try:
                                    text = raw.decode("utf-8")
                                except UnicodeDecodeError:
                                    text = raw.decode("latin-1", "ignore")
                            with open(cache_path, "w", encoding="utf-8") as f:
                                f.write(text)
                        else:
                            with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                                text = f.read()
                    else:
                        if os.path.exists(src):
                            with open(src, "r", encoding="utf-8", errors="ignore") as f:
                                text = f.read()
                        else:
                            return
                    results[idx] = text
                except Exception:
                    results[idx] = None
            threads = []
            for idx, src in enumerate(playlist_sources):
                if src.startswith(("http://", "https://")):
                    t = threading.Thread(target=fetch_playlist, args=(idx, src), daemon=True)
                    t.start()
                    threads.append(t)
                else:
                    fetch_playlist(idx, src)
            for t in threads:
                t.join()
            for idx, src in enumerate(playlist_sources):
                text = results[idx]
                if not text:
                    continue
                try:
                    channels = self._parse_m3u_return(text)
                    for ch in channels:
                        key = (ch.get("name", ""), ch.get("url", ""))
                        if key in seen_channel_keys:
                            continue
                        seen_channel_keys.add(key)
                        grp = ch.get("group") or "Uncategorized"
                        channels_by_group.setdefault(grp, []).append(ch)
                        all_channels.append(ch)
                except Exception:
                    continue
            def finish():
                self.channels_by_group = channels_by_group
                self.all_channels = all_channels
                self._refresh_group_ui()
                self._cleanup_cache_and_channels(valid_caches)
            wx.CallAfter(finish)
        threading.Thread(target=load, daemon=True).start()

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
        self.channel_list.Clear()
        source = (self.all_channels if self.current_group == "All Channels"
                  else self.channels_by_group.get(self.current_group, []))
        for ch in source:
            if txt in ch.get("name", "").lower():
                self.displayed.append({"type": "channel", "data": ch})
                self.channel_list.Append(ch.get("name", ""))
        if not txt:
            if self.displayed:
                self.channel_list.SetSelection(0)
                self.channel_list.SetFocus()
                self.on_highlight()
            return

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
                for r in results:
                    label = f"{r['channel_name']} - {r['show_title']} ({self._fmt_time(r['start'])}–{self._fmt_time(r['end'])})"
                    self.displayed.append({"type": "epg", "data": r})
                    self.channel_list.Append(label)
                if self.displayed:
                    self.channel_list.SetSelection(0)
                    self.channel_list.SetFocus()
                    self.on_highlight()
            wx.CallAfter(update_ui)
        threading.Thread(target=lambda: epg_search(my_token), daemon=True).start()

    def _refresh_group_ui(self):
        self.group_list.Clear()
        self.channel_list.Clear()
        self.group_list.Append(f"All Channels ({len(self.all_channels)})")
        for grp in sorted(self.channels_by_group):
            self.group_list.Append(f"{grp} ({len(self.channels_by_group[grp])})")
        self.group_list.SetSelection(0)
        self.on_group_select()

    def reload_epg_sources(self):
        self.epg_sources = self.config.get("epgs", [])

    def start_epg_import_background(self):
        sources = list(self.epg_sources)
        if not sources:
            return
        if self.epg_importing:
            return
        self.epg_importing = True

        def do_import():
            try:
                db = EPGDatabase(get_db_path(), for_threading=True)
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
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()

    def show_manager(self, _):
        dlg = PlaylistManagerDialog(self, self.playlist_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.playlist_sources = dlg.GetResult()
            self.config["playlists"] = self.playlist_sources
            save_config(self.config)
            self.reload_all_sources_initial()
        dlg.Destroy()

    def show_epg_manager(self, _):
        dlg = EPGManagerDialog(self, self.epg_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.epg_sources = dlg.GetResult()
            self.config["epgs"] = self.epg_sources
            save_config(self.config)
            self.reload_epg_sources()
            self.start_epg_import_background()
        dlg.Destroy()

    def import_epg(self, _):
        if self.epg_importing:
            wx.MessageBox("EPG import is already in progress.", "Wait", wx.OK | wx.ICON_INFORMATION)
        return
        # NOTE: the above early return is intentional in this version to keep UI responsive
        # when an import is already running.

    def finish_import(self):
        self.epg_importing = False
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
        self.displayed = []
        self.channel_list.Clear()
        source = self.all_channels if grp == "All Channels" else self.channels_by_group.get(grp, [])
        for ch in source:
            self.displayed.append({"type": "channel", "data": ch})
            self.channel_list.Append(ch.get("name", ""))
        if self.displayed:
            self.channel_list.SetSelection(0)
            self.channel_list.SetFocus()
            self.on_highlight()
        else:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")

    def _fmt_time(self, s):
        # s: "YYYYMMDDHHMMSS" (UTC)
        try:
            dt = datetime.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            local = utc_to_local(dt)
            return local.strftime("%H:%M")
        except Exception:
            return "?"

    def on_highlight(self):
        i = self.channel_list.GetSelection()
        if i < 0 or i >= len(self.displayed):
            self.epg_display.SetValue("")
            self.url_display.SetValue("")
            return
        item = self.displayed[i]
        if item["type"] == "channel":
            ch = item["data"]
            self.url_display.SetValue(ch.get("url", ""))
            cname = ch.get("name", "")

            # check cache
            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                if (datetime.datetime.now() - ts).total_seconds() < self._CACHE_SHOW_STALE_SECS:
                    self.epg_display.SetValue(self._epg_msg_from_tuple(now_show, next_show))
                else:
                    self.epg_display.SetValue("Refreshing EPG…")
                    with self.epg_cache_lock:
                        self.epg_cache.pop(key, None)
                    threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
            else:
                self.epg_display.SetValue("Looking up EPG…")
                threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
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
            self.epg_display.SetValue(msg)
            self.url_display.SetValue(url)
        else:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")

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
        try:
            db = EPGDatabase(get_db_path(), readonly=True)
            try:
                if hasattr(db, "conn"):
                    db.conn.execute("PRAGMA busy_timeout=2000;")
                    db.conn.execute("PRAGMA read_uncommitted=1;")
            except Exception:
                pass
            now_next = db.get_now_next(channel)
            try:
                if hasattr(db, "close"):
                    db.close()
                elif hasattr(db, "conn"):
                    db.conn.close()
            except Exception:
                pass
        except Exception:
            now_next = None
        if not now_next:
            if self.epg_importing:
                wx.CallAfter(self.epg_display.SetValue, "EPG importing…")
            elif not self.epg_sources:
                wx.CallAfter(self.epg_display.SetValue, "No EPG data available.")
            else:
                wx.CallAfter(self.epg_display.SetValue, "No program info found.")
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
                try:
                    print("[DEBUG] EPG UI update for:", item["data"].get("name", ""))
                except Exception:
                    pass
                self.epg_display.SetValue(self._epg_msg_from_tuple(now_show, next_show))

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
            # IMPORTANT: No hidden-window flags, no STARTF_USESHOWWINDOW.
            subprocess.Popen([exe_path, url])  # visible, normal priority
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
            "VLC": [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
            ],
            "MPC": [
                r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe",
                r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe",
            ],
            "MPC-BE": [
                r"C:\Program Files\MPC-BE\mpc-be64.exe",
                r"C:\Program Files (x86)\MPC-BE\mpc-be.exe",
                r"C:\Program Files\MPC-BE x64\mpc-be64.exe",
                r"C:\Program Files\MPC-BE\mpc-be.exe"
            ],
            "Kodi": [r"C:\Program Files\Kodi\kodi.exe"],
            "Winamp": [r"C:\Program Files\Winamp\winamp.exe"],
            "Foobar2000": [r"C:\Program Files\foobar2000\foobar2000.exe"],
            "MPV": [
                r"C:\Program Files\mpv\mpv.exe",
                r"C:\Program Files (x86)\mpv\mpv.exe"
            ],
            "SMPlayer": [
                r"C:\Program Files\SMPlayer\smplayer.exe",
                r"C:\Program Files (x86)\SMPlayer\smplayer.exe",
                r"C:\Program Files\WindowsApps\SMPlayerTeam.SMPlayer_*",
                r"C:\Program Files\WindowsApps\SMPlayerTeam.SMPlayer*\smplayer.exe"
            ],
            "Totem": [],
            "QuickTime": [
                r"C:\Program Files\QuickTime\QuickTimePlayer.exe",
                r"C:\Program Files (x86)\QuickTime\QuickTimePlayer.exe"
            ],
            "iTunes/Apple Music": [
                r"C:\Program Files\iTunes\iTunes.exe",
                r"C:\Program Files (x86)\iTunes\iTunes.exe",
                r"C:\Program Files\WindowsApps\AppleInc.AppleMusic_*",
                r"C:\Program Files\Apple\Music\AppleMusic.exe",
            ],
            "QMPlay2": [
                r"C:\Program Files\QMPlay2\QMPlay2.exe",
                r"C:\Program Files (x86)\QMPlay2\QMPlay2.exe",
            ],
            "PotPlayer": [
                r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
                r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe",
            ],
            "KMPlayer": [
                r"C:\Program Files\KMP Media\KMPlayer\KMPlayer64.exe",
                r"C:\Program Files\KMP Media\KMPlayer\KMPlayer.exe",
            ],
            "AIMP": [
                r"C:\Program Files\AIMP\AIMP.exe",
                r"C:\Program Files (x86)\AIMP\AIMP.exe",
            ],
            "GOM Player": [
                r"C:\Program Files\GRETECH\GomPlayer\GOM.exe",
                r"C:\Program Files (x86)\GRETECH\GomPlayer\GOM.exe",
            ],
            "Clementine": [r"C:\Program Files\Clementine\clementine.exe"],
            "Strawberry": [r"C:\Program Files\Strawberry\strawberry.exe"],
            "Amarok": [],
            "Rhythmbox": [],
            "Pragha": [],
            "Lollypop": [],
            "Exaile": [],
            "Quod Libet": [],
            "Gmusicbrowser": [],
            "Xmms": [],
            "Vocal": [],
            "Haruna": [],
            "Celluloid": [],
        }

        mac_paths = {
            "VLC": ["/Applications/VLC.app/Contents/MacOS/VLC", "/Applications/VLC.app/Contents/MacOS/VLC"],
            "QuickTime": ["/Applications/QuickTime Player.app/Contents/MacOS/QuickTime Player"],
            "iTunes/Apple Music": [
                "/Applications/iTunes.app/Contents/MacOS/iTunes",
                "/Applications/Music.app/Contents/MacOS/Music"
            ],
            "QMPlay2": ["/Applications/QMPlay2.app/Contents/MacOS/QMPlay2"],
            "Audacious": ["/Applications/Audacious.app/Contents/MacOS/Audacious"],
            "Fauxdacious": ["/Applications/Fauxdacious.app/Contents/MacOS/Fauxdacious"],
        }

        linux_players = {
            "VLC": "vlc",
            "MPV": "mpv",
            "Kodi": "kodi",
            "SMPlayer": "smplayer",
            "Totem": "totem",
            "PotPlayer": "potplayer",
            "KMPlayer": "kmplayer",
            "AIMP": "aimp",
            "QMPlay2": "qmplay2",
            "GOM Player": "gomplayer",
            "Audacious": "audacious",
            "Fauxdacious": "fauxdacious",
            "MPC-BE": "mpc-be",
            "Clementine": "clementine",
            "Strawberry": "strawberry",
            "Amarok": "amarok",
            "Rhythmbox": "rhythmbox",
            "Pragha": "pragha",
            "Lollypop": "lollypop",
            "Exaile": "exaile",
            "Quod Libet": "quodlibet",
            "Gmusicbrowser": "gmusicbrowser",
            "Xmms": "xmms",
            "Vocal": "vocal",
            "Haruna": "haruna",
            "Celluloid": "celluloid",
        }

        ok = False
        err = ""

        if self.default_player == "Custom" and custom_path:
            exe = custom_path
            if platform.system() == "Windows":
                ok, err = self._spawn_windows(exe, url)
            else:
                ok, err = self._spawn_posix(exe, url)
        else:
            system = platform.system()
            if system == "Windows":
                choices = win_paths.get(player, [])
                for exe in choices:
                    if "*" in exe:
                        base = os.path.dirname(exe.split("*")[0])
                        if os.path.isdir(base):
                            for root, dirs, files in os.walk(base):
                                for f in files:
                                    if f.lower().endswith(".exe") and "smplayer" in f.lower():
                                        exe_path = os.path.join(root, f)
                                        ok, err = self._spawn_windows(exe_path, url)
                                        if ok:
                                            break
                                if ok:
                                    break
                        if ok:
                            break
                    if os.path.exists(exe):
                        ok, err = self._spawn_windows(exe, url)
                        if ok:
                            break
                if not ok:
                    err = err or f"Could not locate {player} executable."
            elif system == "Darwin":
                choices = mac_paths.get(player, [])
                for exe in choices:
                    if os.path.exists(exe):
                        ok, err = self._spawn_posix(exe, url)
                        if ok:
                            break
                if not ok:
                    err = err or f"Could not locate {player} app."
            else:
                cmd = linux_players.get(player)
                if cmd:
                    ok, err = self._spawn_posix(cmd, url)
                else:
                    err = f"{player} is not configured for Linux."

        if not ok:
            wx.MessageBox(f"Failed to launch {self.default_player}:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)

if __name__ == "__main__":
    app = wx.App()
    IPTVClient()
    app.MainLoop()

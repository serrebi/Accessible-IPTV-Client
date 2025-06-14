import os
import sys
import json
import urllib.request
import gzip
import io
import sqlite3
import threading
import hashlib
from typing import Dict, List, Optional
import wx
import xml.etree.ElementTree as ET
import datetime
import re
import shutil
import platform

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

from options import (
    load_config, save_config, get_cache_path_for_url, get_cache_dir,
    get_db_path, canonicalize_name, relaxed_name, extract_group, utc_to_local,
    CustomPlayerDialog
)
from playlist import (
    EPGDatabase, EPGImportDialog, EPGManagerDialog, PlaylistManagerDialog
)

class IPTVClient(wx.Frame):
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
        self._build_ui()
        self.Centre()
        self.reload_all_sources_initial()
        self.reload_epg_sources()
        self.Show()
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
                    grp = ch.get("group", "Uncategorized")
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
                            text = urllib.request.urlopen(
                                urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                            ).read().decode("utf-8", "ignore")
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
                    t = threading.Thread(target=fetch_playlist, args=(idx, src))
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
                        grp = ch.get("group", "Uncategorized")
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
        files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.endswith(".m3u")]
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
        self.channel_list.Bind(wx.EVT_CHAR_HOOK, self.on_channel_key)
        self.epg_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.url_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        vs_r.Add(self.filter_box, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.channel_list, 1, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.epg_display, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.url_display, 0, wx.EXPAND | wx.ALL, 5)
        hs.Add(vs_l, 1, wx.EXPAND)
        hs.Add(vs_r, 2, wx.EXPAND)
        p.SetSizerAndFit(hs)
        self.group_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_group_select())
        self.filter_box.Bind(wx.EVT_TEXT_ENTER, lambda _: self.apply_filter())
        self.channel_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_highlight())
        self.channel_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _: self.play_selected())
        mb = wx.MenuBar()
        fm = wx.Menu()
        m_mgr = fm.Append(wx.ID_ANY, "Playlist Manager\tCtrl+M")
        m_epg = fm.Append(wx.ID_ANY, "EPG Manager\tCtrl+E")
        m_imp = fm.Append(wx.ID_ANY, "Import EPG to DB\tCtrl+I")
        fm.AppendSeparator()
        m_exit = fm.Append(wx.ID_EXIT, "Exit\tCtrl+Q")
        mb.Append(fm, "File")
        om = wx.Menu()
        player_menu = wx.Menu()
        self.player_VLC = player_menu.AppendRadioItem(wx.ID_ANY, "VLC")
        self.player_MPC = player_menu.AppendRadioItem(wx.ID_ANY, "MPC")
        self.player_MPCBE = player_menu.AppendRadioItem(wx.ID_ANY, "MPC-BE")
        self.player_Kodi = player_menu.AppendRadioItem(wx.ID_ANY, "Kodi")
        self.player_Winamp = player_menu.AppendRadioItem(wx.ID_ANY, "Winamp")
        self.player_Foobar2000 = player_menu.AppendRadioItem(wx.ID_ANY, "Foobar2000")
        self.player_MPV = player_menu.AppendRadioItem(wx.ID_ANY, "MPV")
        self.player_SMPlayer = player_menu.AppendRadioItem(wx.ID_ANY, "SMPlayer")
        self.player_Totem = player_menu.AppendRadioItem(wx.ID_ANY, "Totem")
        self.player_QuickTime = player_menu.AppendRadioItem(wx.ID_ANY, "QuickTime")
        self.player_iTunes = player_menu.AppendRadioItem(wx.ID_ANY, "iTunes/Apple Music")
        self.player_PotPlayer = player_menu.AppendRadioItem(wx.ID_ANY, "PotPlayer")
        self.player_KMPlayer = player_menu.AppendRadioItem(wx.ID_ANY, "KMPlayer")
        self.player_AIMP = player_menu.AppendRadioItem(wx.ID_ANY, "AIMP")
        self.player_QMPlay2 = player_menu.AppendRadioItem(wx.ID_ANY, "QMPlay2")
        self.player_GOMPlayer = player_menu.AppendRadioItem(wx.ID_ANY, "GOM Player")
        self.player_Audacious = player_menu.AppendRadioItem(wx.ID_ANY, "Audacious")
        self.player_Fauxdacious = player_menu.AppendRadioItem(wx.ID_ANY, "Fauxdacious")
        self.player_Custom = player_menu.AppendRadioItem(wx.ID_ANY, "Custom Player...")
        om.AppendSubMenu(player_menu, "Media Player to Use")
        mb.Append(om, "Options")
        self.SetMenuBar(mb)
        self.Bind(wx.EVT_MENU, self.show_manager, m_mgr)
        self.Bind(wx.EVT_MENU, self.show_epg_manager, m_epg)
        self.Bind(wx.EVT_MENU, self.import_epg, m_imp)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), m_exit)
        for item, key in [
            (self.player_VLC, "VLC"),
            (self.player_MPC, "MPC"),
            (self.player_MPCBE, "MPC-BE"),
            (self.player_Kodi, "Kodi"),
            (self.player_Winamp, "Winamp"),
            (self.player_Foobar2000, "Foobar2000"),
            (self.player_MPV, "MPV"),
            (self.player_SMPlayer, "SMPlayer"),
            (self.player_Totem, "Totem"),
            (self.player_QuickTime, "QuickTime"),
            (self.player_iTunes, "iTunes/Apple Music"),
            (self.player_PotPlayer, "PotPlayer"),
            (self.player_KMPlayer, "KMPlayer"),
            (self.player_AIMP, "AIMP"),
            (self.player_QMPlay2, "QMPlay2"),
            (self.player_GOMPlayer, "GOM Player"),
            (self.player_Audacious, "Audacious"),
            (self.player_Fauxdacious, "Fauxdacious"),
        ]:
            self.Bind(wx.EVT_MENU, lambda evt, attr=key: self._select_player(attr), item)
        self.Bind(wx.EVT_MENU, self._select_custom_player, self.player_Custom)
        defplayer = self.config.get("media_player", "VLC")
        if defplayer == "Custom":
            self.player_Custom.Check()
        else:
            attr = f"player_{defplayer.replace('/','').replace(' ','')}"
            if hasattr(self, attr):
                getattr(self, attr).Check()
            else:
                self.player_VLC.Check()

    def _select_player(self, player):
        self.default_player = player
        self.config["media_player"] = player
        save_config(self.config)

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
        self.player_Custom.Check()

    def on_channel_key(self, event):
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
            return
        def epg_search():
            try:
                db = EPGDatabase(get_db_path(), readonly=True)
                results = db.get_channels_with_show(txt)
                db.close()
            except Exception:
                results = []
            def update_ui():
                if txt != self.filter_box.GetValue().strip().lower():
                    return
                for r in results:
                    label = f"{r['channel_name']} - {r['show_title']} ({self._fmt_time(r['start'])}–{self._fmt_time(r['end'])})"
                    self.displayed.append({"type": "epg", "data": r})
                    self.channel_list.Append(label)
            wx.CallAfter(update_ui)
        threading.Thread(target=epg_search, daemon=True).start()

    def _fmt_time(self, s):
        try:
            dt = datetime.datetime.strptime(s[:14], "%Y%m%d%H%M%S")
            return utc_to_local(dt).strftime('%a %H:%M')
        except Exception:
            return s

    def on_group_select(self):
        sel = self.group_list.GetStringSelection().split(" (", 1)[0]
        self.current_group = sel
        self.apply_filter()

    def on_highlight(self):
        i = self.channel_list.GetSelection()
        if 0 <= i < len(self.displayed):
            item = self.displayed[i]
            if item["type"] == "channel":
                self.url_display.SetValue(item["data"].get("url", ""))
                epg_txt = self.get_epg_info(item["data"])
                self.epg_display.SetValue(epg_txt)
            elif item["type"] == "epg":
                self.url_display.SetValue("")
                r = item["data"]
                url = ""
                for ch in self.all_channels:
                    if canonicalize_name(ch["name"]) == canonicalize_name(r["channel_name"]):
                        url = ch.get("url", "")
                        break
                msg = f"Show: {r['show_title']} | Channel: {r['channel_name']} | Start: {self._fmt_time(r['start'])} | End: {self._fmt_time(r['end'])})"
                self.epg_display.SetValue(msg)
                self.url_display.SetValue(url)
        else:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")

    # LAG FIX IS HERE: Short-circuit DB calls if no EPG sources are loaded!
    def get_epg_info(self, channel):
        if self.epg_importing:
            return "EPG importing…"
        if not self.epg_sources:
            return "No EPG data available."
        if not channel.get("tvg-id") and not channel.get("name"):
            return "No EPG data available."
        db = EPGDatabase(get_db_path())
        try:
            now_next = db.get_now_next(channel)
        finally:
            db.close()
        if not now_next:
            return "No EPG data available."
        now, nxt = now_next
        msg = ""
        def localfmt(dt):
            local = utc_to_local(dt)
            return local.strftime('%H:%M')
        if now:
            msg += f"Now: {now['title']} ({localfmt(now['start'])} – {localfmt(now['end'])})"
        else:
            msg += "No program currently airing."
        if nxt:
            msg += f"\nNext: {nxt['title']} ({localfmt(nxt['start'])} – {localfmt(nxt['end'])})"
        return msg

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
                r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"
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
            "PotPlayer": [
                r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
                r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe"
            ],
            "KMPlayer": [
                r"C:\Program Files\KMPlayer\KMPlayer.exe",
                r"C:\Program Files (x86)\KMPlayer\KMPlayer.exe"
            ],
            "AIMP": [
                r"C:\Program Files\AIMP\AIMP.exe",
                r"C:\Program Files (x86)\AIMP\AIMP.exe"
            ],
            "QMPlay2": [
                r"C:\Program Files\QMPlay2\QMPlay2.exe",
                r"C:\Program Files (x86)\QMPlay2\QMPlay2.exe"
            ],
            "GOM Player": [
                r"C:\Program Files\GRETECH\GomPlayer\GOM.exe",
                r"C:\Program Files (x86)\GRETECH\GomPlayer\GOM.exe"
            ],
            "Audacious": [
                r"C:\Program Files (x86)\Audacious\audacious.exe",
                r"C:\Program Files\Audacious\audacious.exe"
            ],
            "Fauxdacious": [
                r"C:\Program Files\Fauxdacious\fauxdacious.exe",
                r"C:\Program Files (x86)\Fauxdacious\fauxdacious.exe",
                r"C:\Program Files (x86)\Fauxdacious\bin\fauxdacious.exe",
                r"C:\Program Files\Fauxdacious\bin\fauxdacious.exe"
            ]
        }
        mac_paths = {
            "VLC": ["/Applications/VLC.app/Contents/MacOS/VLC"],
            "QuickTime": ["/Applications/QuickTime Player.app/Contents/MacOS/QuickTime Player"],
            "iTunes/Apple Music": [
                "/Applications/iTunes.app/Contents/MacOS/iTunes",
                "/Applications/Music.app/Contents/MacOS/Music"
            ],
            "PotPlayer": [],
            "KMPlayer": [],
            "AIMP": [],
            "QMPlay2": ["/Applications/QMPlay2.app/Contents/MacOS/QMPlay2"],
            "GOM Player": [],
            "Audacious": ["/Applications/Audacious.app/Contents/MacOS/Audacious"],
            "Fauxdacious": ["/Applications/Fauxdacious.app/Contents/MacOS/Fauxdacious"],
            "MPC-BE": []
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
            "MPC-BE": "mpc-be"
        }

        if player == "Custom":
            exe = custom_path
            if not exe:
                wx.MessageBox("No custom player set.", "Error", wx.OK | wx.ICON_ERROR)
                return
            if not os.path.exists(exe):
                wx.MessageBox("Custom player path does not exist.", "Error", wx.OK | wx.ICON_ERROR)
                return
            try:
                if sys.platform.startswith("win"):
                    os.spawnl(os.P_NOWAIT, exe, os.path.basename(exe), url)
                else:
                    threading.Thread(target=lambda: os.system(f'"{exe}" "{url}" &'), daemon=True).start()
            except Exception as e:
                wx.MessageBox(f"Failed to start custom player: {e}", "Error", wx.OK | wx.ICON_ERROR)
            return

        if sys.platform.startswith("win"):
            exe_list = win_paths.get(player, [])
            found = ""
            for p in exe_list:
                if "*" in p:
                    folder = os.path.dirname(p)
                    try:
                        import glob
                        matches = glob.glob(p)
                        if matches:
                            for match in matches:
                                smplayer_exe = os.path.join(match, "smplayer.exe")
                                if os.path.exists(smplayer_exe):
                                    found = smplayer_exe
                                    break
                                am = os.path.join(match, "AppleMusic.exe")
                                if os.path.exists(am):
                                    found = am
                                    break
                            if found:
                                break
                    except Exception:
                        continue
                if os.path.exists(p):
                    found = p
                    break
            if found:
                try:
                    os.spawnl(os.P_NOWAIT, found, os.path.basename(found), url)
                except Exception as e:
                    wx.MessageBox(f"Failed to start {player}: {e}", "Error", wx.OK | wx.ICON_ERROR)
                return
            wx.MessageBox(f"{player} not found.", "Error",
                          wx.OK | wx.ICON_ERROR)
        elif sys.platform == "darwin":
            plist = mac_paths.get(player, [])
            for path in plist:
                if os.path.exists(path):
                    try:
                        threading.Thread(target=lambda: os.system(f'"{path}" "{url}" &'), daemon=True).start()
                    except Exception as e:
                        wx.MessageBox(f"Failed to start {player}: {e}", "Error", wx.OK | wx.ICON_ERROR)
                    return
            wx.MessageBox(f"{player} not found in /Applications.", "Error",
                          wx.OK | wx.ICON_ERROR)
        else:
            exe = linux_players.get(player, player.lower())
            found = shutil.which(exe)
            if found:
                try:
                    threading.Thread(target=lambda: os.system(f'"{found}" "{url}" &'), daemon=True).start()
                except Exception as e:
                    wx.MessageBox(f"Failed to start {player}: {e}", "Error", wx.OK | wx.ICON_ERROR)
                return
            wx.MessageBox(f"{player} not found in PATH.", "Error",
                          wx.OK | wx.ICON_ERROR)

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
        self.epg_importing = True

        def do_import():
            try:
                db = EPGDatabase(get_db_path(), for_threading=True)
                db.import_epg_xml(sources)
                wx.CallAfter(self.finish_import_background)
            except Exception:
                wx.CallAfter(self.finish_import_background)
        threading.Thread(target=do_import, daemon=True).start()

    def finish_import_background(self):
        self.epg_importing = False
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
        dlg.Destroy()

    def import_epg(self, _):
        if self.epg_importing:
            wx.MessageBox("EPG import is already in progress.", "Wait", wx.OK | wx.ICON_INFORMATION)
            return
        sources = list(self.epg_sources)
        if not sources:
            wx.MessageBox("No EPG sources to import.", "Error", wx.OK | wx.ICON_ERROR)
            return
        self.epg_importing = True
        dlg = EPGImportDialog(self, len(sources))
        self.import_dialog = dlg

        def do_import():
            try:
                def progress_callback(value, total):
                    wx.CallAfter(dlg.set_progress, value, total)
                db = EPGDatabase(get_db_path(), for_threading=True)
                db.import_epg_xml(sources, progress_callback)
                wx.CallAfter(dlg.Destroy)
                wx.CallAfter(self.finish_import)
            except Exception as e:
                wx.CallAfter(dlg.Destroy)
                wx.CallAfter(wx.MessageBox, f"EPG import failed: {e}", "Error", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.finish_import)
        thread = threading.Thread(target=do_import, daemon=True)
        thread.start()
        dlg.ShowModal()

    def finish_import(self):
        self.epg_importing = False
        wx.MessageBox("EPG import completed.", "Done", wx.OK | wx.ICON_INFORMATION)
        self.on_highlight()

    def _parse_m3u_return(self, content: str) -> List[Dict[str, str]]:
        lines = content.splitlines()
        current_group = "Uncategorized"
        out = []
        for i, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                group = current_group
                tvg_id = ""
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    group = group_match.group(1)
                    current_group = group
                tvg_id_match = re.search(r'tvg-id="([^"]*)"', line)
                if tvg_id_match:
                    tvg_id = tvg_id_match.group(1)
                if ',' in line:
                    name = line.rsplit(',', 1)[-1]
                else:
                    name = ''
                name = name.strip(' "\'')
                url = ''
                for j in range(i+1, min(i+4, len(lines))):
                    if lines[j].startswith(('http://', 'https://')):
                        url = lines[j]
                        break
                if name and url:
                    out.append({"name": name.strip(), "url": url.strip(), "group": group, "tvg-id": tvg_id})
        return out

    def Destroy(self):
        return super().Destroy()

if __name__ == '__main__':
    app = wx.App(False)
    IPTVClient()
    app.MainLoop()

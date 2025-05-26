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

CONFIG_FILE = "iptvclient.conf"
DB_FILE = "epg.db"

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path():
    return os.path.join(get_base_path(), CONFIG_FILE)

def get_db_path():
    return os.path.join(get_base_path(), DB_FILE)

def get_cache_dir():
    cache_dir = os.path.join(get_base_path(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def get_cache_path_for_url(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(get_cache_dir(), f"{h}.m3u")

def load_config() -> Dict:
    path = get_config_path()
    default = {"playlists": [], "epgs": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "playlists" not in data: data["playlists"] = []
                    if "epgs" not in data: data["epgs"] = []
                    return data
        except Exception as e:
            wx.LogError(f"Failed to load config: {e}")
    return default

def save_config(cfg: Dict):
    try:
        path = get_config_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        wx.LogError(f"Failed to save config: {e}")

def canonicalize_name(name: str) -> str:
    name = name.strip().lower()
    tags = ['hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq']
    pattern = r'^(?:' + '|'.join(tags) + r')\b[\s\-:]*|[\s\-:]*\b(?:' + '|'.join(tags) + r')$'
    while True:
        newname = re.sub(pattern, '', name, flags=re.I).strip()
        if newname == name:
            break
        name = newname
    return name

def relaxed_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r'[\(\[].*?[\)\]]', '', n)
    tags = r'\b(?:hd|sd|hevc|fhd|uhd|4k|8k|hdr|dash|hq)\b'
    n = re.sub(tags, '', n, flags=re.I)
    n = re.sub(r'[^\w\s]', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()

class EPGDatabase:
    def __init__(self, db_path: str, for_threading=False):
        self.db_path = db_path
        self.for_threading = for_threading
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=not for_threading)
        self.create_tables()

    def create_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                norm_name TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS programmes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                title TEXT,
                start TEXT,
                end TEXT,
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                UNIQUE(channel_id, start, end)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start ON programmes (channel_id, start)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_title ON programmes (title)")
        self.conn.commit()

    def insert_channel(self, channel_id: str, display_name: str):
        norm = canonicalize_name(display_name)
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO channels (id, display_name, norm_name) VALUES (?, ?, ?)", (channel_id, display_name, norm))

    def insert_programme(self, channel_id: str, title: str, start: str, end: str):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO programmes (channel_id, title, start, end) VALUES (?, ?, ?, ?)", (channel_id, title, start, end))

    def prune_old_programmes(self, days: int = 4):
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        c.execute("DELETE FROM programmes WHERE end < ?", (cutoff,))
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def get_matching_channel_id(self, channel: Dict[str, str]) -> Optional[str]:
        tvg_id = channel.get("tvg-id", "").strip()
        if tvg_id:
            c = self.conn.cursor()
            row = c.execute("SELECT id FROM channels WHERE id = ?", (tvg_id,)).fetchone()
            if row:
                return row[0]
        name = channel.get("name", "")
        norm = canonicalize_name(name)
        c = self.conn.cursor()
        row = c.execute("SELECT id FROM channels WHERE norm_name = ?", (norm,)).fetchone()
        if row:
            return row[0]
        relaxed = relaxed_name(name)
        rows = c.execute("SELECT id, display_name FROM channels").fetchall()
        for ch_id, display_name in rows:
            if relaxed_name(display_name) == relaxed:
                return ch_id
        return None

    def get_now_next(self, channel: Dict[str, str]) -> Optional[tuple]:
        ch_id = self.get_matching_channel_id(channel)
        if not ch_id:
            return None
        now = datetime.datetime.utcnow()
        now_str = now.strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        row = c.execute(
            "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start <= ? AND end > ? ORDER BY start DESC LIMIT 1",
            (ch_id, now_str, now_str)).fetchone()
        current = None
        nxt = None
        if row:
            title, start, end = row
            current = {
                "title": title,
                "start": datetime.datetime.strptime(start, "%Y%m%d%H%M%S"),
                "end": datetime.datetime.strptime(end, "%Y%m%d%H%M%S")
            }
            row2 = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start > ? ORDER BY start ASC LIMIT 1",
                (ch_id, end)).fetchone()
            if row2:
                title2, start2, end2 = row2
                nxt = {
                    "title": title2,
                    "start": datetime.datetime.strptime(start2, "%Y%m%d%H%M%S"),
                    "end": datetime.datetime.strptime(end2, "%Y%m%d%H%M%S")
                }
        return (current, nxt)

    def close(self):
        self.conn.close()

    def import_epg_xml(self, xml_sources: List[str], progress_callback=None):
        thread_db = EPGDatabase(self.db_path, for_threading=True)
        total = len(xml_sources)
        for idx, src in enumerate(xml_sources):
            try:
                if src.startswith("http"):
                    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        raw = resp.read()
                        if src.endswith(".gz") or resp.info().get("Content-Encoding") == "gzip":
                            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                                f = io.TextIOWrapper(gz, encoding="utf-8", errors="ignore")
                                thread_db._stream_parse_epg(f)
                        else:
                            f = io.StringIO(raw.decode("utf-8", "ignore"))
                            thread_db._stream_parse_epg(f)
                else:
                    if src.endswith(".gz"):
                        with gzip.open(src, "rt", encoding="utf-8", errors="ignore") as f:
                            thread_db._stream_parse_epg(f)
                    else:
                        with open(src, "r", encoding="utf-8", errors="ignore") as f:
                            thread_db._stream_parse_epg(f)
            except Exception as e:
                wx.LogError(f"EPG import failed for {src}: {e}")
            if progress_callback:
                progress_callback(idx + 1, total)
        thread_db.commit()
        thread_db.prune_old_programmes(4)
        thread_db.close()

    def _stream_parse_epg(self, filelike):
        context = ET.iterparse(filelike, events=("end",))
        for event, elem in context:
            if elem.tag == "channel":
                cid = elem.get("id")
                disp_name = None
                for dn in elem.findall("display-name"):
                    disp_name = dn.text or ""
                    break
                if cid and disp_name:
                    self.insert_channel(cid, disp_name)
                elem.clear()
            elif elem.tag == "programme":
                cid = elem.get("channel")
                title = elem.findtext("title", "")
                start = elem.get("start")
                end = elem.get("stop")
                if cid and title and start and end:
                    try:
                        _ = datetime.datetime.strptime(start[:14], "%Y%m%d%H%M%S")
                        _ = datetime.datetime.strptime(end[:14], "%Y%m%d%H%M%S")
                        self.insert_programme(cid, title, start[:14], end[:14])
                    except Exception:
                        pass
                elem.clear()

class EPGImportDialog(wx.Dialog):
    def __init__(self, parent, total):
        super().__init__(parent, title="Importing EPG", size=(400, 120))
        self.progress = wx.Gauge(self, range=total)
        self.label = wx.StaticText(self, label="Importing EPG data...")
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.label, 0, wx.ALL | wx.EXPAND, 10)
        sizer.Add(self.progress, 0, wx.ALL | wx.EXPAND, 10)
        self.SetSizer(sizer)
        self.Layout()
        self.CenterOnParent()

    def set_progress(self, value, total):
        self.progress.SetRange(total)
        self.progress.SetValue(value)
        self.label.SetLabel(f"Imported {value}/{total} sources")

class EPGManagerDialog(wx.Dialog):
    def __init__(self, parent, epg_sources):
        super().__init__(parent, title="EPG Manager", size=(600, 300))
        self.epg_sources = epg_sources.copy()
        self._build_ui()
        self.CenterOnParent()
        self.Layout()
        wx.CallAfter(self.add_url_btn.SetFocus)

    def _build_ui(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.add_file_btn = wx.Button(panel, label="Add File")
        self.add_url_btn = wx.Button(panel, label="Add URL")
        self.remove_btn = wx.Button(panel, label="Remove Selected")
        for btn in (self.add_file_btn, self.add_url_btn, self.remove_btn):
            btn_sizer.Add(btn, 0, wx.ALL, 2)
        main_sizer.Add(btn_sizer, 0, wx.EXPAND)
        self.lb = wx.ListBox(panel, style=wx.LB_SINGLE)
        for src in self.epg_sources:
            self.lb.Append(src)
        if self.epg_sources:
            self.lb.SetSelection(0)
        main_sizer.Add(self.lb, 1, wx.EXPAND | wx.ALL, 5)
        ok_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, id=wx.ID_OK)
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL)
        ok_sizer.Add(ok_btn, 0, wx.ALL, 5)
        ok_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        main_sizer.Add(ok_sizer, 0, wx.ALIGN_RIGHT)
        panel.SetSizer(main_sizer)
        self.add_file_btn.Bind(wx.EVT_BUTTON, self.OnAddFile)
        self.add_url_btn.Bind(wx.EVT_BUTTON, self.OnAddURL)
        self.remove_btn.Bind(wx.EVT_BUTTON, self.OnRemove)

    def OnAddFile(self, _):
        with wx.FileDialog(self, "Add XMLTV File",
                           wildcard="XMLTV Files (*.xml;*.gz)|*.xml;*.gz",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                if path and path not in self.epg_sources:
                    self.epg_sources.append(path)
                    self.lb.Append(path)
                    self.lb.SetSelection(self.lb.GetCount() - 1)
        wx.CallAfter(self.add_file_btn.SetFocus)

    def OnAddURL(self, _):
        dlg = wx.TextEntryDialog(self, "Enter EPG XMLTV URL:", "Add URL")
        if dlg.ShowModal() == wx.ID_OK:
            url = dlg.GetValue().strip()
            if url and url not in self.epg_sources:
                self.epg_sources.append(url)
                self.lb.Append(url)
                self.lb.SetSelection(self.lb.GetCount() - 1)
        dlg.Destroy()
        wx.CallAfter(self.add_url_btn.SetFocus)

    def OnRemove(self, _):
        idx = self.lb.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        src = self.epg_sources[idx]
        if wx.MessageBox(f"Remove this EPG source?\n{src}", "Confirm",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) == wx.YES:
            self.epg_sources.pop(idx)
            self.lb.Delete(idx)
            new_count = self.lb.GetCount()
            if new_count > 0:
                self.lb.SetSelection(min(idx, new_count - 1))

    def GetResult(self):
        return self.epg_sources

class PlaylistManagerDialog(wx.Dialog):
    def __init__(self, parent, playlist_sources):
        super().__init__(parent, title="Playlist Manager", size=(600, 300))
        self.playlist_sources = playlist_sources.copy()
        self._build_ui()
        self.CenterOnParent()
        self.Layout()
        wx.CallAfter(self.add_file_btn.SetFocus)

    def _build_ui(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.add_file_btn = wx.Button(panel, label="Add File")
        self.add_url_btn = wx.Button(panel, label="Add URL")
        self.remove_btn = wx.Button(panel, label="Remove Selected")
        self.up_btn = wx.Button(panel, label="Move Up")
        self.down_btn = wx.Button(panel, label="Move Down")
        for btn in (self.add_file_btn, self.add_url_btn, self.remove_btn, self.up_btn, self.down_btn):
            btn_sizer.Add(btn, 0, wx.ALL, 2)
        main_sizer.Add(btn_sizer, 0, wx.EXPAND)
        self.lb = wx.ListBox(panel, style=wx.LB_SINGLE)
        for src in self.playlist_sources:
            self.lb.Append(src)
        if self.playlist_sources:
            self.lb.SetSelection(0)
        main_sizer.Add(self.lb, 1, wx.EXPAND | wx.ALL, 5)
        ok_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, id=wx.ID_OK)
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL)
        ok_sizer.Add(ok_btn, 0, wx.ALL, 5)
        ok_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        main_sizer.Add(ok_sizer, 0, wx.ALIGN_RIGHT)
        panel.SetSizer(main_sizer)
        self.add_file_btn.Bind(wx.EVT_BUTTON, self.OnAddFile)
        self.add_url_btn.Bind(wx.EVT_BUTTON, self.OnAddURL)
        self.remove_btn.Bind(wx.EVT_BUTTON, self.OnRemove)
        self.up_btn.Bind(wx.EVT_BUTTON, self.OnMoveUp)
        self.down_btn.Bind(wx.EVT_BUTTON, self.OnMoveDown)

    def OnAddFile(self, _):
        with wx.FileDialog(self, "Add M3U/M3U8 File",
                           wildcard="M3U Files (*.m3u;*.m3u8)|*.m3u;*.m3u8",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                if path and path not in self.playlist_sources:
                    self.playlist_sources.append(path)
                    self.lb.Append(path)
                    self.lb.SetSelection(self.lb.GetCount() - 1)
        wx.CallAfter(self.add_file_btn.SetFocus)

    def OnAddURL(self, _):
        dlg = wx.TextEntryDialog(self, "Enter M3U URL:", "Add URL")
        if dlg.ShowModal() == wx.ID_OK:
            url = dlg.GetValue().strip()
            if url and url not in self.playlist_sources:
                self.playlist_sources.append(url)
                self.lb.Append(url)
                self.lb.SetSelection(self.lb.GetCount() - 1)
        dlg.Destroy()
        wx.CallAfter(self.add_url_btn.SetFocus)

    def OnRemove(self, _):
        idx = self.lb.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        src = self.playlist_sources[idx]
        if wx.MessageBox(f"Remove this playlist?\n{src}", "Confirm",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) == wx.YES:
            self.playlist_sources.pop(idx)
            self.lb.Delete(idx)
            new_count = self.lb.GetCount()
            if new_count > 0:
                self.lb.SetSelection(min(idx, new_count - 1))

    def OnMoveUp(self, _):
        idx = self.lb.GetSelection()
        if idx > 0:
            self.playlist_sources[idx - 1], self.playlist_sources[idx] = (
                self.playlist_sources[idx], self.playlist_sources[idx - 1]
            )
            self.RefreshList(idx - 1)

    def OnMoveDown(self, _):
        idx = self.lb.GetSelection()
        if idx < len(self.playlist_sources) - 1 and idx != wx.NOT_FOUND:
            self.playlist_sources[idx + 1], self.playlist_sources[idx] = (
                self.playlist_sources[idx], self.playlist_sources[idx + 1]
            )
            self.RefreshList(idx + 1)

    def RefreshList(self, new_idx: int):
        self.lb.Clear()
        for src in self.playlist_sources:
            self.lb.Append(src)
        self.lb.SetSelection(new_idx)

    def GetResult(self):
        return self.playlist_sources

class IPTVClient(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))
        self.config = load_config()
        self.playlist_sources = self.config.get("playlists", [])
        self.epg_sources = self.config.get("epgs", [])
        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.filtered: List[Dict[str, str]] = []
        self.current_group = "All Channels"
        self.default_player = "VLC"
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
        self.player_Kodi = player_menu.AppendRadioItem(wx.ID_ANY, "Kodi")
        self.player_Winamp = player_menu.AppendRadioItem(wx.ID_ANY, "Winamp")
        self.player_Foobar2000 = player_menu.AppendRadioItem(wx.ID_ANY, "Foobar2000")
        om.AppendSubMenu(player_menu, "Media Player to Use")
        mb.Append(om, "Options")
        self.SetMenuBar(mb)
        self.Bind(wx.EVT_MENU, self.show_manager, m_mgr)
        self.Bind(wx.EVT_MENU, self.show_epg_manager, m_epg)
        self.Bind(wx.EVT_MENU, self.import_epg, m_imp)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), m_exit)
        for item in (self.player_VLC, self.player_MPC, self.player_Kodi,
                     self.player_Winamp, self.player_Foobar2000):
            self.Bind(wx.EVT_MENU, lambda _: self._select_player(), item)

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

    def on_group_key(self, event):
        key = event.GetKeyCode()
        sel = self.group_list.GetSelection()
        count = self.group_list.GetCount()
        if key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            return
        if key == wx.WXK_UP:
            if sel > 0:
                self.group_list.SetSelection(sel - 1)
                self.on_group_select()
        elif key == wx.WXK_DOWN:
            if sel < count - 1:
                self.group_list.SetSelection(sel + 1)
                self.on_group_select()
        else:
            event.Skip()

    def on_channel_key(self, event):
        key = event.GetKeyCode()
        sel = self.channel_list.GetSelection()
        count = self.channel_list.GetCount()
        if key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            return
        if key == wx.WXK_UP:
            if sel > 0:
                self.channel_list.SetSelection(sel - 1)
                self.on_highlight()
        elif key == wx.WXK_DOWN:
            if sel < count - 1:
                self.channel_list.SetSelection(sel + 1)
                self.on_highlight()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.play_selected()
        else:
            event.Skip()

    def _select_player(self):
        for attr in ("VLC", "MPC", "Kodi", "Winamp", "Foobar2000"):
            item = getattr(self, f"player_{attr}")
            if item.IsChecked():
                self.default_player = attr
                break

    def on_group_select(self):
        sel = self.group_list.GetStringSelection().split(" (", 1)[0]
        self.current_group = sel
        self.apply_filter()

    def apply_filter(self):
        txt = self.filter_box.GetValue().lower()
        self.filtered = []
        self.channel_list.Clear()
        source = (self.all_channels if self.current_group == "All Channels"
                  else self.channels_by_group.get(self.current_group, []))
        for ch in source:
            if txt in ch.get("name", "").lower():
                self.filtered.append(ch)
                self.channel_list.Append(ch.get("name", ""))

    def on_highlight(self):
        i = self.channel_list.GetSelection()
        if 0 <= i < len(self.filtered):
            self.url_display.SetValue(self.filtered[i].get("url", ""))
            epg_txt = self.get_epg_info(self.filtered[i])
            self.epg_display.SetValue(epg_txt)
        else:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")

    def get_epg_info(self, channel):
        if self.epg_importing:
            return "EPG importing…"
        db = EPGDatabase(get_db_path())
        try:
            now_next = db.get_now_next(channel)
        finally:
            db.close()
        if not now_next:
            return "No EPG data available."
        now, nxt = now_next
        msg = ""
        if now:
            msg += f"Now: {now['title']} ({now['start'].strftime('%H:%M')} – {now['end'].strftime('%H:%M')})"
        else:
            msg += "No program currently airing."
        if nxt:
            msg += f"\nNext: {nxt['title']} ({nxt['start'].strftime('%H:%M')} – {nxt['end'].strftime('%H:%M')})"
        return msg

    def play_selected(self):
        i = self.channel_list.GetSelection()
        if not (0 <= i < len(self.filtered)):
            return
        url = self.filtered[i].get("url", "")
        exe_list = {
            "VLC": [r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"],
            "MPC": [r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                    r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"],
            "Kodi": [r"C:\Program Files\Kodi\kodi.exe"],
            "Winamp": [r"C:\Program Files\Winamp\winamp.exe"],
            "Foobar2000": [r"C:\Program Files\foobar2000\foobar2000.exe"],
        }[self.default_player]
        for p in exe_list:
            if os.path.exists(p):
                os.spawnl(os.P_NOWAIT, p, os.path.basename(p), url)
                return
        wx.MessageBox(f"{self.default_player} not found.", "Error",
                      wx.OK | wx.ICON_ERROR)

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
                name = line.split(',', 1)[1] if ',' in line else ''
                group = current_group
                tvg_id = ""
                if 'group-title="' in line:
                    try:
                        group = line.split('group-title="', 1)[1].split('"', 1)[0]
                        current_group = group
                    except Exception:
                        group = current_group
                if 'tvg-id="' in line:
                    try:
                        tvg_id = line.split('tvg-id="', 1)[1].split('"', 1)[0]
                    except Exception:
                        tvg_id = ""
                url = ''
                for j in range(i+1, min(i+4, len(lines))):
                    if lines[j].startswith(('http://', 'https://')):
                        url = lines[j]
                        break
                if name and url:
                    out.append({"name": name.strip(), "url": url.strip(), "group": group, "tvg-id": tvg_id})
        return out

    def Destroy(self):
        super().Destroy()

if __name__ == '__main__':
    app = wx.App(False)
    IPTVClient()
    app.MainLoop()

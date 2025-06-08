import sqlite3
import urllib.request
import gzip
import io
import threading
import xml.etree.ElementTree as ET
import datetime
import re
import wx
from typing import Dict, List, Optional

STRIP_TAGS = [
    'hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq', 'st',
    'us', 'usa', 'ca', 'canada', 'car', 'uk', 'u.k.', 'u.k', 'uk.', 'u.s.', 'u.s', 'us.', 'au', 'aus', 'nz'
]

def canonicalize_name(name: str) -> str:
    name = name.strip().lower()
    tags = STRIP_TAGS
    pattern = r'^(?:' + '|'.join(tags) + r')\b[\s\-:]*|[\s\-:]*\b(?:' + '|'.join(tags) + r')$'
    while True:
        newname = re.sub(pattern, '', name, flags=re.I).strip()
        if newname == name:
            break
        name = newname
    name = re.sub(r'\b(?:' + '|'.join(tags) + r')\b', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()

def relaxed_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r'[\(\[].*?[\)\]]', '', n)
    tags = r'\b(?:' + '|'.join(STRIP_TAGS) + r')\b'
    n = re.sub(tags, '', n, flags=re.I)
    n = re.sub(r'[^\w\s]', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()

def extract_group(title: str) -> str:
    if not title:
        return ''
    title = title.lower()
    country_map = {
        'us': 'us', 'usa': 'us', 'united states': 'us', 'u.s.': 'us', 'u.s': 'us',
        'ca': 'ca', 'canada': 'ca', 'car': 'ca',
        'uk': 'uk', 'u.k.': 'uk', 'u.k': 'uk', 'uk.': 'uk',
        'gb': 'uk', 'great britain': 'uk',
        'au': 'au', 'aus': 'au', 'australia': 'au',
        'nz': 'nz', 'new zealand': 'nz'
    }
    for key, val in country_map.items():
        if re.search(r'\b' + re.escape(key) + r'\b', title):
            return val
    m = re.match(r'([a-z]{2,3})', title)
    if m:
        code = m.group(1)
        return country_map.get(code, code)
    return ''

class EPGDatabase:
    def __init__(self, db_path: str, for_threading=False, readonly=False):
        self.db_path = db_path
        self.for_threading = for_threading
        uri = False
        if readonly:
            uri = True
            db_path = "file:{}?mode=ro".format(db_path)
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=not for_threading, uri=uri)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        if not readonly:
            self.create_tables()

    def create_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                norm_name TEXT,
                group_tag TEXT
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
        group_tag = extract_group(display_name)
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO channels (id, display_name, norm_name, group_tag) VALUES (?, ?, ?, ?)",
            (channel_id, display_name, norm, group_tag)
        )

    def insert_programme(self, channel_id: str, title: str, start: str, end: str):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO programmes (channel_id, title, start, end) VALUES (?, ?, ?, ?)", (channel_id, title, start, end))

    def prune_old_programmes(self, days: int = 4):
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        c.execute("DELETE FROM programmes WHERE end < ?", (cutoff,))
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def get_matching_channel_id(self, channel: Dict[str, str]) -> Optional[str]:
        tvg_id = channel.get("tvg-id", "").strip()
        group_tag = extract_group(channel.get("group", ""))
        name = channel.get("name", "")

        c = self.conn.cursor()
        if tvg_id:
            row = c.execute("SELECT id FROM channels WHERE id = ?", (tvg_id,)).fetchone()
            if row:
                return row[0]
        norm = canonicalize_name(name)
        row = c.execute("SELECT id FROM channels WHERE norm_name = ? AND group_tag = ?", (norm, group_tag)).fetchone()
        if row:
            return row[0]
        relaxed = relaxed_name(name)
        rows = c.execute("SELECT id, display_name FROM channels WHERE group_tag = ?", (group_tag,)).fetchall()
        for ch_id, display_name in rows:
            if relaxed_name(display_name) == relaxed:
                return ch_id
        row = c.execute("SELECT id FROM channels WHERE norm_name = ?", (norm,)).fetchone()
        if row:
            return row[0]
        all_rows = c.execute("SELECT id, display_name FROM channels").fetchall()
        for ch_id, display_name in all_rows:
            if relaxed_name(display_name) == relaxed:
                return ch_id
        return None

    def get_now_next(self, channel: Dict[str, str]) -> Optional[tuple]:
        ch_id = self.get_matching_channel_id(channel)
        if not ch_id:
            return None
        now = datetime.datetime.now(datetime.UTC)
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
                (ch_id, now_str)).fetchone()
            if row2:
                title2, start2, end2 = row2
                nxt = {
                    "title": title2,
                    "start": datetime.datetime.strptime(start2, "%Y%m%d%H%M%S"),
                    "end": datetime.datetime.strptime(end2, "%Y%m%d%H%M%S")
                }
        return (current, nxt)

    def get_channels_with_show(self, filter_text: str, max_results: int = 100):
        now = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        rows = c.execute("""
            SELECT p.channel_id, p.title, p.start, p.end, ch.display_name
            FROM programmes p
            JOIN channels ch ON p.channel_id = ch.id
            WHERE LOWER(p.title) LIKE ? AND p.end >= ?
            ORDER BY p.start ASC
            LIMIT ?
        """, (f"%{filter_text.lower()}%", now, max_results*4)).fetchall()
        result = []
        for channel_id, show_title, start, end, channel_name in rows:
            result.append({
                "channel_id": channel_id,
                "show_title": show_title,
                "start": start,
                "end": end,
                "channel_name": channel_name
            })
        now_int = int(now)
        on_now = [r for r in result if int(r["start"]) <= now_int <= int(r["end"])]
        future = [r for r in result if int(r["start"]) > now_int]
        final = []
        added = set()
        for r in on_now + future:
            key = (r["channel_id"], r["show_title"])
            if key not in added:
                final.append(r)
                added.add(key)
            if len(final) >= max_results:
                break
        return final

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

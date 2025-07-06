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
import difflib

STRIP_TAGS = [
    'hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq', 'st',
    'us', 'usa', 'ca', 'canada', 'car', 'uk', 'u.k.', 'u.k', 'uk.', 'u.s.', 'u.s', 'us.', 'au', 'aus', 'nz', 'ie', 'eir', 'ire', 'irl'
]

NOISE_WORDS = [
    'backup', 'alt', 'feed', 'main', 'extra', 'mirror', 'test', 'temp',
    '1', '2', '3', '4', '5', '6', '7', '8', '9', '0',
    'sd', 'hd', 'fhd', 'uhd', '4k', '8k'
]

def group_synonyms():
    return {
        "us": [
            "us", "usa", "u.s.", "u.s", "us.", "united states", "united states of america", "america"
        ],
        "uk": [
            "uk", "u.k.", "u.k", "uk.", "gb", "great britain", "britain", "united kingdom", "england", "scotland", "wales"
        ],
        "ca": [
            "ca", "canada", "car", "ca:", "can"
        ],
        "au": [
            "au", "aus", "australia"
        ],
        "nz": [
            "nz", "new zealand"
        ],
        "ie": [
            "ie", "eir", "ire", "irl", "ireland"
        ],
    }

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

def strip_noise_words(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    pattern = r'\b(' + '|'.join(re.escape(w) for w in NOISE_WORDS) + r')\b'
    text = re.sub(pattern, '', text, flags=re.I)
    text = re.sub(r'[\s\-_]+', ' ', text)
    return text.strip()

def extract_group(title: str) -> str:
    if not title:
        return ''
    title = title.lower()
    for norm_tag, variants in group_synonyms().items():
        for v in variants:
            if re.search(r'\b' + re.escape(v) + r'\b', title):
                return norm_tag
    m = re.match(r'([a-z]{2,3})\b', title)
    if m:
        code = m.group(1)
        if code in group_synonyms():
            return code
    m = re.search(r'\(([a-z]{2,3})\)', title)
    if m:
        code = m.group(1)
        if code in group_synonyms():
            return code
    return ''

def tokenize_channel_name(name: str) -> set:
    if not name:
        return set()
    paren = re.findall(r'\(([^)]*)\)', name)
    outside = re.sub(r'\(.*?\)', '', name)
    words = re.findall(r'\w+', outside)
    paren_words = []
    for p in paren:
        paren_words.extend(re.findall(r'\w+', p))
    all_words = [w.lower() for w in words + paren_words if len(w) > 1]
    bad = set(STRIP_TAGS + NOISE_WORDS + [
        'channel', 'tv', 'the', 'and', 'for', 'with', 'on', 'in', 'f'
    ])
    tokens = set([w for w in all_words if w not in bad])
    return tokens

def strip_backup_terms(name: str) -> str:
    return strip_noise_words(name)

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
        norm = canonicalize_name(strip_backup_terms(display_name))
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

    def get_matching_channel_ids(self, channel: Dict[str, str]) -> List[dict]:
        def norm_group(tag: str) -> str:
            tag = tag or ''
            tag = tag.lower().strip()
            for norm, variants in group_synonyms().items():
                if tag == norm or tag in variants:
                    return norm
            return tag

        tvg_id = channel.get("tvg-id", "").strip()
        tvg_name = channel.get("tvg-name", "").strip()
        name = channel.get("name", "")
        group_tag = norm_group(extract_group(channel.get("group", "")) if channel.get("group") else '')

        c = self.conn.cursor()
        candidates = {}

        # 1. Exact ID
        if tvg_id:
            row = c.execute("SELECT id, group_tag, display_name FROM channels WHERE id = ?", (tvg_id,)).fetchone()
            if row:
                epg_group = norm_group(row[1])
                if not group_tag or not epg_group or group_tag == epg_group:
                    candidates[row[0]] = {'id': row[0], 'group_tag': epg_group, 'score': 100, 'display_name': row[2]}

        # 2. Exact canonicalized tvg-name
        if tvg_name:
            norm_tvg_name = canonicalize_name(strip_backup_terms(tvg_name))
            rows = c.execute("SELECT id, group_tag, display_name FROM channels WHERE norm_name = ?", (norm_tvg_name,)).fetchall()
            for r in rows:
                epg_group = norm_group(r[1])
                if not group_tag or not epg_group or group_tag == epg_group:
                    candidates[r[0]] = {'id': r[0], 'group_tag': epg_group, 'score': 95, 'display_name': r[2]}

        # 3. Exact canonicalized playlist name
        norm_name_pl = canonicalize_name(strip_backup_terms(name))
        rows = c.execute("SELECT id, group_tag, display_name FROM channels WHERE norm_name = ?", (norm_name_pl,)).fetchall()
        for r in rows:
            epg_group = norm_group(r[1])
            if not group_tag or not epg_group or group_tag == epg_group:
                candidates[r[0]] = {'id': r[0], 'group_tag': epg_group, 'score': 90, 'display_name': r[2]}

        # 4. Fuzzy (token overlap): allow if groups match or either group is blank
        target_tokens = tokenize_channel_name(name)
        rows_all = c.execute("SELECT id, display_name, group_tag FROM channels").fetchall()
        for ch_id, disp, grp in rows_all:
            epg_group = norm_group(grp)
            if group_tag and epg_group and group_tag != epg_group:
                continue
            epg_tokens = tokenize_channel_name(disp)
            overlap = target_tokens & epg_tokens
            if len(overlap) >= 2:
                score = 80 + len(overlap)
                if ch_id not in candidates or candidates[ch_id]['score'] < score:
                    candidates[ch_id] = {'id': ch_id, 'group_tag': epg_group, 'score': score, 'display_name': disp}

        # 5. Relaxed fuzzy fallback if either group is blank
        relaxed_target = re.sub(r'\s+', '', canonicalize_name(strip_backup_terms(name)))
        for ch_id, disp, grp in rows_all:
            epg_group = norm_group(grp)
            if group_tag and epg_group and group_tag != epg_group:
                continue
            cand = re.sub(r'\s+', '', canonicalize_name(strip_backup_terms(disp)))
            ratio = difflib.SequenceMatcher(None, relaxed_target, cand).ratio()
            if ratio >= 0.8:
                score = int(55 + 40*ratio)
                if ch_id not in candidates or candidates[ch_id]['score'] < score:
                    candidates[ch_id] = {'id': ch_id, 'group_tag': epg_group, 'score': score, 'display_name': disp}

        return list(candidates.values()), group_tag

    def get_now_next(self, channel: Dict[str, str]) -> Optional[tuple]:
        matches, group_tag = self.get_matching_channel_ids(channel)
        if not matches:
            return None
        now = datetime.datetime.now(datetime.UTC)
        now_str = now.strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        current_shows = []
        next_shows = []

        for match in matches:
            ch_id = match['id']
            grp = match['group_tag']
            group_priority = (grp == group_tag)
            row = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start <= ? AND end > ? ORDER BY start DESC LIMIT 1",
                (ch_id, now_str, now_str)).fetchone()
            if row:
                title, start, end = row
                current_shows.append({
                    "title": title,
                    "start": datetime.datetime.strptime(start, "%Y%m%d%H%M%S"),
                    "end": datetime.datetime.strptime(end, "%Y%m%d%H%M%S"),
                    "channel_id": ch_id,
                    "group_tag": grp,
                    "group_priority": group_priority,
                    "score": match.get("score", 0)
                })
            row2 = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start > ? ORDER BY start ASC LIMIT 1",
                (ch_id, now_str)).fetchone()
            if row2:
                title2, start2, end2 = row2
                next_shows.append({
                    "title": title2,
                    "start": datetime.datetime.strptime(start2, "%Y%m%d%H%M%S"),
                    "end": datetime.datetime.strptime(end2, "%Y%m%d%H%M%S"),
                    "channel_id": ch_id,
                    "group_tag": grp,
                    "group_priority": group_priority,
                    "score": match.get("score", 0)
                })
        def pick_best(showlist, is_now):
            if not showlist:
                return None
            showlist = sorted(showlist, key=lambda s: (not s["group_priority"], -s["score"], s["end" if is_now else "start"]))
            return showlist[0]

        now_show = pick_best(current_shows, True)
        next_show = pick_best(next_shows, False)
        return (now_show, next_show)

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

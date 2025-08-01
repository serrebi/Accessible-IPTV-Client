import os
import sys
import json
import hashlib
import datetime
import re
import wx
from typing import Dict
import tempfile

CONFIG_FILE = "iptvclient.conf"

def _is_writable_dir(path: str) -> bool:
    try:
        if not os.path.isdir(path):
            return False
        testfile = os.path.join(path, ".iptvclient_write_test.tmp")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("test")
        os.remove(testfile)
        return True
    except Exception:
        return False

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_cwd_dir():
    try:
        return os.getcwd()
    except Exception:
        return None

def get_config_read_candidates():
    # Strict priority: 1) CWD 2) App dir
    candidates = []
    cwd = get_cwd_dir()
    if cwd:
        candidates.append(os.path.join(cwd, CONFIG_FILE))
    candidates.append(os.path.join(get_app_dir(), CONFIG_FILE))
    return candidates

def get_config_write_target():
    # Write to CWD first if writable, else app dir if writable; else fallback to CWD/appdir path even if unwritable
    cwd = get_cwd_dir()
    if cwd and _is_writable_dir(cwd):
        return os.path.join(cwd, CONFIG_FILE)
    appdir = get_app_dir()
    if _is_writable_dir(appdir):
        return os.path.join(appdir, CONFIG_FILE)
    return os.path.join(cwd or appdir, CONFIG_FILE)

def get_config_path():
    # Return first existing config by read priority; else preferred write target
    for p in get_config_read_candidates():
        if os.path.exists(p):
            return p
    return get_config_write_target()

def load_config() -> Dict:
    default = {"playlists": [], "epgs": [], "media_player": "VLC", "custom_player_path": "", "minimize_to_tray": False}
    for p in get_config_read_candidates():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in default.items():
                        data.setdefault(k, v)
                    return data
            except Exception as e:
                wx.LogError(f"Failed to load config from {p}: {e}")
                break
    return default

def save_config(cfg: Dict):
    # Always save to preferred write target (CWD if writable)
    path = get_config_write_target()
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            os.rename(tmp_path, path)
    except Exception as e:
        wx.LogError(f"Failed to save config to {path}: {e}")

def get_cache_dir():
    cache_dir = os.path.join(tempfile.gettempdir(), "iptv_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def get_cache_path_for_url(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(get_cache_dir(), f"{h}.m3u")

def get_db_path():
    return os.path.join(tempfile.gettempdir(), "epg.db")

STRIP_TAGS = [
    'hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq', 'st',
    'us', 'usa', 'ca', 'canada', 'car', 'uk', 'u.k.', 'u.k', 'uk.', 'u.s.', 'u.s', 'us.', 'au', 'aus', 'nz'
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
    for norm_tag, variants in group_synonyms().items():
        for v in variants:
            if re.search(r'\b' + re.escape(v) + r'\b', title):
                return norm_tag
    m = re.match(r'([a-z]{2,3})', title)
    if m:
        code = m.group(1)
        if code in group_synonyms():
            return code
    return ''

def utc_to_local(dt):
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            dt = dt
    return dt.astimezone()

class CustomPlayerDialog(wx.Dialog):
    def __init__(self, parent, initial_path):
        super().__init__(parent, title="Select Custom Player")
        self.path = initial_path or ""
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.txt = wx.TextCtrl(self, value=self.path)
        browse = wx.Button(self, label="Browse...")
        btns = self.CreateButtonSizer(wx.OK|wx.CANCEL)
        sizer.Add(wx.StaticText(self, label="Enter player executable or path:"), 0, wx.ALL, 5)
        sizer.Add(self.txt, 0, wx.EXPAND|wx.ALL, 5)
        sizer.Add(browse, 0, wx.ALL, 5)
        sizer.Add(btns, 0, wx.ALL|wx.ALIGN_RIGHT, 5)
        self.SetSizerAndFit(sizer)
        browse.Bind(wx.EVT_BUTTON, self.on_browse)

    def on_browse(self, _):
        with wx.FileDialog(self, "Select Player Executable", style=wx.FD_OPEN) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt.SetValue(dlg.GetPath())

    def GetPath(self):
        return self.txt.GetValue()
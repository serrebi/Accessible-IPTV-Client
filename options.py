import os
import sys
import json
import hashlib
import datetime
import re
import wx
from typing import Dict

CONFIG_FILE = "iptvclient.conf"

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path():
    return os.path.join(get_base_path(), CONFIG_FILE)

def load_config() -> Dict:
    path = get_config_path()
    default = {"playlists": [], "epgs": [], "media_player": "VLC", "custom_player_path": ""}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k in default:
                        if k not in data:
                            data[k] = default[k]
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

def get_cache_dir():
    cache_dir = os.path.join(get_base_path(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def get_cache_path_for_url(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(get_cache_dir(), f"{h}.m3u")

def get_db_path():
    return os.path.join(get_base_path(), "epg.db")

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

def utc_to_local(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
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

import os
import sys
import json
import hashlib
import datetime
import re
try:
    import wx  # type: ignore
    _HAS_WX = True
except ModuleNotFoundError:  # wxPython optional for headless helpers
    wx = None  # type: ignore
    _HAS_WX = False
from typing import Dict
import tempfile

CONFIG_FILE = "iptvclient.conf"
_CONFIG_PATH = None  # Path of config last loaded/saved


def _log_error(message: str):
    """Log errors without requiring a wx.App (headless safe)."""
    app = None
    if _HAS_WX and hasattr(wx, "GetApp"):
        try:
            app = wx.GetApp()
        except Exception:
            app = None
    if _HAS_WX and app is not None:
        try:
            wx.LogError(message)
            return
        except Exception:
            pass
    sys.stderr.write(f"{message}\n")


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

def get_user_config_dir():
    """
    Gets the user-specific config directory, creating it if it doesn't exist.
    This relies on a wx.App object having been created with AppName set, but
    gracefully falls back when running headless or before wx.App exists.
    """
    app = None
    if _HAS_WX and hasattr(wx, "GetApp"):
        try:
            app = wx.GetApp()
        except Exception:
            app = None
    if _HAS_WX and app is not None:
        try:
            paths = wx.StandardPaths.Get()
            config_dir = paths.GetUserConfigDir()
            os.makedirs(config_dir, exist_ok=True)
            return config_dir
        except Exception:
            pass

    # Fallback for headless environments or if called before wx.App is created.
    if sys.platform == "win32":
        path = os.path.join(os.getenv('APPDATA', os.path.expanduser('~')), "IPTVClient")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser('~/Library/Application Support'), "IPTVClient")
    else:  # linux and other unix
        path = os.path.join(os.getenv('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), "IPTVClient")

    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        # Last resort if we can't create any directory
        return tempfile.gettempdir()

def get_config_read_candidates():
    # Revised priority to ensure the app-local config file is honored:
    # 1) App Dir (next to the code/executable)
    # 2) CWD (portable override when explicitly run from that folder)
    # 3) User Config Dir (standard per-user location)
    candidates = []

    app_dir = get_app_dir()
    if app_dir:
        candidates.append(os.path.join(app_dir, CONFIG_FILE))

    cwd = get_cwd_dir()
    if cwd:
        candidates.append(os.path.join(cwd, CONFIG_FILE))

    user_dir = get_user_config_dir()
    if user_dir:
        candidates.append(os.path.join(user_dir, CONFIG_FILE))

    # De-duplicate paths while preserving order
    unique_candidates = []
    seen = set()
    for c in candidates:
        if c not in seen:
            unique_candidates.append(c)
            seen.add(c)
    return unique_candidates

def get_config_write_target():
    # Prefer writing back to the file that was loaded, to avoid surprises.
    global _CONFIG_PATH
    if _CONFIG_PATH:
        try:
            parent = os.path.dirname(_CONFIG_PATH)
            if parent and _is_writable_dir(parent):
                return _CONFIG_PATH
        except Exception:
            pass

    # Otherwise, prefer App Dir, then CWD, then user config dir
    app_dir = get_app_dir()
    if app_dir and _is_writable_dir(app_dir):
        return os.path.join(app_dir, CONFIG_FILE)

    cwd = get_cwd_dir()
    if cwd and _is_writable_dir(cwd):
        return os.path.join(cwd, CONFIG_FILE)

    return os.path.join(get_user_config_dir(), CONFIG_FILE)

def load_config() -> Dict:
    global _CONFIG_PATH
    default = {
        "playlists": [],
        "epgs": [],
        "media_player": "VLC",
        "custom_player_path": "",
        "internal_player_buffer_seconds": 2.0,
        "minimize_to_tray": False,
        "epg_enabled": True
    }
    for p in get_config_read_candidates():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # Ensure all default keys are present
                    for k, v in default.items():
                        data.setdefault(k, v)
                    if data.get("internal_player_buffer_seconds") == 12.0:
                        data["internal_player_buffer_seconds"] = 2.0
                    _CONFIG_PATH = p
                    return data
            except Exception as e:
                _log_error(f"Failed to load config from {p}: {e}")
                # Do not break; try the next candidate location.
    # No config found; remember we are effectively using the App Dir path
    try:
        app_dir = get_app_dir()
        if app_dir:
            _CONFIG_PATH = os.path.join(app_dir, CONFIG_FILE)
    except Exception:
        _CONFIG_PATH = None
    return default

def save_config(cfg: Dict):
    global _CONFIG_PATH
    path = get_config_write_target()
    try:
        # Ensure the directory exists before writing; skip if writing to CWD
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
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
        _CONFIG_PATH = path
    except Exception as e:
        _log_error(f"Failed to save config to {path}: {e}")

def get_loaded_config_path() -> str:
    """Return the config path most recently loaded or saved, if known."""
    return _CONFIG_PATH or ""

def get_cache_dir():
    cache_dir = os.path.join(tempfile.gettempdir(), "iptv_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def get_cache_path_for_url(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(get_cache_dir(), f"{h}.m3u")

def get_db_path():
    return os.path.join(tempfile.gettempdir(), "epg.db")

# Strip from names when canonicalizing (NOT used to detect country)
STRIP_TAGS = [
    'hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq', 'st',
    'us', 'usa', 'ca', 'canada', 'car', 'uk', 'u.k.', 'u.k', 'uk.', 'u.s.', 'u.s', 'us.',
    'au', 'aus', 'nz', 'eu'
]

def group_synonyms():
    # Canonical country code -> variants
    return {
        # North America
        "us": ["us", "usa", "u.s.", "u.s", "us.", "united states", "united states of america", "america"],
        "ca": ["ca", "can", "canada", "car"],
        "mx": ["mx", "mex", "mexico", "méxico"],

        # UK + Ireland
        "uk": ["uk", "u.k.", "gb", "gbr", "great britain", "britain", "united kingdom", "england", "scotland", "wales", "northern ireland"],
        "ie": ["ie", "irl", "ireland", "eire", "éire"],

        # DACH
        "de": ["de", "ger", "deu", "germany", "deutschland"],
        "at": ["at", "aut", "austria", "österreich", "oesterreich"],
        "ch": ["ch", "che", "switzerland", "schweiz", "suisse", "svizzera"],

        # Benelux
        "nl": ["nl", "nld", "netherlands", "holland", "nederland"],
        "be": ["be", "bel", "belgium", "belgie", "belgië", "belgique"],
        "lu": ["lu", "lux", "luxembourg", "letzebuerg", "lëtzebuerg"],

        # Nordics
        "se": ["se", "swe", "sweden", "svenska", "sverige"],
        "no": ["no", "nor", "norway", "norge", "noreg"],
        "dk": ["dk", "dnk", "denmark", "danmark"],
        "fi": ["fi", "fin", "finland", "suomi"],
        "is": ["is", "isl", "iceland", "ísland"],

        # Southern Europe
        "fr": ["fr", "fra", "france", "français", "française"],
        "it": ["it", "ita", "italy", "italia"],
        "es": ["es", "esp", "spain", "españa", "espana", "español"],
        "pt": ["pt", "prt", "portugal", "português"],
        "gr": ["gr", "grc", "greece", "ελλάδα", "ellada"],
        "mt": ["mt", "mlt", "malta"],
        "cy": ["cy", "cyp", "cyprus"],

        # Central/Eastern Europe
        "pl": ["pl", "pol", "poland", "polska"],
        "cz": ["cz", "cze", "czech", "czechia", "cesko", "česko"],
        "sk": ["sk", "svk", "slovakia", "slovensko"],
        "hu": ["hu", "hun", "hungary", "magyar"],
        "si": ["si", "svn", "slovenia", "slovenija"],
        "hr": ["hr", "hrv", "croatia", "hrvatska"],
        "rs": ["rs", "srb", "serbia", "srbija"],
        "ba": ["ba", "bih", "bosnia", "bosnia and herzegovina", "bosna", "hercegovina"],
        "mk": ["mk", "mkd", "north macedonia", "macedonia"],
        "ro": ["ro", "rou", "romania", "românia"],
        "bg": ["bg", "bgr", "bulgaria", "българия", "balgariya"],
        "ua": ["ua", "ukr", "ukraine", "ukraina"],
        "by": ["by", "blr", "belarus"],
        "ru": ["ru", "rus", "russia", "россия", "rossiya"],
        "ee": ["ee", "est", "estonia", "eesti"],
        "lv": ["lv", "lva", "latvia", "latvija"],
        "lt": ["lt", "ltu", "lithuania", "lietuva"],

        # Balkans + nearby
        "al": ["al", "alb", "albania", "shqipëri", "shqiperia"],
        "me": ["me", "mne", "montenegro", "crna gora"],
        "xk": ["xk", "kosovo"],

        # MENA (subset)
        "tr": ["tr", "tur", "turkey", "türkiye", "turkiye"],
        "ma": ["ma", "mar", "morocco", "maroc"],
        "dz": ["dz", "dza", "algeria", "algérie"],
        "tn": ["tn", "tun", "tunisia", "tunisie"],
        "eg": ["eg", "egypt", "misr"],
        "il": ["il", "isr", "israel"],
        "sa": ["sa", "sau", "saudi", "saudi arabia"],
        "ae": ["ae", "are", "uae", "united arab emirates"],
        "qa": ["qa", "qat", "qatar"],
        "kw": ["kw", "kwt", "kuwait"],

        # Asia (subset)
        "in": ["in", "ind", "india", "bharat"],
        "pk": ["pk", "pak", "pakistan"],
        "bd": ["bd", "bgd", "bangladesh"],
        "lk": ["lk", "lka", "sri lanka"],
        "np": ["np", "npl", "nepal"],
        "cn": ["cn", "chn", "china"],
        "hk": ["hk", "hkg", "hong kong"],
        "tw": ["tw", "twn", "taiwan"],
        "jp": ["jp", "jpn", "japan", "日本"],
        "kr": ["kr", "kor", "korea", "south korea"],
        "sg": ["sg", "sgp", "singapore"],
        "my": ["my", "mys", "malaysia"],
        "th": ["th", "tha", "thailand"],
        "vn": ["vn", "vnm", "vietnam"],
        "ph": ["ph", "phl", "philippines"],
        "id": ["id", "idn", "indonesia"],

        # Oceania
        "au": ["au", "aus", "australia"],
        "nz": ["nz", "nzl", "new zealand", "aotearoa"],

        # Latin America (subset)
        "br": ["br", "bra", "brazil", "brasil"],
        "ar": ["ar", "arg", "argentina"],
        "cl": ["cl", "chl", "chile"],
        "co": ["co", "col", "colombia"],
        "pe": ["pe", "per", "peru", "perú"],
        "uy": ["uy", "ury", "uruguay"],
        "py": ["py", "pry", "paraguay"],
        "bo": ["bo", "bol", "bolivia"],
        "ec": ["ec", "ecu", "ecuador"],
        "ve": ["ve", "ven", "venezuela"],
        "cr": ["cr", "cri", "costa rica"],
        "pr": ["pr", "pri", "puerto rico"],

        # Africa (subset)
        "ng": ["ng", "nga", "nigeria"],
        "za": ["za", "zaf", "south africa"],
        "ke": ["ke", "ken", "kenya"],
        "gh": ["gh", "gha", "ghana"],
        "et": ["et", "eth", "ethiopia"],
        "tz": ["tz", "tza", "tanzania"],
        "ug": ["ug", "uga", "uganda"],
        "ci": ["ci", "civ", "côte d’ivoire", "ivory coast"],
        "sn": ["sn", "sen", "senegal"],
    }

def _build_reverse_country_lookup():
    lookup = {}
    for code, variants in group_synonyms().items():
        for v in variants:
            lookup[v.lower()] = code
    lookup["gb"] = "uk"
    lookup["gbr"] = "uk"
    return lookup

_COUNTRY_LOOKUP = _build_reverse_country_lookup()

def _normalize_country_token(tok: str) -> str:
    if not tok:
        return ''
    t = tok.strip().lower()
    if t in _COUNTRY_LOOKUP:
        return _COUNTRY_LOOKUP[t]
    t2 = t.replace('.', '')
    if t2 in _COUNTRY_LOOKUP:
        return _COUNTRY_LOOKUP[t2]
    return ''

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

def _search_country_in_text(text: str) -> str:
    if not text:
        return ''
    s = text.lower()
    for m in re.findall(r'[\(\[\{]([^\)\]\}]{2,24})[\)\]\}]', s):
        for token in re.findall(r'[a-zA-ZÀ-ÿ\.]+', m):
            code = _normalize_country_token(token)
            if code:
                return code
    for token in re.split(r'[\|\-\/:,–—]+', s):
        token = token.strip()
        code = _normalize_country_token(token)
        if code:
            return code
    for word in re.findall(r'[a-zA-ZÀ-ÿ\.]+', s):
        code = _normalize_country_token(word)
        if code:
            return code
    m = re.match(r'^\s*([a-zA-Z\.]{2,4})\b', s)
    if m:
        code = _normalize_country_token(m.group(1))
        if code:
            return code
    return ''

def extract_group(title: str) -> str:
    return _search_country_in_text(title or "")

def utc_to_local(dt):
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            dt = dt
    return dt.astimezone()

if _HAS_WX:
    class CustomPlayerDialog(wx.Dialog):  # type: ignore[misc]
        def __init__(self, parent, initial_path):
            super().__init__(parent, title="Select Custom Player")
            self.path = initial_path or ""
            sizer = wx.BoxSizer(wx.VERTICAL)
            self.txt = wx.TextCtrl(self, value=self.path)
            browse = wx.Button(self, label="Browse...")
            btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
            sizer.Add(wx.StaticText(self, label="Enter player executable or path:"), 0, wx.ALL, 5)
            sizer.Add(self.txt, 0, wx.EXPAND | wx.ALL, 5)
            sizer.Add(browse, 0, wx.ALL, 5)
            sizer.Add(btns, 0, wx.ALL | wx.ALIGN_RIGHT, 5)
            self.SetSizerAndFit(sizer)
            browse.Bind(wx.EVT_BUTTON, self.on_browse)

        def on_browse(self, _):
            with wx.FileDialog(self, "Select Player Executable", style=wx.FD_OPEN) as dlg:
                if dlg.ShowModal() == wx.ID_OK:
                    self.txt.SetValue(dlg.GetPath())

        def GetPath(self):
            return self.txt.GetValue()
else:
    class CustomPlayerDialog:  # type: ignore[too-many-ancestors]
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("CustomPlayerDialog requires wxPython. Install wxPython to use this dialog.")

        def GetPath(self):
            return ""

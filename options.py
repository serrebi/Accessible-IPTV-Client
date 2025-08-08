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
    candidates = []
    cwd = get_cwd_dir()
    if cwd:
        candidates.append(os.path.join(cwd, CONFIG_FILE))
    candidates.append(os.path.join(get_app_dir(), CONFIG_FILE))
    return candidates

def get_config_write_target():
    cwd = get_cwd_dir()
    if cwd and _is_writable_dir(cwd):
        return os.path.join(cwd, CONFIG_FILE)
    appdir = get_app_dir()
    if _is_writable_dir(appdir):
        return os.path.join(appdir, CONFIG_FILE)
    return os.path.join(cwd or appdir, CONFIG_FILE)

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

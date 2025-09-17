import os
import re
import io
import wx
import gzip
import time
import json
import tracemalloc
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import datetime
import logging
import logging.handlers
import tempfile
import random
from providers import generate_provider_id
from typing import Dict, List, Optional, Tuple, Set


class _FieldAccessible(wx.Accessible):
    def __init__(self, label: str, description: str):
        super().__init__()
        self._label = label
        self._description = description

    def GetName(self, childId):
        if childId in (0, wx.ACC_SELF):
            return wx.ACC_OK, self._label
        return wx.ACC_NOT_IMPLEMENTED, None

    def GetDescription(self, childId):
        if childId in (0, wx.ACC_SELF):
            return wx.ACC_OK, self._description
        return wx.ACC_NOT_IMPLEMENTED, None

# =========================
# Debug logging (rotating file) + memory helpers
# =========================

DEBUG = True if os.getenv("EPG_DEBUG", "1").strip() not in {"0", "false", "False"} else False
# FIX: Write log to temp dir to avoid permission errors on startup
LOG_PATH = os.path.join(tempfile.gettempdir(), "iptvclient_epg_debug.log")
_logger = logging.getLogger("EPG")
if not _logger.handlers:
    _logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    try:
        _fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
        _fh.setFormatter(_fmt)
        _logger.addHandler(_fh)
        # Also mirror to stderr while debugging (harmless if GUI)
        if DEBUG:
            _sh = logging.StreamHandler()
            _sh.setFormatter(_fmt)
            _logger.addHandler(_sh)
        _logger.debug("EPG debug logging initialized. File: %s", LOG_PATH)
    except Exception as e:
        # If logging setup fails, don't crash the app. Print error and continue.
        print(f"FATAL: Could not initialize logger at {LOG_PATH}. Error: {e}")


def _mem_mb() -> int:
    try:
        import psutil  # optional
        p = psutil.Process(os.getpid())
        return int(p.memory_info().rss / (1024 * 1024))
    except Exception:
        try:
            import resource  # *nix
            # ru_maxrss is in kilobytes on Linux, bytes on macOS; normalize to MB best-effort
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if rss_kb > 1024 * 1024:  # assume bytes (macOS)
                return int(rss_kb / (1024 * 1024))
            return int(rss_kb / 1024)
        except Exception:
            return -1

def _safe(s: str, n=200) -> str:
    s = str(s or "")
    return (s[:n] + "...") if len(s) > n else s

# =========================
# Normalization & Tokenizing
# =========================

STRIP_TAGS = [
    'hd', 'sd', 'hevc', 'fhd', 'uhd', '4k', '8k', 'hdr', 'dash', 'hq', 'st',
    'us', 'usa', 'ca', 'canada', 'car', 'uk', 'u.k.', 'u.k', 'uk.', 'u.s.', 'u.s', 'us.',
    'au', 'aus', 'nz'
]

NOISE_WORDS = [
    'backup', 'alt', 'feed', 'main', 'extra', 'mirror', 'test', 'temp',
    '1', '2', '3', '4', '5', '6', '7', '8', '9', '0',
    'sd', 'hd', 'fhd', 'uhd', '4k', '8k', 'plus', 'live', 'network'
]

def group_synonyms():
    return {
        "us": ["us","usa","u.s.","u.s","us.","united states","united states of america","america"],
        "ca": ["ca","can","canada","car"],
        "mx": ["mx","mex","mexico","méxico"],
        "uk": ["uk","u.k.","gb","gbr","great britain","britain","united kingdom","england","scotland","wales","northern ireland"],
        "ie": ["ie","irl","ireland","eire","éire"],
        "de": ["de","ger","deu","germany","deutschland"],
        "at": ["at","aut","austria","österreich","oesterreich"],
        "ch": ["ch","che","switzerland","schweiz","suisse","svizzera"],
        "nl": ["nl","nld","netherlands","holland","nederland"],
        "be": ["be","bel","belgium","belgie","belgië","belgique"],
        "lu": ["lu","lux","luxembourg","letzebuerg","lëtzebuerg"],
        "se": ["se","swe","sweden","svenska","sverige"],
        "no": ["no","nor","norway","norge","noreg"],
        "dk": ["dk","dnk","denmark","danmark"],
        "fi": ["fi","fin","finland","suomi"],
        "is": ["is","isl","iceland","ísland"],
        "fr": ["fr","fra","france","français","française"],
        "it": ["it","ita","italy","italia"],
        "es": ["es","esp","spain","españa","espana","español"],
        "pt": ["pt","prt","portugal","português"],
        "gr": ["gr","grc","greece","ελλάδα","ellada"],
        "mt": ["mt","mlt","malta"],
        "cy": ["cy","cyp","cyprus"],
        "pl": ["pl","pol","poland","polska"],
        "cz": ["cz","cze","czech","czechia","cesko","česko"],
        "sk": ["sk","svk","slovakia","slovensko"],
        "hu": ["hu","hun","hungary","magyar"],
        "si": ["si","svn","slovenia","slovenija"],
        "hr": ["hr","hrv","croatia","hrvatska"],
        "rs": ["rs","srb","serbia","srbija"],
        "ba": ["ba","bih","bosnia","bosnia and herzegovina","bosna","hercegovina"],
        "mk": ["mk","mkd","north macedonia","macedonia"],
        "ro": ["ro","rou","romania","românia"],
        "bg": ["bg","bgr","bulgaria","българия","balgariya"],
        "ua": ["ua","ukr","ukraine","ukraina"],
        "by": ["by","blr","belarus"],
        "ru": ["ru","rus","russia","россия","rossiya"],
        "ee": ["ee","est","estonia","eesti"],
        "lv": ["lv","lva","latvia","latvija"],
        "lt": ["lt","ltu","lithuania","lietuva"],
        "al": ["al","alb","albania","shqipëri","shqiperia"],
        "me": ["me","mne","montenegro","crna gora"],
        "xk": ["xk","kosovo"],
        "tr": ["tr","tur","turkey","türkiye","turkiye"],
        "ma": ["ma","mar","morocco","maroc"],
        "dz": ["dz","dza","algeria","algérie"],
        "tn": ["tn","tun","tunisia","tunisie"],
        "eg": ["eg","egypt","misr"],
        "il": ["il","isr","israel"],
        "sa": ["sa","sau","saudi","saudi arabia"],
        "ae": ["ae","are","uae","united arab emirates"],
        "qa": ["qa","qat","qatar"],
        "kw": ["kw","kwt","kuwait"],
        "in": ["in","ind","india","bharat"],
        "pk": ["pk","pak","pakistan"],
        "bd": ["bd","bgd","bangladesh"],
        "lk": ["lk","lka","sri lanka"],
        "np": ["np","npl","nepal"],
        "cn": ["cn","chn","china"],
        "hk": ["hk","hkg","hong kong"],
        "tw": ["tw","twn","taiwan"],
        "jp": ["jp","jpn","japan","日本"],
        "kr": ["kr","kor","korea","south korea"],
        "sg": ["sg","sgp","singapore"],
        "my": ["my","mys","malaysia"],
        "th": ["th","tha","thailand"],
        "vn": ["vn","vnm","vietnam"],
        "ph": ["ph","phl","philippines"],
        "id": ["id","idn","indonesia"],
        "au": ["au","aus","australia"],
        "nz": ["nz","nzl","new zealand","aotearoa"],
        "br": ["br","bra","brazil","brasil"],
        "ar": ["ar","arg","argentina"],
        "cl": ["cl","chl","chile"],
        "co": ["co","col","colombia"],
        "pe": ["pe","per","peru","perú"],
        "uy": ["uy","ury","uruguay"],
        "py": ["py","pry","paraguay"],
        "bo": ["bo","bolivia"],
        "ec": ["ec","ecu","ecuador"],
        "ve": ["ve","ven","venezuela"],
        "cr": ["cr","cri","costa rica"],
        "pr": ["pr","pri","puerto rico"],
        "ng": ["ng","nga","nigeria"],
        "za": ["za","zaf","south africa"],
        "ke": ["ke","ken","kenya"],
        "gh": ["gh","gha","ghana"],
        "et": ["et","eth","ethiopia"],
        "tz": ["tz","tza","tanzania"],
        "ug": ["ug","uga","uganda"],
        "ci": ["ci","civ","côte d’ivoire","ivory coast"],
        "sn": ["sn","sen","senegal"],
    }

def canonicalize_name(name: str) -> str:
    name = (name or "").strip().lower()
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
    tokens = set([w for w in all_words if w not in bad and not w.isdigit()])
    return tokens

def strip_backup_terms(name: str) -> str:
    return strip_noise_words(name)

# =========================
# Matching helpers
# =========================

MATCH_DEBUG = bool(os.environ.get("EPG_MATCH_DEBUG")) or DEBUG

ZONE_SYNONYMS = {
    "east": {"east", "e", "eastern"},
    "west": {"west", "w", "western"},
    "central": {"central", "c", "ct", "ctr"},
    "mountain": {"mountain", "mtn"},
    "pacific": {"pacific", "p", "pt", "pst", "pdt", "pac"},
    "atlantic": {"atlantic", "atl"},
}

def _mk(*xs):
    return {x.lower() for x in xs if x}

# ==== HBO Variant Helpers ====
_HBO_VARIANTS = ("base", "1", "2", "family", "latino", "signature", "comedy", "hits", "zone", "plus")

def _extract_hbo_variant(raw_text: str) -> str:
    if not raw_text:
        return ""
    s = raw_text.lower()
    if re.search(r'\bfamily\b', s): return "family"
    if re.search(r'\blatino\b', s): return "latino"
    if re.search(r'\bsignature\b', s): return "signature"
    if re.search(r'\bcomedy\b', s): return "comedy"
    if re.search(r'\bhits\b', s): return "hits"
    if re.search(r'\bzone\b', s): return "zone"
    if re.search(r'\bplus\b', s): return "plus"
    if re.search(r'\bhbo[\s\-]*1\b', s) or re.search(r'\b1\b', s): return "1"
    if re.search(r'\bhbo[\s\-]*2\b', s) or re.search(r'\b2\b', s): return "2"
    if re.search(r'\bhbo\b', s): return "base"
    return ""

def _normalize_hbo_variant(country_code: str, variant: str) -> str:
    v = (variant or "").strip().lower()
    cc = (country_code or "").strip().lower()
    if not v:
        return ""
    if cc == "us" and v == "1":
        return "base"
    return v

def _is_hbo_family(text: str) -> bool:
    return bool(text and re.search(r'\bhbo\b', text.lower()))

AFFILIATE_MARKETS: Dict[str, Dict[str, Dict[str, Set[str]]]] = {
    "ca": {
        "cbc": {"vancouver-bc": _mk("vancouver","bc"), "calgary-ab": _mk("calgary","ab"), "edmonton-ab": _mk("edmonton","ab"),
                "saskatoon-sk": _mk("saskatoon","sk"), "regina-sk": _mk("regina","sk"), "winnipeg-mb": _mk("winnipeg","mb"),
                "ottawa-on": _mk("ottawa","on"), "toronto-on": _mk("toronto","on"), "montreal-qc": _mk("montreal","montréal","qc"),
                "halifax-ns": _mk("halifax","ns"), "stjohns-nl": _mk("st johns","st. johns","nl")},
        "ctv": {"vancouver-bc": _mk("vancouver","bc"), "calgary-ab": _mk("calgary","ab"), "edmonton-ab": _mk("edmonton","ab"),
                "saskatoon-sk": _mk("saskatoon","sk"), "regina-sk": _mk("regina","sk"), "winnipeg-mb": _mk("winnipeg","mb"),
                "ottawa-on": _mk("ottawa","on"), "toronto-on": _mk("toronto","on"), "london-on": _mk("london","on"),
                "montreal-qc": _mk("montreal","montréal","qc"), "halifax-ns": _mk("halifax","ns")},
        "ctv2": {"vancouver-bc": _mk("vancouver","bc"), "ottawa-on": _mk("ottawa","on"), "london-on": _mk("london","on"), "windsor-on": _mk("windsor","on")},
        "citytv": {"vancouver-bc": _mk("vancouver","bc"), "calgary-ab": _mk("calgary","ab"), "edmonton-ab": _mk("edmonton","ab"),
                   "winnipeg-mb": _mk("winnipeg","mb"), "toronto-on": _mk("toronto","on"), "montreal-qc": _mk("montreal","montréal","qc")},
        "global": {"vancouver-bc": _mk("vancouver","bc","british columbia","global bc"), "calgary-ab": _mk("calgary","ab"),
                   "edmonton-ab": _mk("edmonton","ab"), "saskatoon-sk": _mk("saskatoon","sk"), "regina-sk": _mk("regina","sk"),
                   "winnipeg-mb": _mk("winnipeg","mb"), "toronto-on": _mk("toronto","on"), "montreal-qc": _mk("montreal","montréal","qc"),
                   "halifax-ns": _mk("halifax","ns")},
        "tsn": {"tsn1-west": _mk("tsn1","west","bc","ab","pacific","mountain"), "tsn2-central": _mk("central"),
                "tsn3-prairies": _mk("prairies","mb","sk"), "tsn4-ontario": _mk("ontario","on","toronto"),
                "tsn5-east": _mk("east","ottawa","montreal","qc","atlantic")},
        "sportsnet": {"pacific": _mk("pacific","bc","vancouver"), "west": _mk("west","ab","calgary","edmonton"),
                      "prairies": _mk("prairies","sk","mb"), "ontario": _mk("ontario","on","toronto"),
                      "east": _mk("east","qc","montreal","atlantic"), "one": _mk("sn1","sportsnet one","one"),
                      "360": _mk("sportsnet 360","sn360","360")},
        "tva": {"montreal-qc": _mk("montreal","montréal","qc")},
        "noovo": {"montreal-qc": _mk("montreal","montréal","qc")},
        "hbo": {},
    },
    "us": {"abc": {}, "nbc": {}, "cbs": {}, "fox": {}, "pbs": {}, "cw": {}, "mynetwork": {}, "telemundo": {}, "univision": {}, "hbo": {}},
    "uk": {"bbc one": {}, "bbc two": {}, "itv": {}, "sky crime": {}, "sky mix": {}, "sky max": {}},
    "de": {"ard": {}, "wdr": {}, "ndr": {}, "mdr": {}, "br": {}, "hr": {}, "rbb": {}, "swr": {}},
    "au": {"abc": {}, "seven": {}, "nine": {}, "ten": {}, "sbs": {}},
    "nz": {"tvnz 1": {}, "tvnz 2": {}, "three": {}},
}

AFFILIATE_BRANDS: Set[str] = {
    "cbc","ctv","ctv2","citytv","global","tva","noovo","tsn","sportsnet",
    "abc","nbc","cbs","fox","pbs","cw","mynetwork","telemundo","univision",
    "bbc one","bbc two","itv","channel 4","channel 5","sky crime","sky mix","sky max",
    "ard","wdr","ndr","mdr","br","hr","rbb","swr","seven","nine","ten","sbs","tvnz 1","tvnz 2","three",
    "hbo",
}

def _reverse_country_lookup():
    rev = {}
    for code, variants in group_synonyms().items():
        rev[code] = code
        for v in variants:
            rev[v.lower()] = code
        rev["gb"] = "uk"
        rev["gbr"] = "uk"
    return rev

_COUNTRY_LOOKUP = _reverse_country_lookup()

def _norm_country(tok: str) -> str:
    if not tok:
        return ''
    t = tok.strip().lower().replace('.', '')
    return _COUNTRY_LOOKUP.get(t, '')

def _detect_region_from_id(ch_id: str) -> str:
    if not ch_id:
        return ''
    s = ch_id.lower()
    parts = re.split(r'[.\-_:|/]+', s)
    for token in (list(reversed(parts)) + parts):
        code = _norm_country(token)
        if code:
            return code
    m = re.search(r'([a-z]{2,3})$', s)
    if m:
        code = _norm_country(m.group(1))
        if code:
            return code
    return ''

_TS_REGEXES = [
    re.compile(r'(?<!\w)\+(\d{1,2})\s*(?:h|hr|hour|hours)?(?!\w)', re.I),
    re.compile(r'\bplus\s*(\d{1,2})\b', re.I),
]
def _detect_timeshift(text: str) -> int:
    if not text:
        return 0
    s = str(text)
    for rx in _TS_REGEXES:
        m = rx.search(s)
        if m:
            try:
                v = int(m.group(1))
                if 0 < v <= 24:
                    return v
            except Exception:
                pass
    return 0

def _detect_zone(text: str) -> str:
    if not text:
        return ''
    s = text.lower()
    for zone, toks in ZONE_SYNONYMS.items():
        for t in toks:
            if re.search(r'\b' + re.escape(t) + r'\b', s):
                return zone
    return ''

_CALLSIGN_CORE_RX = re.compile(r'\b([A-Z]{3,5})(?:\s*-\s*(?:TV|DT|DT\d|HD))?\b', re.I)
_CALLSIGN_PREFIXES = ('K','W','C')
def extract_callsigns(text: str) -> Set[str]:
    out = set()
    if not text:
        return out
    s = re.sub(r'[\[\]\(\)]', ' ', str(text).upper())
    for token in re.findall(r'[A-Z0-9\-]{3,8}', s):
        parts = re.split(r'[^A-Z0-9]+', token)
        for p in parts:
            if not p:
                continue
            m = _CALLSIGN_CORE_RX.match(p)
            core = None
            if m:
                core = m.group(1)
            else:
                m2 = re.match(r'^([A-Z]{3,5})(?:DT\d?|DT|TV|HD)?$', p)
                if m2:
                    core = m2.group(1)
            if core and core[0] in _CALLSIGN_PREFIXES:
                if core not in {"NEWS","SPORT","LIVE","PLUS","MAX"} and len(core) >= 3:
                    out.add(core)
    return out

def callsign_overlap_score(pl_calls: Set[str], epg_calls: Set[str]) -> Tuple[int, str]:
    if not pl_calls or not epg_calls:
        return 0, ''
    if pl_calls & epg_calls:
        return 100, '+callsign-exact'
    for a in pl_calls:
        for b in epg_calls:
            if a == b:
                return 100, '+callsign-exact'
            if a in b or b in a:
                return 70, '+callsign-core'
    return 0, ''

def _brand_key(name: str) -> str:
    n = canonicalize_name(strip_noise_words(name or ""))
    for toks in ZONE_SYNONYMS.values():
        n = re.sub(r'\b(' + '|'.join(re.escape(t) for t in toks) + r')\b', ' ', n, flags=re.I)
    n = re.sub(r'(?<!\w)\+\d{1,2}(?!\w)', ' ', n)
    n = re.sub(r'[^a-z0-9]+', '', n.lower())
    return n

def _reverse_brand_lookup(text: str) -> str:
    t = (text or "").lower()
    if "bbc one" in t: return "bbc one"
    if "bbc two" in t: return "bbc two"
    if "itv" in t: return "itv"
    if "citytv" in t or re.search(r'\bcity\b', t): return "citytv"
    if "ctv2" in t: return "ctv2"
    if re.search(r'\bctv\b', t): return "ctv"
    if re.search(r'\bcbc\b', t): return "cbc"
    if "global" in t: return "global"
    if re.search(r'\btsn\b', t): return "tsn"
    if "sportsnet" in t or "sn1" in t or re.search(r'\bsn\b', t): return "sportsnet"
    if re.search(r'\babc\b', t): return "abc"
    if re.search(r'\bnbc\b', t): return "nbc"
    if re.search(r'\bcbs\b', t): return "cbs"
    if re.search(r'\bfox\b', t) and "fox news" not in t: return "fox"
    if re.search(r'\bpbs\b', t): return "pbs"
    if re.search(r'\bcw\b', t): return "cw"
    if "my network" in t or re.search(r'\bmyn\b', t): return "mynetwork"
    if "telemundo" in t: return "telemundo"
    if "univision" in t: return "univision"
    if "wdr" in t: return "wdr"
    if "ndr" in t: return "ndr"
    if "mdr" in t: return "mdr"
    if "rbb" in t: return "rbb"
    if "swr" in t: return "swr"
    if "sky crime" in t: return "sky crime"
    if "sky mix" in t or re.search(r'\bsky\s*mix\b', t): return "sky mix"
    if "sky max" in t or re.search(r'\bsky\s*max\b', t): return "sky max"
    if re.search(r'\bbr\b', t): return "br"
    if re.search(r'\bhr\b', t): return "hr"
    if re.search(r'\bhbo\b', t):
        return "hbo"
    return ""

def _normalize_str(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').strip().lower())

_US_STATE_NAMES = {
    "alabama":"al","alaska":"ak","arizona":"az","arkansas":"ar","california":"ca","colorado":"co","connecticut":"ct",
    "delaware":"de","florida":"fl","georgia":"ga","hawaii":"hi","idaho":"id","illinois":"il","indiana":"in","iowa":"ia",
    "kansas":"ks","kentucky":"ky","louisiana":"la","maine":"me","maryland":"md","massachusetts":"ma","michigan":"mi",
    "minnesota":"mn","mississippi":"ms","missouri":"mo","montana":"mt","nebraska":"ne","nevada":"nv","new hampshire":"nh",
    "new jersey":"nj","new mexico":"nm","new york":"ny","north carolina":"nc","north dakota":"nd","ohio":"oh","oklahoma":"ok",
    "oregon":"or","pennsylvania":"pa","rhode island":"ri","south carolina":"sc","south dakota":"sd","tennessee":"tn",
    "texas":"tx","utah":"ut","vermont":"vt","virginia":"va","washington":"wa","west virginia":"wv","wisconsin":"wi","wyoming":"wy",
    "district of columbia":"dc","washington, dc":"dc","washington dc":"dc"
}

def _market_tokens_for(country: str, brand: str, text: str) -> Tuple[Set[str], Set[str], Set[str]]:
    markets = set()
    provinces = set()
    cities = set()
    s = _normalize_str(text)

    for tok in re.findall(r'\b[a-z]{2,3}\b', s):
        t = tok.lower()
        if country == "ca" and t in {"bc","ab","sk","mb","on","qc","ns","nl","nb","pe","yt","nt","nu"}:
            provinces.add(t)
        elif country == "us" and t in {
            "ny","nj","pa","ma","ct","ri","nh","vt","me","dc","va","md","de","nc","sc","ga","fl","al","ms","la","tx","ok","nm","az","ca","or","wa","nv","ut","co","wy","mt","id",
            "nd","sd","ne","ks","mn","ia","mo","il","in","oh","mi","wi","tn","ky","wv","ar"
        }:
            provinces.add(t)
        elif country == "uk" and t in {"ni"}:
            provinces.add("ni")

    if country == "us":
        for full, abbr in _US_STATE_NAMES.items():
            if re.search(r'\b' + re.escape(full) + r'\b', s):
                provinces.add(abbr)

    markets_map = AFFILIATE_MARKETS.get(country, {}).get((brand or "").lower(), {})
    if markets_map:
        for mk, syns in markets_map.items():
            for syn in syns:
                if re.search(r'\b' + re.escape(syn) + r'\b', s):
                    markets.add(mk)
                    if '-' in mk:
                        cities.add(mk.split('-')[0])

    if country == "us":
        calls = extract_callsigns(text)
        if calls:
            markets |= {c.lower() for c in calls}
        MAJOR_US_CITIES = {
            "new york","los angeles","chicago","philadelphia","dallas","san francisco","washington","houston",
            "atlanta","boston","phoenix","seattle","tacoma","detroit","tampa","minneapolis","miami","denver","orlando",
            "cleveland","sacramento","st louis","portland","pittsburgh","raleigh","charlotte","baltimore",
            "indianapolis","san diego","nashville","salt lake","san antonio","kansas city","columbus","milwaukee",
            "cincinnati","austin","las vegas","new orleans","memphis","oklahoma city","albuquerque","boise","anchorage",
            "birmingham","charleston","charlottesville","chattanooga","dayton","des moines","el paso","fort worth","grand rapids",
            "greensboro","greenville","hartford","jacksonville","knoxville","louisville","madison","norfolk","omaha",
            "providence","richmond","rochester","roanoke","san jose","spokane","springfield","toledo","tucson","tulsa"
        }
        for city in MAJOR_US_CITIES:
            if re.search(r'\b' + re.escape(city) + r'\b', s):
                cities.add(city)

    return markets, provinces, cities

# =========================
# XMLTV time parsing to UTC (ROBUST & EXCEPTION-SAFE)
# =========================

_XMLTV_TS_RX = re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+\-]\d{4})?$')

def _parse_xmltv_to_utc_str(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    try:
        if 'T' in s:
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            elif re.search(r'[+\-]\d{4}$', s):
                s = s[:-2] + ':' + s[-2:]
            dt = datetime.datetime.fromisoformat(s)
        else: # Handle formats like "YYYYMMDDHHMMSS +ZZZZ"
            m = _XMLTV_TS_RX.match(s)
            if not m: return None
            dt_str, offset_str = "".join(m.groups()[:6]), m.group(7)
            dt = datetime.datetime.strptime(dt_str, "%Y%m%d%H%M%S")
            if offset_str:
                offset_val = int(offset_str)
                tz = datetime.timezone(datetime.timedelta(hours=offset_val//100, minutes=offset_val%100))
                dt = dt.replace(tzinfo=tz)
            else:
                 dt = dt.replace(tzinfo=datetime.timezone.utc)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


# ---- duration helpers ----

_ISO8601_DUR_RX = re.compile(
    r'^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$',
    re.I
)

def _parse_duration_to_seconds(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    # Try integer minutes (XMLTV <length> default is minutes)
    if re.match(r'^\d+$', s):
        try:
            return int(s) * 60
        except Exception:
            return None
    m = _ISO8601_DUR_RX.match(s)
    if m:
        days = int(m.group('days') or 0)
        hours = int(m.group('hours') or 0)
        minutes = int(m.group('minutes') or 0)
        seconds = int(m.group('seconds') or 0)
        return days*86400 + hours*3600 + minutes*60 + seconds
    return None

def _calc_end_from_length_or_duration(start_utc: Optional[str], elem: ET.Element) -> Optional[str]:
    """If provider uses <length units="minutes"> or <duration>PT...,
    compute end time. Returns UTC "YYYYMMDDHHMMSS" or None."""
    if not start_utc:
        return None
    # <length units="minutes">NN</length> or units="seconds"
    length_elem = elem.find(".//length")
    dur_seconds = None
    if length_elem is not None and (length_elem.text or "").strip():
        units = (length_elem.get("units") or "").strip().lower()
        try:
            val = float(length_elem.text.strip())
        except Exception:
            val = None
        if val is not None:
            if units in {"", "minute", "minutes", "mins", "min"}:
                dur_seconds = int(val * 60)
            elif units in {"second", "seconds", "sec", "secs"}:
                dur_seconds = int(val)
    if dur_seconds is None:
        # <duration>PT1H30M</duration>
        dur_elem = elem.find(".//duration")
        if dur_elem is not None and (dur_elem.text or "").strip():
            dur_seconds = _parse_duration_to_seconds(dur_elem.text.strip())
    if dur_seconds is None:
        return None
    try:
        st = datetime.datetime.strptime(start_utc, "%Y%m%d%H%M%S")
        end_dt = st + datetime.timedelta(seconds=dur_seconds)
        return end_dt.strftime("%Y%m%d%H%M%S")
    except Exception:
        return None

# =========================
# DB PRAGMAs
# =========================

PRAGMA_IMPORT = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA temp_store=MEMORY;",
    "PRAGMA mmap_size=268435456;",   # 256MB
    "PRAGMA cache_size=-131072;",    # ~128MB
    "PRAGMA wal_autocheckpoint=0;",  # we'll checkpoint manually
    "PRAGMA busy_timeout=5000;",
]

PRAGMA_READONLY = [
    "PRAGMA busy_timeout=2000;",
    "PRAGMA read_uncommitted=1;",    # let readers proceed during writer txn
]

# =========================
# EPG Database
# =========================

class EPGDatabase:
    def __init__(self, db_path: str, readonly: bool = False, for_threading: bool = False):
        self.db_path = db_path
        self.readonly = readonly
        self.for_threading = for_threading
        self._open()

    def _open(self):
        if self.readonly:
            # Open as read-only, shared cache; set reader-friendly pragmas
            uri = f"file:{self.db_path}?mode=ro&cache=shared"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            for p in PRAGMA_READONLY:
                try:
                    self.conn.execute(p)
                except Exception:
                    pass
        else:
            # Writer/normal
            self.conn = sqlite3.connect(self.db_path, check_same_thread=not self.for_threading)
            for p in PRAGMA_IMPORT:
                try:
                    self.conn.execute(p)
                except Exception:
                    pass
        self._create_tables()

    def _create_tables(self):
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
        # Indexes crucial for fast lookups
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start ON programmes (channel_id, start)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_end ON programmes (channel_id, end)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start_end ON programmes (channel_id, start, end)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_title ON programmes (title)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_channels_norm ON channels (norm_name)")
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def insert_channel(self, channel_id: str, display_name: str):
        name_region = extract_group(display_name)
        id_region = _detect_region_from_id(channel_id or "")
        group_tag = name_region or id_region or ''
        norm = canonicalize_name(strip_noise_words(display_name))
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO channels (id, display_name, norm_name, group_tag) VALUES (?, ?, ?, ?)",
            (channel_id, display_name, norm, group_tag)
        )

    def insert_programme(self, channel_id: str, title: str, start_utc: str, end_utc: str):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO programmes (channel_id, title, start, end) VALUES (?, ?, ?, ?)",
                  (channel_id, title, start_utc, end_utc))

    def prune_old_programmes(self, days: int = 7):
        utcnow = self._utcnow()
        cutoff = (utcnow - datetime.timedelta(days=days)).strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        c.execute("DELETE FROM programmes WHERE end < ?", (cutoff,))
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    # ---------- Candidate selection (fast; no full table scan) ----------
    def _candidate_rows(self, c, name: str, tvg_name: str, region: str) -> List[Tuple[str, str, str]]:
        """
        Return a limited set of likely channel rows: (id, display_name, group_tag)
        Strategy:
          1) exact norm_name for tvg_name and name
          2) brand-key LIKE
          3) token LIKEs (up to 3 tokens)
          4) restrict to region/unknown region when available
        """
        out: List[Tuple[str, str, str]] = []
        seen: Set[str] = set()

        def add_rows(rows):
            for r in rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    out.append(r)

        norm_name = canonicalize_name(strip_noise_words(name))
        norm_tvg = canonicalize_name(strip_noise_words(tvg_name))
        brand = _brand_key(name)
        tokens = list(tokenize_channel_name(name))[:3]

        region_clause = ""
        params_region: List[str] = []
        if region:
            region_clause = " AND (group_tag = ? OR group_tag = '') "
            params_region = [region]

        # 1) exact norm matches
        if norm_tvg:
            rows = c.execute(
                f"SELECT id, display_name, group_tag FROM channels WHERE norm_name = ? {region_clause} LIMIT 100",
                [norm_tvg] + params_region
            ).fetchall()
            add_rows(rows)
        if norm_name and norm_name != norm_tvg:
            rows = c.execute(
                f"SELECT id, display_name, group_tag FROM channels WHERE norm_name = ? {region_clause} LIMIT 100",
                [norm_name] + params_region
            ).fetchall()
            add_rows(rows)

        # 2) brand-key LIKE
        if brand:
            rows = c.execute(
                f"SELECT id, display_name, group_tag FROM channels WHERE norm_name LIKE ? {region_clause} LIMIT 200",
                [f"%{brand}%"] + params_region
            ).fetchall()
            add_rows(rows)

        # 3) token LIKEs
        for tok in tokens:
            rows = c.execute(
                f"SELECT id, display_name, group_tag FROM channels WHERE norm_name LIKE ? {region_clause} LIMIT 200",
                [f"%{tok}%"] + params_region
            ).fetchall()
            add_rows(rows)

        # Fallback if we still have nothing and region provided: pull small regional sample
        if not out and region:
            rows = c.execute(
                "SELECT id, display_name, group_tag FROM channels WHERE group_tag = ? LIMIT 200",
                (region,)
            ).fetchall()
            add_rows(rows)

        # Cap result size to keep scoring cheap
        return out[:400]

    def _has_any_schedule_from_now(self, ch_id: str) -> bool:
        now = self._utcnow().strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        row = c.execute(
            "SELECT 1 FROM programmes WHERE channel_id = ? AND end > ? LIMIT 1",
            (ch_id, now)
        ).fetchone()
        if not row:
            # Try next few hours window
            fut = (self._utcnow() + datetime.timedelta(hours=3)).strftime("%Y%m%d%H%M%S")
            row = c.execute(
                "SELECT 1 FROM programmes WHERE channel_id = ? AND start <= ? AND end > ? LIMIT 1",
                (ch_id, fut, now)
            ).fetchone()
        return bool(row)

    def _collect_candidates_by_id_and_name(self, c, tvg_id: str, tvg_name: str, name: str):
        candidates = {}

        if tvg_id:
            row = c.execute("SELECT id, group_tag, display_name FROM channels WHERE id = ?", (tvg_id,)).fetchone()
            if row:
                candidates[row[0]] = {'id': row[0], 'group_tag': row[1], 'score': 100, 'display_name': row[2], 'why': 'exact-id', 'ts_offset': 0}

        if tvg_name:
            norm_tvg_name = canonicalize_name(strip_noise_words(tvg_name))
            rows = c.execute("SELECT id, group_tag, display_name FROM channels WHERE norm_name = ?", (norm_tvg_name,)).fetchall()
            for r in rows:
                candidates[r[0]] = {'id': r[0], 'group_tag': r[1], 'score': 96, 'display_name': r[2], 'why': 'exact-tvg-name', 'ts_offset': 0}

        return candidates

    def get_matching_channel_ids(self, channel: Dict[str, str]) -> Tuple[List[dict], str]:
        tvg_id = (channel.get("tvg-id") or "").strip()
        tvg_name = (channel.get("tvg-name") or "").strip()
        name = (channel.get("name") or "").strip()

        playlist_region = _derive_playlist_region(channel)
        playlist_zone = _detect_zone(" ".join([channel.get("group",""), tvg_name, name]))
        playlist_brand_key = _brand_key(name)
        playlist_ts = _detect_timeshift(" ".join([tvg_name, name]))
        brand_text = canonicalize_name(strip_noise_words(name)).lower()
        playlist_brand_family = _reverse_brand_lookup(brand_text)
        pl_calls = extract_callsigns(" ".join([tvg_name, name, channel.get("group",""), tvg_id]))
        pl_tokens = tokenize_channel_name(name)

        # HBO variant extraction (playlist side)
        pl_hbo_variant_raw = _extract_hbo_variant(" ".join([tvg_name, name, channel.get("group",""), tvg_id])) if playlist_brand_family == "hbo" else ""
        pl_hbo_variant = _normalize_hbo_variant(playlist_region, pl_hbo_variant_raw)

        c = self.conn.cursor()
        candidates = self._collect_candidates_by_id_and_name(c, tvg_id, tvg_name, name)

        # Drop exact-id/name candidates from the wrong region; keep only same or unknown region
        if playlist_region:
            candidates = {k: v for k, v in candidates.items() if (v.get("group_tag") in ("", playlist_region))}
        # FAST candidate set (no full channels scan)
        rows_all = self._candidate_rows(c, name, tvg_name, playlist_region)
        pl_markets, pl_provinces, _ = _market_tokens_for(playlist_region or "", playlist_brand_family, " ".join([tvg_name, name]))

        for ch_id, disp, grp in rows_all:
            epg_text_norm = canonicalize_name(strip_noise_words(disp)).lower()
            epg_brand_family = _reverse_brand_lookup(epg_text_norm)
            epg_calls = extract_callsigns(" ".join([disp, ch_id]))
            epg_tokens = tokenize_channel_name(disp)
            token_overlap = len(pl_tokens & epg_tokens)

            families_align = (playlist_brand_family and epg_brand_family and playlist_brand_family == epg_brand_family)
            cs_delta, cs_reason = callsign_overlap_score(pl_calls, epg_calls)

            if not (families_align or cs_delta >= 60 or token_overlap >= 1):
                strong_us_local = False
                if playlist_region == "us":
                    epg_markets_tmp, epg_provs_tmp, _ = _market_tokens_for("us", epg_brand_family, disp)
                    if (pl_markets & epg_markets_tmp) or (pl_provinces & epg_provs_tmp):
                        strong_us_local = True
                if not strong_us_local:
                    continue

            score = 0
            why = []

            if cs_delta:
                score += cs_delta
                why.append(cs_reason)

            if families_align:
                score += 40
                why.append('+brand-family')
                epg_brand_key = _brand_key(disp)
                if playlist_brand_key and epg_brand_key and playlist_brand_key == epg_brand_key:
                    score += 10
                    why.append('+brand-key')

            if playlist_region:
                if grp == playlist_region:
                    score += 18
                    why.append('+same-region')
                elif grp == '':
                    score += 6
                    why.append('+unknown-region')
                else:
                    score -= 40
                    why.append('-other-region')
                if playlist_brand_family == "hbo" and families_align and grp and playlist_region and grp != playlist_region:
                    score -= 20
                    why.append('-hbo-wrong-region')

            epg_zone = _detect_zone(disp)
            if playlist_zone and epg_zone:
                if playlist_zone == epg_zone:
                    score += 8
                    why.append('+zone')
                else:
                    score -= 15
                    why.append('-zone')

            epg_ts = _detect_timeshift(" ".join([disp, ch_id]))
            if playlist_ts and epg_ts:
                ts_delta = abs(playlist_ts - epg_ts)
                if ts_delta == 0:
                    score += 5
                    why.append('+timeshift-match')
                elif ts_delta <= 2:
                    score += 3
                    why.append('+timeshift-close')
                else:
                    score -= 5 * ts_delta
                    why.append('-timeshift-far')

            # ---- HBO variant-aware boosting ----
            if playlist_brand_family == "hbo" and epg_brand_family == "hbo":
                epg_hbo_variant_raw = _extract_hbo_variant(" ".join([disp, ch_id]))
                epg_hbo_variant = _normalize_hbo_variant(grp, epg_hbo_variant_raw)

                if pl_hbo_variant or epg_hbo_variant:
                    if _normalize_hbo_variant(playlist_region, pl_hbo_variant or "base") == _normalize_hbo_variant(grp, epg_hbo_variant or "base"):
                        score += 40
                        why.append(f'+hbo-variant({pl_hbo_variant or "base"})')
                    else:
                        if playlist_region == "us":
                            if (pl_hbo_variant in {"", "base", "1"} and epg_hbo_variant in {"base", "1"}) or (epg_hbo_variant in {"", "base"} and pl_hbo_variant in {"base", "1"}):
                                score += 18
                                why.append('+hbo-us-base/1')
                            else:
                                score -= 10
                                why.append('-hbo-variant-mismatch')
                        else:
                            score -= 12
                            why.append('-hbo-variant-mismatch-ca')

            # token_overlap already computed above; reuse for scoring
            score += min(20, token_overlap * 4)
            if token_overlap:
                why.append(f'+tokens({token_overlap})')

            if score > 0:
                candidates[ch_id] = {
                    'id': ch_id,
                    'group_tag': grp,
                    'score': score,
                    'display_name': disp,
                    'why': " ".join(why),
                    'ts_offset': epg_ts if families_align else 0
                }

        out = list(candidates.values())
        if MATCH_DEBUG and DEBUG:
            _logger.debug("MATCH TRACE for '%s' (tvg-id=%s tvg-name=%s): kept=%d", _safe(name, 120), tvg_id, _safe(tvg_name, 120), len(out))
        return out, playlist_region

    def _utcnow(self):
        try:
            return datetime.datetime.now(datetime.UTC)
        except AttributeError:
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def get_now_next(self, channel: Dict[str, str]) -> Optional[tuple]:
        matches, _playlist_region = self.get_matching_channel_ids(channel)
        if not matches:
            if DEBUG:
                _logger.debug("NOW/NEXT: no candidates for '%s' (tvg-id=%s tvg-name=%s)",
                              _safe(channel.get('name')), _safe(channel.get('tvg-id')), _safe(channel.get('tvg-name')))
            return None

        # Sort by score
        matches = sorted(matches, key=lambda m: -m.get('score', 0))

        # Probe schedule availability for top-N only and reorder with those first
        try:
            avail = []
            for m in matches[:20]:
                ch_id = m['id']
                has_any = self._has_any_schedule_from_now(ch_id)
                avail.append((m, has_any))
                if DEBUG:
                    _logger.debug("MATCH CAND ch_id=%s score=%s grp=%s why=%s has_schedule_from_now=%s",
                                  ch_id, m.get('score'), m.get('group_tag'), _safe(m.get('why'), 300), has_any)
            matches = [m for m, ok in avail if ok] + [m for m, ok in avail if not ok] + matches[20:]
        except Exception:
            pass

        c = self.conn.cursor()
        now = self._utcnow()
        now_str = now.strftime("%Y%m%d%H%M%S")
        now_int = int(now_str)

        current_shows = []  # list of (start_int, payload)
        next_shows = []     # list of (start_int, payload)
        score_map = {m["id"]: m.get("score", 0) for m in matches}

        # strict interval; no pre-start grace
        for m in matches[:30]:
            ch_id = m['id']
            ts_offset = m.get('ts_offset') or 0
            if ts_offset:
                try:
                    now_adj_dt = now + datetime.timedelta(hours=ts_offset)
                except Exception:
                    now_adj_dt = now
            else:
                now_adj_dt = now
            now_adj = now_adj_dt.strftime("%Y%m%d%H%M%S")
            now_adj_int = int(now_adj)

            rows = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND end > ? ORDER BY start ASC LIMIT 6",
                (ch_id, now_adj)
            ).fetchall()
            for title, start, end in rows:
                st_i = int(start); en_i = int(end)
                # Current if st <= now <= en, OR if within small grace after start
                if st_i <= now_adj_int < en_i:
                    current_shows.append((st_i, {
                        'channel_id': ch_id,
                        'title': title,
                        'start': datetime.datetime.strptime(start, "%Y%m%d%H%M%S"),
                        'end': datetime.datetime.strptime(end, "%Y%m%d%H%M%S")
                    }))
                elif st_i > now_adj_int:
                    next_shows.append((st_i, {
                        'channel_id': ch_id,
                        'title': title,
                        'start': datetime.datetime.strptime(start, "%Y%m%d%H%M%S"),
                        'end': datetime.datetime.strptime(end, "%Y%m%d%H%M%S")
                    }))

        now_show = None if not current_shows else sorted(current_shows, key=lambda x: (-score_map.get(x[1]["channel_id"], 0), x[0]))[0][1]

        if next_shows:
            score_map = {m['id']: m.get('score', 0) for m in matches}
            next_shows.sort(key=lambda x: (-score_map.get(x[1]['channel_id'], 0), x[0]))
            next_show = next_shows[0][1]
        else:
            next_show = None

        if DEBUG:
            _logger.debug("NOW/NEXT result for '%s' -> now=%s (ch=%s) next=%s (ch=%s)",
                          _safe(channel.get('name')),
                          _safe(now_show['title'] if now_show else None),
                          now_show['channel_id'] if now_show else None,
                          _safe(next_show['title'] if next_show else None),
                          next_show['channel_id'] if next_show else None)

        return now_show, next_show
    
    def get_channels_with_show(self, query: str) -> List[Dict[str, str]]:
        q = "%" + canonicalize_name(strip_noise_words(query)) + "%"
        c = self.conn.cursor()
        now = self._utcnow().strftime("%Y%m%d%H%M%S")
        rows = c.execute("""
            SELECT p.channel_id, p.title, p.start, p.end, c.display_name
            FROM programmes p
            JOIN channels c ON c.id = p.channel_id
            WHERE (LOWER(c.norm_name) LIKE LOWER(?) OR LOWER(p.title) LIKE LOWER(?))
              AND p.end >= ?
            ORDER BY p.start ASC
            LIMIT 200
        """, (q, q, now)).fetchall()
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
        on_now = [r for r in result if int(r["start"]) <= now_int < int(r["end"])]
        future = [r for r in result if int(r["start"]) > now_int]
        final = []
        added = set()
        for r in on_now + future:
            key = (r["channel_id"], r["show_title"])
            if key not in added:
                added.add(key)
                final.append(r)
        return final

    def get_recent_programmes(self, channel: Dict[str, str], hours: int = 48, limit: int = 60) -> List[Dict[str, str]]:
        matches, _ = self.get_matching_channel_ids(channel)
        if not matches:
            return []
        now = self._utcnow()
        now_str = now.strftime("%Y%m%d%H%M%S")
        cutoff = (now - datetime.timedelta(hours=hours)).strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        results: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str, str]] = set()
        per_match = max(1, limit // max(1, len(matches)))
        ordered = sorted(matches, key=lambda m: -m.get('score', 0))[:5]
        for m in ordered:
            ch_id = m.get('id')
            if not ch_id:
                continue
            rows = c.execute(
                """
                SELECT title, start, end
                FROM programmes
                WHERE channel_id = ? AND end <= ? AND end >= ?
                ORDER BY start DESC
                LIMIT ?
                """,
                (ch_id, now_str, cutoff, per_match)
            ).fetchall()
            for title, start, end in rows:
                key = (ch_id, start, end)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "channel_id": ch_id,
                    "channel_name": m.get('display_name') or channel.get("name", ""),
                    "title": title,
                    "start": start,
                    "end": end
                })
        results.sort(key=lambda r: r["start"], reverse=True)
        return results[:limit]

    # =========================
    # Streaming importer with detailed debug
    # =========================
    def import_epg_xml(self, xml_sources: List[str], progress_callback=None):
        if DEBUG and tracemalloc.is_tracing(): tracemalloc.stop()
        tracemalloc.start()

        for p in PRAGMA_IMPORT:
            try: self.conn.execute(p)
            except Exception: pass

        total = len(xml_sources)
        BATCH = 10000
        grand_prog, grand_chan = 0, 0

        def _open_stream(src):
            _logger.debug("Opening stream: %s", src)
            if src.startswith(("http://", "https://")):
                req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=300)
                _logger.debug("HTTP GET %s | status=%s mem=%sMB", src, getattr(resp, "status", "?"), _mem_mb())
                is_gz = resp.info().get('Content-Encoding') == 'gzip' or src.lower().endswith('.gz')
                return gzip.GzipFile(fileobj=resp) if is_gz else resp
            else: # Local file
                is_gz = src.lower().endswith('.gz')
                return gzip.open(src, 'rb') if is_gz else open(src, 'rb')

        for idx, src in enumerate(xml_sources):
            t0 = time.time()
            chan_count, prog_count, inserted_since_commit, sample_ok = 0, 0, 0, 0
            stream = None
            try:
                stream = _open_stream(src)
                parser = ET.XMLPullParser(['start', 'end'])
                elem_stack: List[ET.Element] = []
                _logger.debug("EPG START src=%s (mem=%sMB)", src, _mem_mb())
                self.conn.execute("BEGIN IMMEDIATE")

                # *** DEFINITIVE MEMORY LEAK FIX ***
                # Read stream in chunks and feed to the pull parser
                while True:
                    chunk = stream.read(65536) # 64KB chunk
                    if not chunk:
                        break
                    parser.feed(chunk)
                    for event, elem in parser.read_events():
                        if event == 'start':
                            elem_stack.append(elem)
                            continue
                        # event == 'end'
                        try:
                            elem_stack.pop()
                        except IndexError:
                            elem_stack = []
                        parent = elem_stack[-1] if elem_stack else None
                        tag = elem.tag.rsplit('}', 1)[-1]
                        if tag == 'channel':
                            ch_id = elem.get("id", "")
                            dn_elem = elem.find("./display-name")
                            disp = dn_elem.text.strip() if dn_elem is not None and dn_elem.text else ""
                            if ch_id or disp:
                                self.insert_channel(ch_id, disp)
                                chan_count += 1
                        elif tag == 'programme':
                            ch_id = elem.get("channel", "")
                            title_elem = elem.find("./title")
                            title_txt = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                            st_raw = elem.get("start", "")
                            en_raw = elem.get("stop") or elem.get("end", "")

                            st_utc = _parse_xmltv_to_utc_str(st_raw)
                            en_utc = _parse_xmltv_to_utc_str(en_raw) if en_raw else None
                            if not en_utc and st_utc:
                                en_utc = _calc_end_from_length_or_duration(st_utc, elem)

                            if st_utc and en_utc and ch_id:
                                if DEBUG and sample_ok < 8:
                                    _logger.debug("EPG SAMPLE OK src=%s ...", src); sample_ok += 1
                                self.insert_programme(ch_id, title_txt, st_utc, en_utc)
                                prog_count += 1
                                inserted_since_commit += 1
                                if inserted_since_commit >= BATCH:
                                    self.commit()
                                    _logger.debug("EPG COMMIT src=%s progs+%d total=%d mem=%sMB", src, BATCH, prog_count, _mem_mb())
                                    inserted_since_commit = 0
                        # Clear processed nodes and detach them from their parent so
                        # completed <programme>/<channel> elements don't accumulate.
                        if tag in {'channel', 'programme'}:
                            try:
                                elem.clear()
                                if parent is not None:
                                    parent.remove(elem)
                            except Exception:
                                pass
                
                parser.close() # Finalize
                self.commit()
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                _logger.debug("EPG DONE src=%s channels=%d progs=%d elapsed=%.1fs mem=%sMB",
                              src, chan_count, prog_count, time.time() - t0, _mem_mb())

            except Exception as e:
                _logger.exception("EPG ERROR src=%s : %s", src, e)
                try: wx.LogError(f"Failed to import EPG source {src}: {e}")
                except Exception: pass
            finally:
                if stream:
                    try: stream.close()
                    except Exception: pass

            grand_chan += chan_count
            grand_prog += prog_count
            if progress_callback:
                try: progress_callback(idx + 1, total)
                except Exception: pass
        
        self.prune_old_programmes(days=14)
        self.commit()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        try:
            c = self.conn.cursor()
            row_c = c.execute("SELECT COUNT(*) FROM channels").fetchone()
            row_p = c.execute("SELECT COUNT(*) FROM programmes").fetchone()
            _logger.info("EPG SUMMARY total_added ch=%d pg=%d | db_final ch=%s pg=%s | mem=%sMB peak_trace=%sKB",
                         grand_chan, grand_prog, row_c[0] if row_c else '?', row_p[0] if row_p else '?', _mem_mb(), int(peak/1024))
        except Exception as e: _logger.debug("EPG SUMMARY failed to query DB counts: %s", e)


# =========================
# EPG Import/Manager UI
# =========================

class EPGImportDialog(wx.Dialog):
    def __init__(self, parent, total_sources):
        super().__init__(parent, title="Importing EPG", size=(400, 150))
        self.total_sources = total_sources
        self._build_ui()
        self.CenterOnParent()
        self.Layout()

    def _build_ui(self):
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.label = wx.StaticText(panel, label="Importing EPG data…")
        self.gauge = wx.Gauge(panel, range=max(1, self.total_sources))
        vbox.Add(self.label, 0, wx.ALL, 8)
        vbox.Add(self.gauge, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(vbox)

    def set_progress(self, value, total):
        try:
            self.gauge.SetRange(max(1, total))
            self.gauge.SetValue(min(value, total))
        except Exception:
            pass

class EPGManagerDialog(wx.Dialog):
    def __init__(self, parent, epg_sources):
        super().__init__(parent, title="EPG Manager", size=(600, 300))
        self.epg_sources = epg_sources.copy()
        self._build_ui()
        self.CenterOnParent()
        self.Layout()

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
        with wx.FileDialog(self, "Choose EPG XML file", wildcard="XML files (*.xml)|*.xml|GZip XML (*.gz)|*.gz|All files (*.*)|*.*") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                self.epg_sources.append(path)
                self.lb.Append(path)

    def OnAddURL(self, _):
        with wx.TextEntryDialog(self, "Enter EPG XML URL") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                url = dlg.GetValue().strip()
                if url:
                    self.epg_sources.append(url)
                    self.lb.Append(url)

    def OnRemove(self, _):
        i = self.lb.GetSelection()
        if i != wx.NOT_FOUND:
            self.epg_sources.pop(i)
            self.lb.Delete(i)

    def GetResult(self):
        return self.epg_sources

class PlaylistManagerDialog(wx.Dialog):
    def __init__(self, parent, playlist_sources):
        super().__init__(parent, title="Playlist Manager", size=(600, 300))
        self.playlist_sources = playlist_sources.copy()
        self._build_ui()
        self.CenterOnParent()
        self.Layout()

    def _build_ui(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.add_file_btn = wx.Button(panel, label="Add File")
        self.add_url_btn = wx.Button(panel, label="Add URL")
        self.add_xtream_btn = wx.Button(panel, label="Add Xtream Codes")
        self.add_stalker_btn = wx.Button(panel, label="Add Stalker Portal")
        self.remove_btn = wx.Button(panel, label="Remove Selected")
        for btn in (self.add_file_btn, self.add_url_btn, self.add_xtream_btn, self.add_stalker_btn, self.remove_btn):
            btn_sizer.Add(btn, 0, wx.ALL, 2)
        main_sizer.Add(btn_sizer, 0, wx.EXPAND)
        self.lb = wx.ListBox(panel, style=wx.LB_SINGLE)
        for src in self.playlist_sources:
            self.lb.Append(self._format_source_label(src))
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
        self.add_xtream_btn.Bind(wx.EVT_BUTTON, self.OnAddXtream)
        self.add_stalker_btn.Bind(wx.EVT_BUTTON, self.OnAddStalker)
        self.remove_btn.Bind(wx.EVT_BUTTON, self.OnRemove)

    def OnAddFile(self, _):
        with wx.FileDialog(self, "Choose M3U file", wildcard="M3U files (*.m3u;*.m3u8)|*.m3u;*.m3u8|All files (*.*)|*.*") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                self.playlist_sources.append(path)
                self.lb.Append(self._format_source_label(path))

    def OnAddURL(self, _):
        with wx.TextEntryDialog(self, "Enter M3U URL") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                url = dlg.GetValue().strip()
                if url:
                    self.playlist_sources.append(url)
                    self.lb.Append(self._format_source_label(url))

    def OnAddXtream(self, _):
        dlg = XtreamCodesDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.get_data()
            if data:
                data.setdefault("id", generate_provider_id())
                self.playlist_sources.append(data)
                self.lb.Append(self._format_source_label(data))
        dlg.Destroy()

    def OnAddStalker(self, _):
        dlg = StalkerPortalDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.get_data()
            if data:
                data.setdefault("id", generate_provider_id())
                self.playlist_sources.append(data)
                self.lb.Append(self._format_source_label(data))
        dlg.Destroy()

    def OnRemove(self, _):
        i = self.lb.GetSelection()
        if i != wx.NOT_FOUND:
            self.playlist_sources.pop(i)
            self.lb.Delete(i)

    def _format_source_label(self, src):
        if isinstance(src, dict):
            stype = (src.get("type") or "").lower()
            name = src.get("name") or src.get("username") or src.get("base_url") or "Provider"
            if stype == "xtream":
                return f"Xtream Codes – {name}"
            if stype == "stalker":
                return f"Stalker Portal – {name}"
            return f"Provider – {name}"
        return src

    def GetResult(self):
        return self.playlist_sources


class XtreamCodesDialog(wx.Dialog):
    STREAM_TYPES = ["m3u_plus", "m3u", "enigma2"]
    OUTPUT_TYPES = ["ts", "m3u8", "rtmp"]

    def __init__(self, parent):
        super().__init__(parent, title="Add Xtream Codes Account")
        self._build_ui()
        self.CenterOnParent()

    def _build_ui(self):
        outer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        grid = wx.FlexGridSizer(0, 2, 6, 10)
        grid.AddGrowableCol(1, 1)

        def add_row(label, ctrl, hint=None):
            text = wx.StaticText(panel, label=f"{label}:")
            text.SetName(f"{label} label")
            if hasattr(text, "SetAccessibleName"):
                text.SetAccessibleName(f"{label} label")
            ctrl.SetName(label)
            if hasattr(ctrl, "SetAccessibleName"):
                ctrl.SetAccessibleName(label)
            desc = hint or f"{label} field"
            if hasattr(ctrl, "SetAccessibleDescription"):
                ctrl.SetAccessibleDescription(desc)
            if hint:
                ctrl.SetToolTip(hint)
            if hasattr(ctrl, "SetHelpText") and not isinstance(ctrl, wx.ComboBox):
                ctrl.SetHelpText(desc)
            # Only editable text controls support SetHint without assertions on Windows.
            if isinstance(ctrl, wx.TextCtrl) and hint and hasattr(ctrl, "SetHint"):
                try:
                    ctrl.SetHint(hint)
                except Exception:
                    pass
            if hasattr(ctrl, "SetAccessible"):
                acc = _FieldAccessible(label, desc)
                ctrl.SetAccessible(acc)
                ctrl._field_accessible = acc
            grid.Add(text, 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)

        self.name_ctrl = wx.TextCtrl(panel)
        self.url_ctrl = wx.TextCtrl(panel)
        self.user_ctrl = wx.TextCtrl(panel)
        self.pass_ctrl = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        self.stream_ctrl = wx.ComboBox(panel, choices=self.STREAM_TYPES, value="m3u_plus", style=wx.CB_READONLY)
        self.output_ctrl = wx.ComboBox(panel, choices=self.OUTPUT_TYPES, value="ts", style=wx.CB_READONLY)
        self.auto_epg_ctrl = wx.CheckBox(panel, label="Automatically add XMLTV URL")
        self.auto_epg_ctrl.SetName("Automatically add XMLTV URL")
        if hasattr(self.auto_epg_ctrl, "SetAccessibleName"):
            self.auto_epg_ctrl.SetAccessibleName("Automatically add XMLTV URL")
        self.auto_epg_ctrl.SetToolTip("Include the provider's XMLTV guide automatically")
        self.auto_epg_ctrl.SetValue(True)

        add_row("Display name", self.name_ctrl, "Optional nickname for this Xtream Codes account")
        add_row("Portal base URL", self.url_ctrl, "Base URL of the Xtream Codes server")
        add_row("Username", self.user_ctrl, "Xtream Codes username")
        add_row("Password", self.pass_ctrl, "Xtream Codes password")
        add_row("Playlist type", self.stream_ctrl, "Select the playlist format to download")
        add_row("Stream output", self.output_ctrl, "Preferred stream container format")
        grid.Add(wx.StaticText(panel, label=""))
        grid.Add(self.auto_epg_ctrl)

        panel.SetSizer(grid)
        outer.Add(panel, 1, wx.ALL | wx.EXPAND, 12)

        btn_sizer = wx.StdDialogButtonSizer()
        self.ok_btn = wx.Button(self, wx.ID_OK, "Add")
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(self.ok_btn)
        btn_sizer.AddButton(self.cancel_btn)
        btn_sizer.Realize()
        outer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 12)

        self.ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.ok_btn.SetDefault()
        self.cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)

        self.SetSizerAndFit(outer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_ok(self, event):
        if not self.user_ctrl.GetValue().strip() or not self.pass_ctrl.GetValue().strip() or not self.url_ctrl.GetValue().strip():
            wx.MessageBox("Username, password, and URL are required.", "Validation", wx.OK | wx.ICON_WARNING)
            return
        self.EndModal(wx.ID_OK)

    def _on_cancel(self, event):
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def _on_close(self, event):
        self._on_cancel(event)

    def get_data(self):
        url = self.url_ctrl.GetValue().strip()
        username = self.user_ctrl.GetValue().strip()
        password = self.pass_ctrl.GetValue().strip()
        if not url or not username or not password:
            return None
        return {
            "type": "xtream",
            "name": self.name_ctrl.GetValue().strip(),
            "base_url": url,
            "username": username,
            "password": password,
            "stream_type": self.stream_ctrl.GetValue() or "m3u_plus",
            "output": self.output_ctrl.GetValue() or "ts",
            "auto_epg": self.auto_epg_ctrl.GetValue()
        }


class StalkerPortalDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Add Stalker Portal Account")
        self._build_ui()
        self.CenterOnParent()

    def _build_ui(self):
        outer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        grid = wx.FlexGridSizer(0, 2, 6, 10)
        grid.AddGrowableCol(1, 1)

        def add_row(label, ctrl, hint=None):
            text = wx.StaticText(panel, label=f"{label}:")
            text.SetName(f"{label} label")
            if hasattr(text, "SetAccessibleName"):
                text.SetAccessibleName(f"{label} label")
            ctrl.SetName(label)
            if hasattr(ctrl, "SetAccessibleName"):
                ctrl.SetAccessibleName(label)
            desc = hint or f"{label} field"
            if hasattr(ctrl, "SetAccessibleDescription"):
                ctrl.SetAccessibleDescription(desc)
            if hint:
                ctrl.SetToolTip(hint)
            if hasattr(ctrl, "SetHelpText") and not isinstance(ctrl, wx.ComboBox):
                ctrl.SetHelpText(desc)
            if isinstance(ctrl, wx.TextCtrl) and hint and hasattr(ctrl, "SetHint"):
                try:
                    ctrl.SetHint(hint)
                except Exception:
                    pass
            if hasattr(ctrl, "SetAccessible"):
                acc = _FieldAccessible(label, desc)
                ctrl.SetAccessible(acc)
                ctrl._field_accessible = acc
            grid.Add(text, 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)

        self.name_ctrl = wx.TextCtrl(panel)
        self.url_ctrl = wx.TextCtrl(panel)
        self.user_ctrl = wx.TextCtrl(panel)
        self.pass_ctrl = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        self.mac_ctrl = wx.TextCtrl(panel)
        self.auto_epg_ctrl = wx.CheckBox(panel, label="Attempt to add provider XMLTV")
        self.auto_epg_ctrl.SetName("Attempt to add provider XMLTV")
        if hasattr(self.auto_epg_ctrl, "SetAccessibleName"):
            self.auto_epg_ctrl.SetAccessibleName("Attempt to add provider XMLTV")
        self.auto_epg_ctrl.SetToolTip("Try to add the portal's XMLTV guide automatically")
        self.auto_epg_ctrl.SetValue(True)

        self.mac_ctrl.SetValue(self._default_mac())

        self.mac_btn = wx.Button(panel, label="Randomise MAC")
        self.mac_btn.Bind(wx.EVT_BUTTON, self._on_random_mac)

        add_row("Display name", self.name_ctrl, "Optional nickname for this portal account")
        add_row("Portal base URL", self.url_ctrl, "Base URL of the Stalker/Ministra portal")
        add_row("Username", self.user_ctrl, "Portal account username")
        add_row("Password", self.pass_ctrl, "Portal account password")
        add_row("MAC address", self.mac_ctrl, "MAC address presented to the portal")
        grid.Add(wx.StaticText(panel, label=""))
        grid.Add(self.mac_btn)
        grid.Add(wx.StaticText(panel, label=""))
        grid.Add(self.auto_epg_ctrl)

        panel.SetSizer(grid)
        outer.Add(panel, 1, wx.ALL | wx.EXPAND, 12)

        btn_sizer = wx.StdDialogButtonSizer()
        self.ok_btn = wx.Button(self, wx.ID_OK, "Add")
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(self.ok_btn)
        btn_sizer.AddButton(self.cancel_btn)
        btn_sizer.Realize()
        outer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 12)

        self.ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.ok_btn.SetDefault()
        self.cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)

        self.SetSizerAndFit(outer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _sanitize_mac(self, value: str) -> str:
        value = value.replace('-', ':').replace('.', '')
        value = value.upper()
        if ':' not in value and len(value) == 12:
            value = ':'.join(value[i:i+2] for i in range(0, 12, 2))
        return value

    def _default_mac(self) -> str:
        prefix = [0x00, 0x1A, 0x79]
        suffix = [random.randint(0x00, 0xFF) for _ in range(3)]
        return ':'.join(f"{b:02X}" for b in prefix + suffix)

    def _on_random_mac(self, _):
        self.mac_ctrl.SetValue(self._default_mac())

    def _on_ok(self, event):
        if not self.user_ctrl.GetValue().strip() or not self.pass_ctrl.GetValue().strip() or not self.url_ctrl.GetValue().strip():
            wx.MessageBox("Portal URL, username, and password are required.", "Validation", wx.OK | wx.ICON_WARNING)
            return
        mac = self._sanitize_mac(self.mac_ctrl.GetValue().strip())
        if len(mac.split(':')) != 6:
            wx.MessageBox("MAC address must contain six octets (e.g., 00:1A:79:12:34:56).", "Validation", wx.OK | wx.ICON_WARNING)
            return
        self.mac_ctrl.SetValue(mac)
        self.EndModal(wx.ID_OK)

    def _on_cancel(self, event):
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def _on_close(self, event):
        self._on_cancel(event)

    def get_data(self):
        url = self.url_ctrl.GetValue().strip()
        username = self.user_ctrl.GetValue().strip()
        password = self.pass_ctrl.GetValue().strip()
        if not url or not username or not password:
            return None
        return {
            "type": "stalker",
            "name": self.name_ctrl.GetValue().strip(),
            "base_url": url,
            "username": username,
            "password": password,
            "mac": self.mac_ctrl.GetValue().strip(),
            "auto_epg": self.auto_epg_ctrl.GetValue()
        }

# =========================
# Helpers for region + playlist
# =========================

def _derive_playlist_region(channel: Dict[str, str]) -> str:
    # Try group/title and tvg fields first
    g = channel.get("group") or ""
    for tok in re.findall(r'[a-z]{2,3}', g.lower()):
        if tok in group_synonyms():
            return tok

    def _normalize_for_prefix(text: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (text or '').lower())

    def _strip_quality_prefix(remainder: str) -> str:
        # successively drop common quality/format tags so "ukfhd" -> ""
        rem = remainder
        changed = True
        while rem and changed:
            changed = False
            for tag in STRIP_TAGS:
                clean_tag = _normalize_for_prefix(tag)
                if clean_tag and rem.startswith(clean_tag):
                    rem = rem[len(clean_tag):]
                    changed = True
        return rem

    # Handle compact group names like "UKSD" where region + quality tag are glued
    compact_group = _normalize_for_prefix(g)
    if compact_group:
        for code, variants in group_synonyms().items():
            for variant in variants:
                prefix = _normalize_for_prefix(variant)
                if prefix and compact_group.startswith(prefix):
                    remainder = _strip_quality_prefix(compact_group[len(prefix):])
                    if not remainder:
                        return code

    # tvg-id suffixes sometimes carry region
    tid = (channel.get("tvg-id") or "").lower()
    for tok in re.findall(r'[.\-_:|/]([a-z]{2,3})', tid):
        if tok in group_synonyms():
            return tok

    # fallback: guess from display name / tvg-name, handling compact tags
    name_field = channel.get("name", "")
    for text in (name_field, channel.get("tvg-name", "")):
        compact = _normalize_for_prefix(text)
        if compact:
            for code, variants in group_synonyms().items():
                for variant in variants:
                    prefix = _normalize_for_prefix(variant)
                    if prefix and compact.startswith(prefix):
                        remainder = _strip_quality_prefix(compact[len(prefix):])
                        if not remainder:
                            return code

    # fallback to broader text search
    if name_field:
        return extract_group(name_field)
    tvg_name = channel.get("tvg-name", "")
    return extract_group(tvg_name) if tvg_name else ''

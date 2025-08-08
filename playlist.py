# playlist.py
import os
import re
import io
import wx
import gzip
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import datetime
from typing import Dict, List, Optional, Tuple, Set

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

MATCH_DEBUG = bool(os.environ.get("EPG_MATCH_DEBUG"))

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

# Recognized HBO variants
_HBO_VARIANTS = ("base", "1", "2", "family", "latino", "signature", "comedy", "hits", "zone", "plus")

def _extract_hbo_variant(raw_text: str) -> str:
    """
    Return which HBO subbrand is referenced in text.
    'base' means plain HBO (or HBO East/West without a number).
    """
    if not raw_text:
        return ""
    s = raw_text.lower()
    # Look for explicit sub-brands first
    if re.search(r'\bfamily\b', s): return "family"
    if re.search(r'\blatino\b', s): return "latino"
    if re.search(r'\bsignature\b', s): return "signature"
    if re.search(r'\bcomedy\b', s): return "comedy"
    if re.search(r'\bhits\b', s): return "hits"
    if re.search(r'\bzone\b', s): return "zone"
    if re.search(r'\bplus\b', s): return "plus"

    # Numeric variants
    if re.search(r'\bhbo[\s\-]*1\b', s) or re.search(r'\b1\b', s):
        return "1"
    if re.search(r'\bhbo[\s\-]*2\b', s) or re.search(r'\b2\b', s):
        return "2"

    # Default: if 'hbo' mentioned, assume base
    if re.search(r'\bhbo\b', s):
        return "base"
    return ""

def _normalize_hbo_variant(country_code: str, variant: str) -> str:
    """
    Region-aware normalization:
    - US: treat HBO1 as base (same channel). '1' => 'base'
    - CA: keep '1' distinct (Canada HBO1 should match to Canada HBO1, not base).
    """
    v = (variant or "").strip().lower()
    cc = (country_code or "").strip().lower()
    if not v:
        return ""
    if cc == "us":
        if v == "1":
            return "base"
    return v

def _is_hbo_family(text: str) -> bool:
    return bool(text and re.search(r'\bhbo\b', text.lower()))

AFFILIATE_MARKETS: Dict[str, Dict[str, Dict[str, Set[str]]]] = {
    "ca": {
        "cbc": {
            "vancouver-bc": _mk("vancouver", "bc", "british columbia"),
            "calgary-ab": _mk("calgary", "ab"),
            "edmonton-ab": _mk("edmonton", "ab"),
            "saskatoon-sk": _mk("saskatoon", "sk"),
            "regina-sk": _mk("regina", "sk"),
            "winnipeg-mb": _mk("winnipeg", "mb"),
            "ottawa-on": _mk("ottawa", "on"),
            "toronto-on": _mk("toronto", "on"),
            "montreal-qc": _mk("montreal", "montréal", "qc"),
            "halifax-ns": _mk("halifax", "ns"),
            "stjohns-nl": _mk("st johns", "st. johns", "nl"),
        },
        "ctv": {
            "vancouver-bc": _mk("vancouver", "bc"),
            "calgary-ab": _mk("calgary", "ab"),
            "edmonton-ab": _mk("edmonton", "ab"),
            "saskatoon-sk": _mk("saskatoon", "sk"),
            "regina-sk": _mk("regina", "sk"),
            "winnipeg-mb": _mk("winnipeg", "mb"),
            "ottawa-on": _mk("ottawa", "on"),
            "toronto-on": _mk("toronto", "on"),
            "london-on": _mk("london", "on"),
            "montreal-qc": _mk("montreal", "montréal", "qc"),
            "halifax-ns": _mk("halifax", "ns"),
        },
        "ctv2": {
            "vancouver-bc": _mk("vancouver", "bc"),
            "ottawa-on": _mk("ottawa", "on"),
            "london-on": _mk("london", "on"),
            "windsor-on": _mk("windsor", "on"),
        },
        "citytv": {
            "vancouver-bc": _mk("vancouver", "bc"),
            "calgary-ab": _mk("calgary", "ab"),
            "edmonton-ab": _mk("edmonton", "ab"),
            "winnipeg-mb": _mk("winnipeg", "mb"),
            "toronto-on": _mk("toronto", "on"),
            "montreal-qc": _mk("montreal", "montréal", "qc"),
        },
        "global": {
            "vancouver-bc": _mk("vancouver", "bc", "british columbia", "global bc"),
            "calgary-ab": _mk("calgary", "ab"),
            "edmonton-ab": _mk("edmonton", "ab"),
            "saskatoon-sk": _mk("saskatoon", "sk"),
            "regina-sk": _mk("regina", "sk"),
            "winnipeg-mb": _mk("winnipeg", "mb"),
            "toronto-on": _mk("toronto", "on"),
            "montreal-qc": _mk("montreal", "montréal", "qc"),
            "halifax-ns": _mk("halifax", "ns"),
        },
        "tsn": {
            "tsn1-west": _mk("tsn1", "west", "bc", "ab", "pacific", "mountain"),
            "tsn2-central": _mk("tsn2", "central"),
            "tsn3-prairies": _mk("prairies", "mb", "sk"),
            "tsn4-ontario": _mk("ontario", "on", "toronto"),
            "tsn5-east": _mk("east", "ottawa", "montreal", "qc", "atlantic"),
        },
        "sportsnet": {
            "pacific": _mk("pacific", "bc", "vancouver"),
            "west": _mk("west", "ab", "calgary", "edmonton"),
            "prairies": _mk("prairies", "sk", "mb"),
            "ontario": _mk("ontario", "on", "toronto"),
            "east": _mk("east", "qc", "montreal", "atlantic"),
            "one": _mk("sn1", "sportsnet one", "one"),
            "360": _mk("sportsnet 360", "sn360", "360"),
        },
        "tva": {"montreal-qc": _mk("montreal", "montréal", "qc"), "quebeccity-qc": _mk("quebec city", "québec")},
        "noovo": {"montreal-qc": _mk("montreal", "montréal", "qc")},
        # Optional named brand to let region grouping exist for HBO in CA if present
        "hbo": {},
    },
    "us": {
        "abc": {}, "nbc": {}, "cbs": {}, "fox": {}, "pbs": {}, "cw": {}, "mynetwork": {}, "telemundo": {}, "univision": {},
        "hbo": {},  # Allow region tagging if EPG uses it
    },
    "uk": {
        "bbc one": {"london": _mk("london"), "wales": _mk("wales", "cymru"), "scotland": _mk("scotland", "stv"), "northern ireland": _mk("northern ireland", "ni")},
        "bbc two": {"wales": _mk("wales"), "scotland": _mk("scotland"), "northern ireland": _mk("northern ireland")},
        "itv": {"london": _mk("london"), "wales": _mk("wales"), "yorkshire": _mk("yorkshire"), "granada": _mk("granada"),
                "tyne tees": _mk("tyne tees"), "meridian": _mk("meridian"), "central": _mk("central"),
                "border": _mk("border"), "stv": _mk("stv"), "utv": _mk("utv", "ulster", "northern ireland")},
        "sky crime": {},
        "sky mix": {},
        "sky max": {},
    },
    "de": {"ard": {}, "wdr": {}, "ndr": {}, "mdr": {}, "br": {}, "hr": {}, "rbb": {}, "swr": {}},
    "au": {"abc": {}, "seven": {}, "nine": {}, "ten": {}, "sbs": {}},
    "nz": {"tvnz 1": {}, "tvnz 2": {}, "three": {}},
}

AFFILIATE_BRANDS: Set[str] = {
    "cbc", "ctv", "ctv2", "citytv", "global", "tva", "noovo", "tsn", "sportsnet",
    "abc", "nbc", "cbs", "fox", "pbs", "cw", "mynetwork", "telemundo", "univision",
    "bbc one", "bbc two", "itv", "channel 4", "channel 5", "sky crime", "sky mix", "sky max",
    "ard", "wdr", "ndr", "mdr", "br", "hr", "rbb", "swr",
    "seven", "nine", "ten", "sbs", "tvnz 1", "tvnz 2", "three",
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
        # Any HBO or subbrand maps to 'hbo' family
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

    brand_l = (brand or "").lower()
    markets_map = AFFILIATE_MARKETS.get(country, {}).get(brand_l, {})
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
            "birmingham","buffalo","charleston","dayton","el paso","fresno","greensboro","hartford","jacksonville",
            "knoxville","louisville","madison","norfolk","omaha","providence","richmond","rochester","san jose","st paul",
            "toledo","tulsa","wichita","spokane","eugene","bakersfield","grand rapids"
        }
        for city in MAJOR_US_CITIES:
            if re.search(r'\b' + re.escape(city) + r'\b', s):
                markets.add(city.replace(' ', ''))
                cities.add(city)

    if country == "uk" and (brand_l in {"bbc one","bbc two","itv"} or brand_l == ""):
        UK_REGIONS = {
            "london","wales","scotland","northern ireland","yorkshire","granada","tyne tees","meridian","central","border","stv","utv","ulster","england"
        }
        for reg in UK_REGIONS:
            if re.search(r'\b' + re.escape(reg) + r'\b', s):
                markets.add(reg.replace(' ', '-'))

    if country == "de":
        DE_LAND = {
            "bayern","berlin","brandenburg","hessen","nordrhein","westfalen","nrw","niedersachsen","schleswig","holstein",
            "hamburg","sachsen","anhalt","thüringen","thueringen","baden","württemberg","rheinland","pfalz","mecklenburg","vorpommern"
        }
        for reg in DE_LAND:
            if re.search(r'\b' + re.escape(reg) + r'\b', s):
                markets.add(reg.replace(' ', '-'))

    return markets, provinces, cities

# =========================
# XMLTV time parsing to UTC
# =========================

_XMLTV_TS_RX = re.compile(
    r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:\s*([+\-]\d{4})|Z)?'
)

def _parse_xmltv_to_utc_str(s: str) -> Optional[str]:
    if not s:
        return None
    m = _XMLTV_TS_RX.match(s.strip())
    if not m:
        return None
    y, mo, d, h, mi, sec, off = m.groups()
    try:
        y = int(y); mo = int(mo); d = int(d); h = int(h); mi = int(mi); sec = int(sec)
    except Exception:
        return None
    base = datetime.datetime(y, mo, d, h, mi, sec)
    if off:
        try:
            sign = 1 if off[0] == '+' else -1
            offset_hours = int(off[1:3])
            offset_minutes = int(off[3:5])
            base -= datetime.timedelta(hours=sign*offset_hours, minutes=sign*offset_minutes)
        except Exception:
            pass
    return base.strftime("%Y%m%d%H%M%S")

# =========================
# EPG Database
# =========================

class EPGDatabase:
    def __init__(self, db_path: str, readonly: bool = False, for_threading: bool = False):
        self.db_path = db_path
        if for_threading:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        else:
            self.conn = sqlite3.connect(self.db_path)
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start ON programmes (channel_id, start)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_programmes_title ON programmes (title)")
        self.conn.commit()

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

    def _has_any_schedule_from_now(self, ch_id: str) -> bool:
        now = self._utcnow().strftime("%Y%m%d%H%M%S")
        c = self.conn.cursor()
        row = c.execute("SELECT 1 FROM programmes WHERE channel_id = ? AND end >= ? LIMIT 1", (ch_id, now)).fetchone()
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

        norm_name_pl = canonicalize_name(strip_noise_words(name))
        rows = c.execute("SELECT id, group_tag, display_name FROM channels WHERE norm_name = ?", (norm_name_pl)).fetchall() if False else []
        return candidates

    def get_matching_channel_ids(self, channel: Dict[str, str]) -> List[dict]:
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

        # HBO variant extraction (playlist side)
        pl_hbo_variant_raw = _extract_hbo_variant(" ".join([tvg_name, name, channel.get("group",""), tvg_id])) if playlist_brand_family == "hbo" else ""
        pl_hbo_variant = _normalize_hbo_variant(playlist_region, pl_hbo_variant_raw)

        c = self.conn.cursor()
        candidates = self._collect_candidates_by_id_and_name(c, tvg_id, tvg_name, name)

        target_tokens = tokenize_channel_name(name)
        rows_all = c.execute("SELECT id, display_name, group_tag FROM channels").fetchall()

        pl_markets, pl_provinces, _ = _market_tokens_for(playlist_region or "", playlist_brand_family, " ".join([tvg_name, name]))

        for ch_id, disp, grp in rows_all:
            epg_text_norm = canonicalize_name(strip_noise_words(disp)).lower()
            epg_brand_family = _reverse_brand_lookup(epg_text_norm)
            epg_calls = extract_callsigns(" ".join([disp, ch_id]))

            families_align = (playlist_brand_family and epg_brand_family and playlist_brand_family == epg_brand_family)
            cs_delta, cs_reason = callsign_overlap_score(pl_calls, epg_calls)

            # Let strong local market mapping through even if brands don't match (for US locals),
            # otherwise require family align or strong callsign match
            if not (families_align or cs_delta >= 60 or (not playlist_brand_family and epg_calls and pl_calls and cs_delta >= 60)):
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
                # Keep region penalty for HBO too if region mismatches
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
            ts_delta = 0
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

                # Treat US 'HBO1' as base; in CA keep '1' distinct
                if pl_hbo_variant or epg_hbo_variant:
                    if _normalize_hbo_variant(playlist_region, pl_hbo_variant or "base") == _normalize_hbo_variant(grp, epg_hbo_variant or "base"):
                        score += 40
                        why.append(f'+hbo-variant({pl_hbo_variant or "base"})')
                    else:
                        # If playlist is generic/base and EPG is either base or 1 in US, still give a smaller boost
                        if playlist_region == "us":
                            if (pl_hbo_variant in {"", "base", "1"} and epg_hbo_variant in {"base", "1"}) or (epg_hbo_variant in {"", "base"} and pl_hbo_variant in {"base", "1"}):
                                score += 18
                                why.append('+hbo-us-base/1')
                            else:
                                score -= 10
                                why.append('-hbo-variant-mismatch')
                        else:
                            # In Canada, 1 must match 1 explicitly
                            score -= 12
                            why.append('-hbo-variant-mismatch-ca')

            # Token overlap
            epg_tokens = tokenize_channel_name(disp)
            token_overlap = len(target_tokens & epg_tokens)
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

        return list(candidates.values()), playlist_region

    def _utcnow(self):
        try:
            return datetime.datetime.now(datetime.UTC)
        except AttributeError:
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def get_now_next(self, channel: Dict[str, str]) -> Optional[tuple]:
        matches, playlist_region = self.get_matching_channel_ids(channel)
        if not matches:
            return None
        matches = sorted(matches, key=lambda m: -m.get('score', 0))

        c = self.conn.cursor()
        now = self._utcnow()
        now_str = now.strftime("%Y%m%d%H%M%S")

        current_shows = []
        next_shows = []

        for match in matches[:12]:
            ch_id = match['id']
            grp = match['group_tag']
            ts_offset = int(match.get('ts_offset', 0))
            group_priority = bool(playlist_region and grp and playlist_region == grp)

            query_now = now - datetime.timedelta(hours=ts_offset) if ts_offset > 0 else now
            qnow_str = query_now.strftime("%Y%m%d%H%M%S")

            row = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start <= ? AND end > ? ORDER BY start DESC LIMIT 1",
                (ch_id, qnow_str, qnow_str)).fetchone()
            if row:
                title, start, end = row
                st_dt = datetime.datetime.strptime(start, "%Y%m%d%H%M%S")
                en_dt = datetime.datetime.strptime(end, "%Y%m%d%H%M%S")
                if ts_offset > 0:
                    st_dt += datetime.timedelta(hours=ts_offset)
                    en_dt += datetime.timedelta(hours=ts_offset)
                current_shows.append({
                    "title": title,
                    "start": st_dt,
                    "end": en_dt,
                    "channel_id": ch_id,
                    "group_tag": grp,
                    "group_priority": group_priority,
                    "score": match.get("score", 0)
                })

            row2 = c.execute(
                "SELECT title, start, end FROM programmes WHERE channel_id = ? AND start > ? ORDER BY start ASC LIMIT 1",
                (ch_id, qnow_str)).fetchone()
            if row2:
                title2, start2, end2 = row2
                st2_dt = datetime.datetime.strptime(start2, "%Y%m%d%H%M%S")
                en2_dt = datetime.datetime.strptime(end2, "%Y%m%d%H%M%S")
                if ts_offset > 0:
                    st2_dt += datetime.timedelta(hours=ts_offset)
                    en2_dt += datetime.timedelta(hours=ts_offset)
                next_shows.append({
                    "title": title2,
                    "start": st2_dt,
                    "end": en2_dt,
                    "channel_id": ch_id,
                    "group_tag": grp,
                    "group_priority": group_priority,
                    "score": match.get("score", 0)
                })

        def pick_best(showlist, is_now):
            if not showlist:
                return None
            key_fn = (lambda s: (not s["group_priority"], -s["score"], s["end"])) if is_now else (lambda s: (not s["group_priority"], -s["score"], s["start"]))
            return sorted(showlist, key=key_fn)[0]

        now_show = pick_best(current_shows, True)
        next_show = pick_best(next_shows, False)
        return (now_show, next_show)

    def get_channels_with_show(self, filter_text: str, max_results: int = 100):
        now = self._utcnow().strftime("%Y%m%d%H%M%S")
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
                        if src.endswith(".gz"):
                            with gzip.GzipFile(fileobj=io.BytesIO(resp.read())) as gz:
                                content = gz.read().decode("utf-8", "ignore")
                        else:
                            content = resp.read().decode("utf-8", "ignore")
                else:
                    if src.endswith(".gz"):
                        with gzip.open(src, "rt", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    else:
                        with open(src, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                # Parse
                root = ET.fromstring(content)
                for ch in root.findall("./channel"):
                    ch_id = ch.get("id") or ""
                    disp = ""
                    dn = ch.find("display-name")
                    if dn is not None:
                        disp = (dn.text or "").strip()
                    thread_db.insert_channel(ch_id, disp)
                for prg in root.findall("./programme"):
                    ch_id = prg.get("channel") or ""
                    title = prg.find("title")
                    title_txt = (title.text or "").strip() if title is not None else ""
                    start = prg.get("start") or ""
                    end = prg.get("end") or ""
                    st_utc = _parse_xmltv_to_utc_str(start)
                    en_utc = _parse_xmltv_to_utc_str(end)
                    if st_utc and en_utc:
                        thread_db.insert_programme(ch_id, title_txt, st_utc, en_utc)
                root.clear()
            except Exception as e:
                try:
                    wx.LogError(f"Failed to import EPG source {src}: {e}")
                except Exception:
                    pass
            if progress_callback:
                try:
                    progress_callback(idx+1, total)
                except Exception:
                    pass
        thread_db.commit()
        thread_db.close()

def _derive_playlist_region(channel: Dict[str, str]) -> str:
    # Try tvg-id, name, and group fields
    for field in ("tvg-id", "name", "group"):
        val = (channel.get(field) or "").strip().lower()
        if not val:
            continue
        parts = re.split(r'[.\-_:|/()\[\]\s]+', val)
        for tok in parts:
            code = _norm_country(tok)
            if code:
                return code
    return ''

class EPGImportDialog(wx.Dialog):
    def __init__(self, parent, total_sources: int):
        super().__init__(parent, title="Importing EPG…", size=(400, 120))
        self.total = total_sources
        self.current = 0
        self._build_ui()
        self.CenterOnParent()
        self.Layout()

    def _build_ui(self):
        p = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.gauge = wx.Gauge(p, range=max(1, self.total))
        s.Add(self.gauge, 1, wx.EXPAND | wx.ALL, 10)
        p.SetSizer(s)

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

    def GetSources(self):
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
        self.remove_btn = wx.Button(panel, label="Remove Selected")
        for btn in (self.add_file_btn, self.add_url_btn, self.remove_btn):
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

    def OnAddFile(self, _):
        with wx.FileDialog(self, "Choose M3U file", wildcard="M3U files (*.m3u;*.m3u8)|*.m3u;*.m3u8|All files (*.*)|*.*") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                self.playlist_sources.append(path)
                self.lb.Append(path)

    def OnAddURL(self, _):
        with wx.TextEntryDialog(self, "Enter M3U URL") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                url = dlg.GetValue().strip()
                if url:
                    self.playlist_sources.append(url)
                    self.lb.Append(url)

    def OnRemove(self, _):
        i = self.lb.GetSelection()
        if i != wx.NOT_FOUND:
            self.playlist_sources.pop(i)
            self.lb.Delete(i)

    def GetSources(self):
        return self.playlist_sources

import os
import shutil
import json
import socket
import tempfile
import ctypes
import urllib.request
import urllib.parse
import sqlite3
import threading
from typing import Dict, List, Optional
import wx
import datetime
import re
import platform
import time
import subprocess
import hashlib
import asyncio
import concurrent.futures

import wx.adv

from options import (
    load_config, save_config, get_cache_path_for_url, get_cache_dir,
    get_db_path, canonicalize_name, extract_group, utc_to_local,
    CustomPlayerDialog, resolve_internal_player_settings
)
from playlist import (
    EPGDatabase, EPGManagerDialog, PlaylistManagerDialog
)
from providers import (
    XtreamCodesClient, XtreamCodesConfig,
    StalkerPortalClient, StalkerPortalConfig,
    ProviderError, generate_provider_id
)
from casting import CastingManager, CastDevice, CastProtocol
from http_headers import channel_http_headers
from external_player import ExternalPlayerLauncher

try:
    from internal_player import (
        InternalPlayerFrame,
        InternalPlayerUnavailableError,
        _VLC_IMPORT_ERROR,
    )
except Exception as _internal_player_import_error:  # pragma: no cover - import guard
    InternalPlayerFrame = None  # type: ignore[assignment]
    _VLC_IMPORT_ERROR = _internal_player_import_error  # type: ignore[assignment]

    class InternalPlayerUnavailableError(RuntimeError):
        """Fallback error when internal player cannot load."""


_M3U_ATTR_RE = re.compile(r'([A-Za-z0-9_\-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^",\s]+))')

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

def check_ffmpeg() -> bool:
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=10, creationflags=creation_flags)
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

def install_ffmpeg():
    system = platform.system()
    if system == 'Windows':
        # Helper to check if choco is available
        def check_choco():
            try:
                subprocess.run(['choco', '--version'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
                return True
            except (FileNotFoundError, OSError):
                return False

        # Helper to install choco
        def install_choco():
            print("Chocolatey not found. Installing Chocolatey...")
            ps_command = "Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
            try:
                subprocess.run(["powershell", "-NoProfile", "-InputFormat", "None", "-ExecutionPolicy", "Bypass", "-Command", ps_command], check=True, creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install Chocolatey: {e}")

        # Install ffmpeg using choco
        try:
            if not check_choco():
                install_choco()
                # Refresh path? The current process might not see the new path immediately.
                # However, choco usually adds to path. We might need to restart or use full path.
                # For now, let's assume it works or the user restarts.
                # Or try to run it via refreshing env vars.
                pass
            
            print("Installing ffmpeg via Chocolatey...")
            subprocess.run(['choco', 'install', 'ffmpeg', '-y'], check=True, creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install ffmpeg via Chocolatey: {e}")

    elif system == 'Linux':
        distro = os.environ.get("MYAPP_DISTRO", "unknown")
        if distro in ("ubuntu", "debian", "mint", "popos"):
            try:
                subprocess.run(['sudo', 'apt-get', 'update'], check=True)
                subprocess.run(['sudo', 'apt-get', 'install', '-y', 'ffmpeg'], check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install ffmpeg on {distro}: {e}")
        elif distro in ("fedora",):
            try:
                subprocess.run(['sudo', 'dnf', 'install', '-y', 'ffmpeg'], check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install ffmpeg on {distro}: {e}")
        elif distro in ("centos", "rhel"):
            # CentOS 7 uses yum, CentOS 8 uses dnf
            try:
                subprocess.run(['sudo', 'dnf', 'install', '-y', 'ffmpeg'], check=True)
            except subprocess.CalledProcessError:
                subprocess.run(['sudo', 'yum', 'install', '-y', 'ffmpeg'], check=True)
        elif distro in ("arch", "manjaro"):
            try:
                subprocess.run(['sudo', 'pacman', '-S', '--noconfirm', 'ffmpeg'], check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install ffmpeg on {distro}: {e}")
        elif distro == "opensuse":
            try:
                subprocess.run(['sudo', 'zypper', 'install', '-y', 'ffmpeg'], check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install ffmpeg on {distro}: {e}")
        else:
            raise RuntimeError(f"Unsupported Linux distribution: {distro}. Please install ffmpeg manually.")
    else:
        raise RuntimeError(f"Unsupported OS: {system}. Please install ffmpeg manually.")

set_linux_env()

if not check_ffmpeg():
    try:
        install_ffmpeg()
    except Exception as e:
        print(f"Warning: ffmpeg not found and installation failed: {e}")

class TrayIcon(wx.adv.TaskBarIcon):
    TBMENU_RESTORE = wx.NewIdRef()
    TBMENU_EXIT = wx.NewIdRef()
    TBMENU_PLAYER_SHOW = wx.NewIdRef()
    TBMENU_PLAYER_TOGGLE = wx.NewIdRef()
    TBMENU_PLAYER_STOP = wx.NewIdRef()
    TBMENU_CAST = wx.NewIdRef()

    def __init__(self, parent, on_restore, on_exit, *, on_player_show=None, on_player_toggle=None, on_player_stop=None, on_cast=None):
        super().__init__()
        self.parent = parent
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.on_player_show = on_player_show
        self.on_player_toggle = on_player_toggle
        self.on_player_stop = on_player_stop
        self.on_cast = on_cast
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_taskbar_activate)
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, self.on_taskbar_activate)
        self.Bind(wx.EVT_MENU, self.on_menu_select)
        self.set_icon()

    def set_icon(self):
        icon = wx.Icon(wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_TOOLBAR, (16, 16)))
        self.SetIcon(icon, "Accessible IPTV Client")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        menu.Append(self.TBMENU_RESTORE, "Restore")
        player_menu = wx.Menu()
        player_menu.Append(self.TBMENU_PLAYER_SHOW, "Show Player")
        player_menu.Append(self.TBMENU_PLAYER_TOGGLE, "Play/Pause")
        player_menu.Append(self.TBMENU_PLAYER_STOP, "Stop")
        player_menu.AppendSeparator()
        player_menu.Append(self.TBMENU_CAST, "Cast / Connect...")
        menu.AppendSubMenu(player_menu, "Player Controls")
        menu.AppendSeparator()
        menu.Append(self.TBMENU_EXIT, "Exit")
        return menu

    def on_taskbar_activate(self, event):
        parent = self.parent
        if not getattr(parent, "_tray_allow_restore", True):
            return
        is_shown = parent.IsShownOnScreen() if hasattr(parent, "IsShownOnScreen") else parent.IsShown()
        if is_shown:
            return
        self.on_restore()

    def on_menu_select(self, event):
        eid = event.GetId()
        if eid == self.TBMENU_RESTORE:
            self.on_restore()
        elif eid == self.TBMENU_PLAYER_SHOW and self.on_player_show:
            self.on_player_show()
        elif eid == self.TBMENU_PLAYER_TOGGLE and self.on_player_toggle:
            self.on_player_toggle()
        elif eid == self.TBMENU_PLAYER_STOP and self.on_player_stop:
            self.on_player_stop()
        elif eid == self.TBMENU_CAST and self.on_cast:
            self.on_cast()
        elif eid == self.TBMENU_EXIT:
            self.on_exit()

class IPTVClient(wx.Frame):
    PLAYER_KEYS = [
        ("Built-in Player", "player_Internal"),
        ("VLC", "player_VLC"),
        ("MPC", "player_MPC"),
        ("MPC-BE", "player_MPCBE"),
        ("Kodi", "player_Kodi"),
        ("Winamp", "player_Winamp"),
        ("Foobar2000", "player_Foobar2000"),
        ("MPV", "player_MPV"),
        ("SMPlayer", "player_SMPlayer"),
        ("Totem", "player_Totem"),
        ("QuickTime", "player_QuickTime"),
        ("iTunes/Apple Music", "player_iTunes"),
        ("PotPlayer", "player_PotPlayer"),
        ("KMPlayer", "player_KMPlayer"),
        ("AIMP", "player_AIMP"),
        ("QMPlay2", "player_QMPlay2"),
        ("GOM Player", "player_GOMPlayer"),
        ("Audacious", "player_Audacious"),
        ("Fauxdacious", "player_Fauxdacious"),
        ("Clementine", "player_Clementine"),
        ("Strawberry", "player_Strawberry"),
        ("Amarok", "player_Amarok"),
        ("Rhythmbox", "player_Rhythmbox"),
        ("Pragha", "player_Pragha"),
        ("Lollypop", "player_Lollypop"),
        ("Exaile", "player_Exaile"),
        ("Quod Libet", "player_QuodLibet"),
        ("Gmusicbrowser", "player_Gmusicbrowser"),
        ("Xmms", "player_Xmms"),
        ("Vocal", "player_Vocal"),
        ("Haruna", "player_Haruna"),
        ("Celluloid", "player_Celluloid"),
    ]
    PLAYER_MENU_ATTRS = dict(PLAYER_KEYS)

    _CACHE_SHOW_STALE_SECS = 600
    _CACHE_REFRESH_AFTER_SECS = 180

    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))
        self.config = load_config()
        self.playlist_sources = self.config.get("playlists", [])
        self.epg_sources = self.config.get("epgs", [])
        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.displayed: List[Dict[str, str]] = []
        self.current_group = "All Channels"
        self.default_player = self.config.get("media_player", "Built-in Player")
        self.custom_player_path = self.config.get("custom_player_path", "")
        self.show_player_on_enter = self._bool_pref(self.config.get("show_player_on_enter", True), default=True)
        self.epg_importing = False
        self.epg_cache = {}
        self.epg_cache_lock = threading.Lock()
        self.refresh_timer = None
        self.minimize_to_tray = bool(self.config.get("minimize_to_tray", False))
        self.tray_icon = None
        self._tray_allow_restore = False
        self._tray_ready_timer: Optional[wx.CallLater] = None
        self.provider_clients: Dict[str, object] = {}
        self.provider_epg_sources: List[str] = []
        self._internal_player_frame: Optional[InternalPlayerFrame] = None

        # Casting Manager
        self.caster = CastingManager()
        self.caster.start()

        self.player_launcher = ExternalPlayerLauncher()

        # batch-population state to avoid UI hangs
        self._populate_token = 0

        # Timer for polling DB during EPG import so UI shows incoming data.
        self._epg_poll_timer: Optional[wx.Timer] = None
        # Track in-flight EPG fetches to avoid hammering get_now_next while importer is busy
        self._epg_fetch_inflight = set()
        self._epg_inflight_lock = threading.Lock()
        
        # Caching map: canonical_name -> db_channel_id
        self._epg_match_cache: Dict[str, Optional[str]] = {}
        # Dedicated executor for EPG lookups to avoid thread-spawning overhead
        self._epg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="EPGFetch")

        self._ensure_db_tuned()
        self._build_ui()
        self.Centre()

        self.group_list.Append("Loading playlists...")
        self.Show()

        # Defer all loading. This call starts ONLY the playlist loading thread.
        wx.CallAfter(self.start_playlist_load)
        
        self.Bind(wx.EVT_ICONIZE, self.on_minimize)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def _channel_is_epg_exempt(self, channel: Dict[str, str]) -> bool:
        """Detect channels that typically have no EPG (e.g., 24/7 loops).
        We do NOT modify names; this only avoids unnecessary DB lookups/logs.
        Rule: tvg-id empty AND name/group contains '24/7' or '24x7'.
        """
        try:
            tvg_id = (channel.get("tvg-id") or channel.get("tvg_id") or "").strip()
            if tvg_id:
                return False
            name = (channel.get("tvg-name") or channel.get("name") or "").lower()
            group = (channel.get("group-title") or channel.get("group") or "").lower()
            if "24/7" in name or "24x7" in name or "24/7" in group or "24x7" in group:
                return True
        except Exception:
            pass
        return False

    def start_playlist_load(self):
        """Kicks off ONLY the playlist loading thread."""
        threading.Thread(target=self._do_playlist_refresh, daemon=True).start()

    def _ensure_db_tuned(self):
        """Enable WAL and indices so read lookups don’t stall behind imports."""
        try:
            path = get_db_path()
            uri = f"file:{path}?cache=shared"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute("PRAGMA mmap_size=268435456;")
            cur.execute("PRAGMA cache_size=-65536;")
            cur.execute("PRAGMA wal_autocheckpoint=0;")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_end ON programmes(channel_id, end);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start ON programmes(channel_id, start);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_programmes_channel_start_end ON programmes(channel_id, start, end);")
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _sync_player_menu_from_config(self):
        defplayer = self.config.get("media_player", "Built-in Player")
        self.default_player = defplayer
        for key, attr in self.PLAYER_MENU_ATTRS.items():
            if hasattr(self, attr):
                getattr(self, attr).Check(key == defplayer)
        if hasattr(self, "player_Custom"):
            self.player_Custom.Check(defplayer == "Custom")
        if hasattr(self, "_player_radio_items"):
            for label, item in self._player_radio_items.items():
                item.Check(label == defplayer)

    def on_menu_open(self, event):
        from options import load_config
        self.config = load_config()
        self._sync_player_menu_from_config()
        if hasattr(self, "min_to_tray_item"):
            self.minimize_to_tray = bool(self.config.get("minimize_to_tray", False))
            self.min_to_tray_item.Check(self.minimize_to_tray)
        self.show_player_on_enter = self._bool_pref(self.config.get("show_player_on_enter", True), default=True)
        if hasattr(self, "show_player_on_enter_item"):
            try:
                self.show_player_on_enter_item.Check(self.show_player_on_enter)
            except Exception:
                pass
        event.Skip()

    def start_refresh_timer(self):
        if self.refresh_timer:
            self.refresh_timer.Stop()
        self.refresh_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer_refresh, self.refresh_timer)
        self.refresh_timer.Start(3 * 60 * 60 * 1000, wx.TIMER_CONTINUOUS)

    def on_timer_refresh(self, event):
        # This full cycle can be triggered by the timer.
        self.start_playlist_load()

    def _do_playlist_refresh(self):
        """
        Loads playlists from cache for a fast UI update, then refreshes from the network.
        Crucially, it only starts the EPG import *after* playlists are loaded.
        """
        import concurrent.futures

        playlist_sources = self.config.get("playlists", [])
        # Ensure all provider entries have persistent IDs so we can track clients.
        mutated = False
        for src in playlist_sources:
            if isinstance(src, dict) and not src.get("id"):
                src["id"] = generate_provider_id()
                mutated = True
        if mutated:
            self.config["playlists"] = playlist_sources
            save_config(self.config)

        channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        all_channels: List[Dict[str, str]] = []
        valid_caches = set()
        seen_channel_keys = set()

        # Fast prefill from parsed caches (no network) so UI shows something immediately.
        prefilled_by_group: Dict[str, List[Dict[str, str]]] = {}
        prefilled_all: List[Dict[str, str]] = []
        prefill_seen = set()

        def _prefill_from_cache(src) -> None:
            parsed_cache = None
            provider_meta = None
            if isinstance(src, dict):
                stype = (src.get("type") or "").lower()
                provider_id = src.get("id") or src.get("provider_id")
                if stype in ("xtream", "stalker"):
                    cache_key = provider_id or f"{stype}:{src.get('base_url') or src.get('url') or ''}:{src.get('username', '')}"
                    parsed_cache = self._parsed_cache_path_for_key(f"provider:{cache_key}")
                    provider_meta = {"provider-type": stype, "provider-id": provider_id}
            elif isinstance(src, str) and src.startswith(("http://", "https://")):
                parsed_cache = self._parsed_cache_path_for_key(src)
            elif isinstance(src, str) and os.path.exists(src):
                parsed_cache = self._parsed_cache_path_for_key(f"file:{os.path.abspath(src)}")
            if not parsed_cache or not os.path.exists(parsed_cache):
                return
            cached = self._load_cached_playlist(parsed_cache, text_hash=None, provider_meta=provider_meta, skip_hash=True)
            if not cached:
                return
            for ch in cached:
                key = (ch.get("name", ""), ch.get("url", ""), ch.get("provider-id", ""))
                if key in prefill_seen:
                    continue
                prefill_seen.add(key)
                grp = ch.get("group") or "Uncategorized"
                prefilled_by_group.setdefault(grp, []).append(ch)
                prefilled_all.append(ch)

        for _src in playlist_sources:
            _prefill_from_cache(_src)

        if prefilled_all:
            seen_channel_keys.update(prefill_seen)
            channels_by_group = {grp: lst.copy() for grp, lst in prefilled_by_group.items()}
            all_channels = list(prefilled_all)

            def apply_prefill(pref_by_group, pref_all):
                self.channels_by_group = pref_by_group
                self.all_channels = pref_all
                self._refresh_group_ui()

            wx.CallAfter(apply_prefill, prefilled_by_group, prefilled_all)

        # We will collect these from the workers
        provider_clients_local: Dict[str, object] = {}
        provider_epg_sources: List[str] = []

        def fetch_and_process_playlist(src):
            result = {
                "channels": [],
                "clients": {},
                "epg_sources": [],
                "valid_cache": None,
                "error": None
            }
            try:
                if isinstance(src, dict):
                    stype = (src.get("type") or "").lower()
                    provider_id = src.get("id") or src.get("provider_id")
                    if stype == "xtream":
                        cfg = XtreamCodesConfig(
                            base_url=src.get("base_url") or src.get("url") or "",
                            username=src.get("username", ""),
                            password=src.get("password", ""),
                            stream_type=src.get("stream_type", "m3u_plus"),
                            output=src.get("output", "ts"),
                            name=src.get("name"),
                            auto_epg=bool(src.get("auto_epg", True)),
                            provider_id=provider_id
                        )
                        client = XtreamCodesClient(cfg)
                        text = client.fetch_playlist()
                        text_hash = self._playlist_text_hash(text)
                        cache_key = provider_id or f"xtream:{cfg.base_url}:{cfg.username}"
                        parsed_cache = self._parsed_cache_path_for_key(f"provider:{cache_key}")
                        provider_meta = {"provider-type": "xtream", "provider-id": provider_id}
                        
                        channels = None
                        if parsed_cache and text_hash:
                            channels = self._load_cached_playlist(parsed_cache, text_hash, provider_meta)
                        if channels is None:
                            channels = self._parse_m3u_return(text, provider_info=provider_meta)
                            if parsed_cache and text_hash:
                                self._store_cached_playlist(parsed_cache, text_hash, channels, provider_meta)
                        
                        result["channels"] = channels or []
                        result["clients"][provider_id] = client
                        if cfg.auto_epg:
                            for epg in client.epg_urls():
                                if epg: result["epg_sources"].append(epg)

                    elif stype == "stalker":
                        cfg = StalkerPortalConfig(
                            base_url=src.get("base_url") or src.get("url") or "",
                            username=src.get("username", ""),
                            password=src.get("password", ""),
                            mac=src.get("mac", ""),
                            name=src.get("name"),
                            auto_epg=bool(src.get("auto_epg", True)),
                            provider_id=provider_id
                        )
                        client = StalkerPortalClient(cfg)
                        channels, epgs = client.fetch_channels()
                        for ch in channels:
                            ch.setdefault("provider-id", provider_id)
                            ch.setdefault("provider-type", "stalker")
                        
                        result["channels"] = channels
                        result["clients"][provider_id] = client
                        for epg in epgs:
                            if epg: result["epg_sources"].append(epg)
                    else:
                        pass # Unknown dict source
                    return result

                # Plain playlist path or URL
                if isinstance(src, str) and src.startswith(("http://", "https://")):
                    cache_path = get_cache_path_for_url(src)
                    parsed_cache = self._parsed_cache_path_for_key(src)
                    result["valid_cache"] = cache_path
                    
                    download = True
                    if os.path.exists(cache_path):
                        age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(cache_path))).total_seconds()
                        if age < 15 * 60:
                            download = False

                    text = ""
                    if download:
                        with urllib.request.urlopen(
                            urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"}), timeout=60
                        ) as resp:
                            raw = resp.read()
                            try:
                                text = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                text = raw.decode("latin-1", "ignore")
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(text)
                    else:
                        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                            
                    text_hash = self._playlist_text_hash(text)
                    
                    channels = None
                    if parsed_cache and text_hash:
                        channels = self._load_cached_playlist(parsed_cache, text_hash, provider_meta=None)
                    if channels is None:
                        channels = self._parse_m3u_return(text, provider_info=None)
                        if parsed_cache and text_hash:
                            self._store_cached_playlist(parsed_cache, text_hash, channels, provider_meta=None)
                    
                    result["channels"] = channels or []

                elif isinstance(src, str) and os.path.exists(src):
                    with open(src, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    text_hash = self._playlist_text_hash(text)
                    cache_key = f"file:{os.path.abspath(src)}"
                    parsed_cache = self._parsed_cache_path_for_key(cache_key)
                    
                    channels = None
                    if parsed_cache and text_hash:
                        channels = self._load_cached_playlist(parsed_cache, text_hash, provider_meta=None)
                    if channels is None:
                        channels = self._parse_m3u_return(text, provider_info=None)
                        if parsed_cache and text_hash:
                            self._store_cached_playlist(parsed_cache, text_hash, channels, provider_meta=None)
                            
                    result["channels"] = channels or []
                else:
                    pass # Invalid source
            except Exception as e:
                result["error"] = str(e)
                # LOG.error(f"Error fetching playlist {src}: {e}")
            
            return result

        # Execute in parallel (fetching AND parsing)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_and_process_playlist, src) for src in playlist_sources]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res["error"]:
                    continue
                
                if res["valid_cache"]:
                    valid_caches.add(res["valid_cache"])
                
                provider_clients_local.update(res["clients"])
                for epg in res["epg_sources"]:
                    if epg not in provider_epg_sources:
                        provider_epg_sources.append(epg)
                
                for ch in res["channels"]:
                    key = (ch.get("name", ""), ch.get("url", ""), ch.get("provider-id", ""))
                    if key in seen_channel_keys:
                        continue
                    seen_channel_keys.add(key)
                    grp = ch.get("group") or "Uncategorized"
                    channels_by_group.setdefault(grp, []).append(ch)
                    all_channels.append(ch)

        # Replace provider mappings atomically after successful refresh
        self.provider_clients = provider_clients_local
        self.provider_epg_sources = provider_epg_sources

        def finish_playlist_load_and_start_background_tasks():
            self.channels_by_group = channels_by_group
            self.all_channels = all_channels
            self._refresh_group_ui()
            self._cleanup_cache_and_channels(valid_caches)

            # Now that playlists are loaded, start the other processes.
            self.reload_epg_sources()
            self.start_refresh_timer()
            wx.CallLater(2000, self.start_epg_import_background)

        wx.CallAfter(finish_playlist_load_and_start_background_tasks)


    def _cleanup_cache_and_channels(self, valid_caches):
        cache_dir = get_cache_dir()
        try:
            files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.endswith(".m3u")]
        except Exception:
            return
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
        # Key bindings (original + added robust handlers)
        self.channel_list.Bind(wx.EVT_CHAR_HOOK, self.on_channel_key)  # original
        self.channel_list.Bind(wx.EVT_KEY_DOWN, self._on_channel_key_down)  # reliable Enter on all platforms
        # Mouse activation (original + added wx generic left double click)
        self.channel_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_highlight())
        self.channel_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _: self.play_selected())  # original
        self.channel_list.Bind(wx.EVT_LEFT_DCLICK, self._on_lb_activate)  # GTK/mac fallback
        self.channel_list.Bind(wx.EVT_CONTEXT_MENU, self._on_channel_context_menu)

        self.epg_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.url_display = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_MULTILINE)
        vs_r.Add(self.filter_box, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.channel_list, 1, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.epg_display, 0, wx.EXPAND | wx.ALL, 5)
        vs_r.Add(self.url_display, 0, wx.EXPAND | wx.ALL, 5)
        hs.Add(vs_l, 1, wx.EXPAND)
        hs.Add(vs_r, 2, wx.EXPAND)

        if platform.system() == "Linux":
            self.menu_button = wx.Button(p, label="Menu")
            self._player_radio_items = {}
            def on_menu_btn(evt):
                menu = wx.Menu()
                menu.Append(1001, "Playlist Manager\tCtrl+M")
                menu.Append(1002, "EPG Manager\tCtrl+E")
                menu.Append(1003, "Import EPG to DB\tCtrl+I")
                menu.AppendSeparator()
                player_ctrl_menu = wx.Menu()
                player_ctrl_menu.Append(1201, "Show Built-in Player")
                player_ctrl_menu.Append(1202, "Play/Pause")
                player_ctrl_menu.Append(1203, "Stop")
                player_ctrl_menu.Append(1204, "Cast / Connect...")
                menu.AppendSubMenu(player_ctrl_menu, "Player")
                self.Bind(wx.EVT_MENU, self._menu_show_player, id=1201)
                self.Bind(wx.EVT_MENU, self._menu_toggle_player, id=1202)
                self.Bind(wx.EVT_MENU, self._menu_stop_player, id=1203)
                self.Bind(wx.EVT_MENU, self._menu_cast_from_player, id=1204)
                menu.AppendSeparator()
                player_menu = wx.Menu()
                for idx, (label, attr) in enumerate(self.PLAYER_KEYS):
                    itemid = 2000 + idx
                    item = player_menu.AppendRadioItem(itemid, label)
                    self._player_radio_items[label] = item
                    self.Bind(wx.EVT_MENU, lambda evt, pl=label: self._select_player(pl), id=itemid)
                    if self.default_player == label:
                        item.Check(True)
                customid = 2999
                customitem = player_menu.AppendRadioItem(customid, "Custom Player...")
                self.Bind(wx.EVT_MENU, self._select_custom_player, id=customid)
                if self.default_player == "Custom":
                    customitem.Check(True)
                menu.AppendSubMenu(player_menu, "Media Player to Use")
                min_to_tray_id = 1101
                min_item = menu.AppendCheckItem(min_to_tray_id, "Minimize to System Tray")
                min_item.Check(self.minimize_to_tray)
                self.Bind(wx.EVT_MENU, self.on_toggle_min_to_tray, id=min_to_tray_id)
                menu.AppendSeparator()
                show_enter_id = 1102
                show_enter_item = menu.AppendCheckItem(show_enter_id, "Show Player on Enter")
                show_enter_item.Check(self.show_player_on_enter)
                self.Bind(wx.EVT_MENU, self.on_toggle_show_player_on_enter, id=show_enter_id)
                menu.AppendSeparator()
                
                # Casting Menu Item (Linux)
                menu.Append(1005, "Cast To...")
                self.Bind(wx.EVT_MENU, self.show_cast_dialog, id=1005)
                
                menu.Append(1004, "Exit\tCtrl+Q")
                self.Bind(wx.EVT_MENU, self.show_manager, id=1001)
                self.Bind(wx.EVT_MENU, self.show_epg_manager, id=1002)
                self.Bind(wx.EVT_MENU, self.import_epg, id=1003)
                self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=1004)
                self.menu_button.PopupMenu(menu)
            self.menu_button.Bind(wx.EVT_BUTTON, on_menu_btn)
            sizer_with_menu = wx.BoxSizer(wx.VERTICAL)
            sizer_with_menu.Add(self.menu_button, 0, wx.EXPAND | wx.ALL, 5)
            sizer_with_menu.Add(hs, 1, wx.EXPAND)
            p.SetSizerAndFit(sizer_with_menu)
        else:
            p.SetSizerAndFit(hs)
            mb = wx.MenuBar()
            fm = wx.Menu()
            m_mgr = fm.Append(wx.ID_ANY, "Playlist Manager\tCtrl+M")
            m_epg = fm.Append(wx.ID_ANY, "EPG Manager\tCtrl+E")
            m_imp = fm.Append(wx.ID_ANY, "Import EPG to DB\tCtrl+I")
            fm.AppendSeparator()
            # Casting Menu Item (Windows/Mac)
            m_cast = fm.Append(wx.ID_ANY, "Cast To...")
            fm.AppendSeparator()
            m_exit = fm.Append(wx.ID_EXIT, "Exit\tCtrl+Q")
            mb.Append(fm, "File")
            pm = wx.Menu()
            pm_show = pm.Append(wx.ID_ANY, "Show Built-in Player\tCtrl+Shift+J")
            pm_toggle = pm.Append(wx.ID_ANY, "Play/Pause\tCtrl+Shift+P")
            pm_stop = pm.Append(wx.ID_ANY, "Stop\tCtrl+Shift+S")
            pm_cast = pm.Append(wx.ID_ANY, "Cast / Connect...\tCtrl+Shift+C")
            mb.Append(pm, "Player")
            om = wx.Menu()
            player_menu = wx.Menu()
            self.player_menu_items = []
            for label, attr in self.PLAYER_KEYS:
                item = player_menu.AppendRadioItem(wx.ID_ANY, label)
                setattr(self, attr, item)
                self.player_menu_items.append((item, label))
            self.player_Custom = player_menu.AppendRadioItem(wx.ID_ANY, "Custom Player...")
            om.AppendSubMenu(player_menu, "Media Player to Use")
            self.min_to_tray_item = om.AppendCheckItem(wx.ID_ANY, "Minimize to System Tray")
            self.show_player_on_enter_item = om.AppendCheckItem(wx.ID_ANY, "Show Player on Enter")
            om.AppendSeparator()
            mb.Append(om, "Options")
            self.SetMenuBar(mb)
            self.Bind(wx.EVT_MENU, self.show_manager, m_mgr)
            self.Bind(wx.EVT_MENU, self.show_epg_manager, m_epg)
            self.Bind(wx.EVT_MENU, self.import_epg, m_imp)
            self.Bind(wx.EVT_MENU, self.show_cast_dialog, m_cast)
            self.Bind(wx.EVT_MENU, lambda _: self.Close(), m_exit)
            self.Bind(wx.EVT_MENU, self._menu_show_player, pm_show)
            self.Bind(wx.EVT_MENU, self._menu_toggle_player, pm_toggle)
            self.Bind(wx.EVT_MENU, self._menu_stop_player, pm_stop)
            self.Bind(wx.EVT_MENU, self._menu_cast_from_player, pm_cast)
            for item, key in self.player_menu_items:
                self.Bind(wx.EVT_MENU, lambda evt, attr=key: self._select_player(attr), item)
            self.Bind(wx.EVT_MENU, self._select_custom_player, self.player_Custom)
            self.Bind(wx.EVT_MENU, self.on_toggle_min_to_tray, self.min_to_tray_item)
            self.Bind(wx.EVT_MENU, self.on_toggle_show_player_on_enter, self.show_player_on_enter_item)
            self.Bind(wx.EVT_MENU_OPEN, self.on_menu_open)
            self._sync_player_menu_from_config()
            self.min_to_tray_item.Check(self.minimize_to_tray)
            self.show_player_on_enter_item.Check(self.show_player_on_enter)

        self.group_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_group_select())
        self.filter_box.Bind(wx.EVT_TEXT_ENTER, lambda _: self.apply_filter())

        entries = [
            (wx.ACCEL_CTRL, ord('M'), 4001),
            (wx.ACCEL_CTRL, ord('E'), 4002),
            (wx.ACCEL_CTRL, ord('I'), 4003),
            (wx.ACCEL_CTRL, ord('Q'), 4004),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('P'), 4010),  # Play/Pause
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('S'), 4011),  # Stop
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('C'), 4012),  # Cast/connect
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('K'), 4013),  # Volume up
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('J'), 4014),  # Volume down
            (wx.ACCEL_CTRL, wx.WXK_UP, 4015),   # Volume up (Ctrl+Up)
            (wx.ACCEL_CTRL, wx.WXK_DOWN, 4016), # Volume down (Ctrl+Down)
        ]
        atable = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(atable)
        self.Bind(wx.EVT_MENU, self.show_manager, id=4001)
        self.Bind(wx.EVT_MENU, self.show_epg_manager, id=4002)
        self.Bind(wx.EVT_MENU, self.import_epg, id=4003)
        self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=4004)
        self.Bind(wx.EVT_MENU, self._menu_toggle_player, id=4010)
        self.Bind(wx.EVT_MENU, self._menu_stop_player, id=4011)
        self.Bind(wx.EVT_MENU, self._menu_cast_from_player, id=4012)
        self.Bind(wx.EVT_MENU, lambda _: self._adjust_internal_volume(+2), id=4013)
        self.Bind(wx.EVT_MENU, lambda _: self._adjust_internal_volume(-2), id=4014)
        self.Bind(wx.EVT_MENU, lambda _: self._adjust_internal_volume(+2), id=4015)
        self.Bind(wx.EVT_MENU, lambda _: self._adjust_internal_volume(-2), id=4016)

    def _on_lb_activate(self, event):
        # Called on generic left double click to ensure activation on GTK/mac too
        self.play_selected()
        # Intentionally do not event.Skip() to avoid duplicate handling.

    def _on_channel_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.play_selected(show_internal_player=self.show_player_on_enter)
            return  # swallow to prevent beep/focus issues
        event.Skip()

    def _on_channel_context_menu(self, event):
        if not self.displayed:
            return
        pos = event.GetPosition()
        idx = self.channel_list.GetSelection()
        if pos != wx.DefaultPosition and not (pos.x == -1 and pos.y == -1):
            try:
                local = self.channel_list.ScreenToClient(pos)
                hit = self.channel_list.HitTest(local) if hasattr(self.channel_list, "HitTest") else (-1,)
                hit_idx = hit[0] if isinstance(hit, tuple) else hit
                if hit_idx not in (None, -1) and 0 <= hit_idx < len(self.displayed):
                    self.channel_list.SetSelection(hit_idx)
                    self.channel_list.SetFocus()
                    idx = hit_idx
            except Exception:
                pass
        if idx == wx.NOT_FOUND and self.channel_list.GetCount():
            self.channel_list.SetSelection(0)
            idx = 0
        if idx == wx.NOT_FOUND or idx >= len(self.displayed):
            return
        item = self.displayed[idx]
        channel = None
        if item.get("type") == "channel":
            channel = item.get("data")
        elif item.get("type") == "epg":
            channel = self._find_channel_for_epg(item.get("data", {}))
        if not channel:
            return
        menu = wx.Menu()
        play_item = menu.Append(wx.ID_ANY, "Play")
        menu.Bind(wx.EVT_MENU, lambda evt: self.play_selected(), play_item)
        
        if not self._channel_is_epg_exempt(channel):
            epg_item = menu.Append(wx.ID_ANY, "View EPG...")
            menu.Bind(wx.EVT_MENU, lambda evt, ch=channel: self._view_channel_epg(ch), epg_item)

        if self._channel_has_catchup(channel):
            catch_item = menu.Append(wx.ID_ANY, "Play Catch-up…")
            menu.Bind(wx.EVT_MENU, lambda evt, ch=channel: self._open_catchup_dialog(ch), catch_item)
        try:
            self.channel_list.PopupMenu(menu)
        finally:
            menu.Destroy()

    def _view_channel_epg(self, channel: Dict[str, str]):
        def fetch_and_show():
            try:
                db = EPGDatabase(get_db_path(), readonly=True)
                now = datetime.datetime.now(datetime.timezone.utc)
                start_dt = now - datetime.timedelta(hours=4)
                end_dt = now + datetime.timedelta(hours=24)
                programmes = db.get_schedule(channel, start_dt, end_dt)
                db.close()
                
                wx.CallAfter(lambda: self._show_epg_dialog(channel.get("name", ""), programmes))
            except Exception as e:
                wx.CallAfter(lambda: wx.MessageBox(f"Error fetching EPG: {e}", "Error", wx.OK | wx.ICON_ERROR))

        threading.Thread(target=fetch_and_show, daemon=True).start()

    def _show_epg_dialog(self, channel_name, programmes):
        if not programmes:
            wx.MessageBox("No upcoming schedule found for this channel.", "EPG", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = ChannelEPGDialog(self, channel_name, programmes)
        dlg.ShowModal()
        dlg.Destroy()

    def on_toggle_min_to_tray(self, event):
        if platform.system() == "Linux":
            self.minimize_to_tray = not self.minimize_to_tray
        else:
            self.minimize_to_tray = self.min_to_tray_item.IsChecked()
        self.config["minimize_to_tray"] = self.minimize_to_tray
        save_config(self.config)

    def on_toggle_show_player_on_enter(self, event):
        self.show_player_on_enter = event.IsChecked()
        self.config["show_player_on_enter"] = self.show_player_on_enter
        save_config(self.config)
        if not self.show_player_on_enter:
            frame = getattr(self, "_internal_player_frame", None)
            if frame and frame.IsShown():
                frame.Hide()

    @staticmethod
    def _bool_pref(value, default: bool = False) -> bool:
        """Coerce config values that might be stored as bools/strings/ints."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(default)

    def show_tray_icon(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if not self.tray_icon:
            self.tray_icon = TrayIcon(
                self,
                on_restore=self.restore_from_tray,
                on_exit=self.exit_from_tray,
                on_player_show=self._tray_show_player,
                on_player_toggle=self._tray_toggle_play_pause,
                on_player_stop=self._tray_stop_player,
                on_cast=self._tray_cast
            )
        self.Hide()
        self._tray_ready_timer = wx.CallLater(250, self._enable_tray_restore)

    def restore_from_tray(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if self.tray_icon:
            try:
                self.tray_icon.RemoveIcon()
            except Exception:
                pass
            self.tray_icon.Destroy()
            self.tray_icon = None
        self.Show()
        self.Raise()
        self.Iconize(False)

    def _tray_show_player(self):
        try:
            frame = self._ensure_internal_player()
        except Exception:
            return
        frame.Enable(True)
        frame.Show()
        frame.Raise()

    def _tray_toggle_play_pause(self):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            wx.CallAfter(frame._on_toggle_pause)

    def _tray_stop_player(self):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            wx.CallAfter(lambda: frame.stop(manual=True))

    def _tray_cast(self):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            wx.CallAfter(lambda: frame._on_cast())

    def _menu_show_player(self, _=None):
        try:
            frame = self._ensure_internal_player()
        except Exception:
            return
        frame.Enable(True)
        frame.Show()
        frame.Raise()

    def _menu_toggle_player(self, _=None):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            frame._on_toggle_pause()

    def _menu_stop_player(self, _=None):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            frame.stop(manual=True)

    def _menu_cast_from_player(self, _=None):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            frame._on_cast()

    def _adjust_internal_volume(self, delta: int):
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            wx.CallAfter(frame._adjust_volume, delta)

    def exit_from_tray(self):
        self._tray_allow_restore = False
        self._cancel_tray_ready_timer()
        if self.tray_icon:
            try:
                self.tray_icon.RemoveIcon()
            except Exception:
                pass
            self.tray_icon.Destroy()
            self.tray_icon = None
        self.caster.stop()
        self.Destroy()

    def _enable_tray_restore(self):
        self._tray_ready_timer = None
        if self.tray_icon:
            self._tray_allow_restore = True

    def _cancel_tray_ready_timer(self):
        if self._tray_ready_timer:
            try:
                self._tray_ready_timer.Stop()
            except Exception:
                pass
            self._tray_ready_timer = None

    def on_minimize(self, event):
        if self.minimize_to_tray and event.IsIconized():
            wx.CallAfter(self.show_tray_icon)
        else:
            event.Skip()

    def on_close(self, event):
        if self.minimize_to_tray:
            wx.CallAfter(self.show_tray_icon)
            event.Veto()
        else:
            # Ensure poll timer stopped on exit
            try:
                self._stop_epg_poll_timer()
            except Exception:
                pass
            if hasattr(self, "_epg_executor"):
                self._epg_executor.shutdown(wait=False)
            if self.caster:
                self.caster.stop()
            if self.tray_icon:
                try:
                    self.tray_icon.RemoveIcon()
                except Exception:
                    pass
                self.tray_icon.Destroy()
                self.tray_icon = None
            frame = getattr(self, "_internal_player_frame", None)
            if frame is not None:
                try:
                    frame.Destroy()
                except Exception:
                    pass
                self._internal_player_frame = None
            self.Destroy()

    def _select_player(self, player):
        self.default_player = player
        self.config["media_player"] = player
        save_config(self.config)
        self._sync_player_menu_from_config()

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
        self._sync_player_menu_from_config()

    def on_channel_key(self, event):
        # Kept for compatibility; EVT_KEY_DOWN handler above is the reliable path
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
        self._populate_token += 1
        self.displayed = []
        self.channel_list.Freeze()
        try:
            self.channel_list.Clear()
            source = (self.all_channels if self.current_group == "All Channels"
                      else self.channels_by_group.get(self.current_group, []))
            if not txt:
                # Rebuild current group quickly without blocking the UI
                self.channel_list.Thaw()
                self.on_group_select()
                return

            items = []
            for ch in source:
                name = (ch.get("name") or "")
                if txt and txt not in name.lower():
                    continue
                self.displayed.append({"type": "channel", "data": ch})
                items.append(name)

            if items:
                self.channel_list.AppendItems(items)

            # Kick off EPG search in background and append results later
            if not hasattr(self, "_search_token"):
                self._search_token = 0
            self._search_token += 1
            my_token = self._search_token

            def epg_search(token):
                try:
                    db = EPGDatabase(get_db_path(), readonly=True)
                    try:
                        if hasattr(db, "conn"):
                            db.conn.execute("PRAGMA busy_timeout=2000;")
                            db.conn.execute("PRAGMA read_uncommitted=1;")
                    except Exception:
                        pass
                    results = db.get_channels_with_show(txt)
                    try:
                        if hasattr(db, "close"):
                            db.close()
                        elif hasattr(db, "conn"):
                            db.conn.close()
                    except Exception:
                        pass
                except Exception:
                    results = []
                def update_ui():
                    if getattr(self, "_search_token", 0) != token:
                        return
                    if txt != self.filter_box.GetValue().strip().lower():
                        return
                    # Preserve current selection to avoid scroll jumps while appending
                    try:
                        cur_sel = self.channel_list.GetSelection()
                        cur_count = self.channel_list.GetCount()
                    except Exception:
                        cur_sel, cur_count = wx.NOT_FOUND, 0
                    if results:
                        add_items = []
                        for r in results:
                            chan_name = r.get('channel_name') or ""
                            show_name = r.get('show_title') or ""
                            chan_lower = chan_name.lower()
                            show_lower = show_name.lower()
                            if txt and txt not in chan_lower and txt not in show_lower:
                                continue
                            label = f"{r['channel_name']} - {r['show_title']} ({self._fmt_time(r['start'])}–{self._fmt_time(r['end'])})"
                            self.displayed.append({"type": "epg", "data": r})
                            add_items.append(label)
                        if add_items:
                            self.channel_list.AppendItems(add_items)
                    # Only auto-select the first item if the list was previously empty
                    # and nothing is selected. Do NOT steal focus or jump the list.
                    try:
                        if cur_count == 0 and cur_sel in (-1, wx.NOT_FOUND) and self.channel_list.GetCount() > 0:
                            # Leave selection empty to avoid scroll jump; user can choose.
                            # If desired later, we can make this opt-in via a setting.
                            pass
                        elif cur_sel not in (-1, wx.NOT_FOUND) and cur_sel < self.channel_list.GetCount():
                            # Reinstate prior selection to keep view position stable.
                            self.channel_list.SetSelection(cur_sel)
                    except Exception:
                        pass
                wx.CallAfter(update_ui)
            threading.Thread(target=lambda: epg_search(my_token), daemon=True).start()
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass

    def _refresh_group_ui(self):
        self.group_list.Freeze()
        try:
            self.group_list.Clear()
            self.channel_list.Clear()

            if not self.all_channels:
                self.group_list.Append("No channels found.")
                return

            self.group_list.Append(f"All Channels ({len(self.all_channels)})")
            for grp in sorted(self.channels_by_group):
                self.group_list.Append(f"{grp} ({len(self.channels_by_group[grp])})")
            
            try:
                current_idx = self.group_list.FindString(self.current_group)
                if current_idx != wx.NOT_FOUND and self.current_group != "All Channels":
                    for i in range(self.group_list.GetCount()):
                        if self.group_list.GetString(i).startswith(f"{self.current_group} ("):
                            current_idx = i
                            break
                self.group_list.SetSelection(current_idx if current_idx != wx.NOT_FOUND else 0)
            except Exception:
                self.group_list.SetSelection(0)
        finally:
            try:
                self.group_list.Thaw()
            except Exception:
                pass
        
        self.on_group_select()

    def reload_epg_sources(self):
        base = list(self.config.get("epgs", []))
        for epg in getattr(self, "provider_epg_sources", []):
            if epg not in base:
                base.append(epg)
        self.epg_sources = base

    def start_epg_import_background(self):
        sources = list(self.epg_sources)
        if not sources or not self.config.get("epg_enabled", True):
            return
        if self.epg_importing:
            return
        self.epg_importing = True

        # Start a short poll timer so UI can show EPG as it arrives for the selected channel.
        wx.CallAfter(self._start_epg_poll_timer)

        def do_import():
            try:
                db = EPGDatabase(get_db_path(), for_threading=True)
                # Pass a coarse progress callback (per-source). The DB importer writes as it streams,
                # so readers can pick up newly inserted rows during import.
                db.import_epg_xml(sources)
                try:
                    if hasattr(db, "close"):
                        db.close()
                    elif hasattr(db, "conn"):
                        db.conn.close()
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                wx.CallAfter(self.finish_import_background)
        threading.Thread(target=do_import, daemon=True).start()

    def finish_import_background(self):
        self.epg_importing = False
        # Stop import-specific polling and restart steady refresh timer
        self._stop_epg_poll_timer()
        with self.epg_cache_lock:
            self.epg_cache.clear()
        # Clear match cache as IDs/channels may have changed in the DB
        self._epg_match_cache.clear()
        self.on_highlight()
        self._start_epg_poll_timer()

    def show_manager(self, _):
        dlg = PlaylistManagerDialog(self, self.playlist_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.playlist_sources = dlg.GetResult()
            self.config["playlists"] = self.playlist_sources
            save_config(self.config)
            self.start_playlist_load() # Reload everything after changes
        dlg.Destroy()

    def show_epg_manager(self, _):
        dlg = EPGManagerDialog(self, self.epg_sources)
        if dlg.ShowModal() == wx.ID_OK:
            self.epg_sources = dlg.GetResult()
            self.config["epgs"] = self.epg_sources
            save_config(self.config)
            self.reload_epg_sources()
            wx.CallLater(1000, self.start_epg_import_background) # Start import after dialog closes
        dlg.Destroy()

    def import_epg(self, _):
        if self.epg_importing:
            wx.MessageBox("EPG import is already in progress.", "In Progress", wx.OK | wx.ICON_INFORMATION)
            return
        
        if not self.epg_sources:
            wx.MessageBox("No EPG sources configured. Please add one in File > EPG Manager.", "No Sources", wx.OK | wx.ICON_WARNING)
            return

        wx.MessageBox("EPG import will start in the background.", "Import Started", wx.OK | wx.ICON_INFORMATION)
        self.start_epg_import_background()

    def finish_import(self):
        # legacy-sounding API; ensure poll timer is stopped here too.
        self.epg_importing = False
        self._stop_epg_poll_timer()
        with self.epg_cache_lock:
            self.epg_cache.clear()
        self.on_highlight()
        self._start_epg_poll_timer()

    def _parse_m3u_return(self, text, provider_info=None):
        provider_info = provider_info or {}
        provider_id = provider_info.get("provider-id")
        provider_type = provider_info.get("provider-type")

        out: List[Dict[str, str]] = []
        append = out.append
        extract_group_local = extract_group
        stream_id_for = self._extract_stream_id
        attr_iter = _M3U_ATTR_RE.finditer

        # Per-channel metadata, reset after each URL
        name = ""
        group = ""
        tvg_id = ""
        tvg_name = ""
        tvg_logo = ""
        tvg_rec = ""
        timeshift = ""
        catchup = ""
        catchup_type = ""
        catchup_days = ""
        catchup_source = ""
        catchup_offset = ""
        http_user_agent = ""
        http_referrer = ""
        http_origin = ""
        http_cookie = ""
        http_headers: List[str] = []
        http_auth = ""
        http_accept = ""

        for raw_line in text.splitlines():
            s = raw_line.strip()
            if not s:
                continue

            if s[0] == '#':
                upper_prefix = s[:10].upper()
                if upper_prefix.startswith("#EXTINF"):
                    name = ""
                    group = ""
                    tvg_id = ""
                    tvg_name = ""
                    tvg_logo = ""
                    tvg_rec = ""
                    timeshift = ""
                    catchup = ""
                    catchup_type = ""
                    catchup_days = ""
                    catchup_source = ""
                    catchup_offset = ""
                    http_user_agent = ""
                    http_referrer = ""
                    http_origin = ""
                    http_cookie = ""
                    http_headers = []
                    http_auth = ""
                    http_accept = ""

                    comma_idx = s.find(',')
                    info_part = s if comma_idx == -1 else s[:comma_idx]
                    if comma_idx != -1:
                        name = s[comma_idx + 1:].strip()

                    colon_idx = info_part.find(':')
                    attr_segment = info_part[colon_idx + 1:] if colon_idx != -1 else ""
                    if attr_segment:
                        attrs: Dict[str, str] = {}
                        for match in attr_iter(attr_segment):
                            key = match.group(1).lower()
                            value = match.group(2) or match.group(3) or match.group(4) or ""
                            if key not in attrs:
                                attrs[key] = value.strip()
                        if attrs:
                            group = attrs.get("group-title", "")
                            tvg_id = attrs.get("tvg-id", "")
                            tvg_name = attrs.get("tvg-name", "")
                            tvg_logo = attrs.get("tvg-logo") or attrs.get("logo") or ""
                            tvg_rec = attrs.get("tvg-rec", "")
                            timeshift = attrs.get("timeshift", "")
                            catchup = attrs.get("catchup", "")
                            catchup_type = attrs.get("catchup-type", "")
                            catchup_days = attrs.get("catchup-days", "")
                            catchup_source = attrs.get("catchup-source", "")
                            catchup_offset = attrs.get("catchup-offset", "")
                            http_user_agent = attrs.get("http-user-agent", "")
                            http_referrer = attrs.get("http-referrer") or attrs.get("http-referer", http_referrer)
                            http_origin = attrs.get("http-origin", http_origin)
                            http_cookie = attrs.get("http-cookie", http_cookie)
                            http_auth = attrs.get("http-authorization", http_auth)
                            http_accept = attrs.get("http-accept", http_accept)
                    continue

                if upper_prefix.startswith("#EXTVLCOPT"):
                    colon_idx = s.find(':')
                    if colon_idx != -1:
                        data = s[colon_idx + 1:]
                        eq_idx = data.find('=')
                        if eq_idx != -1:
                            key = data[:eq_idx].strip().lower()
                            value = data[eq_idx + 1:].strip()
                            if key in {"catchup-source", "catchup_url"}:
                                catchup_source = value
                            elif key == "catchup-days":
                                catchup_days = value
                            elif key == "catchup-type":
                                catchup_type = value
                            elif key == "http-user-agent":
                                http_user_agent = value
                            elif key in {"http-referrer", "http-referer", "referer", "referrer"}:
                                http_referrer = value
                            elif key in {"http-origin", "origin"}:
                                http_origin = value
                            elif key in {"http-cookie", "cookie"}:
                                http_cookie = value
                            elif key in {"http-authorization", "authorization", "auth"}:
                                http_auth = value
                            elif key in {"http-accept", "accept"}:
                                http_accept = value
                            elif key.startswith("http-header"):
                                if value:
                                    http_headers.append(value)
                    continue

                if upper_prefix.startswith("#KODIPROP"):
                    colon_idx = s.find(':')
                    if colon_idx != -1:
                        data = s[colon_idx + 1:]
                        eq_idx = data.find('=')
                        if eq_idx != -1:
                            key = data[:eq_idx].strip().lower()
                            value = data[eq_idx + 1:].strip()
                            if key.endswith("catchup_days"):
                                catchup_days = value
                            elif key.endswith("catchup_source"):
                                catchup_source = value
                            elif key in {"http-referrer", "http-referer", "referer", "referrer"}:
                                http_referrer = value
                            elif key in {"http-origin", "origin"}:
                                http_origin = value
                            elif key in {"http-cookie", "cookie"}:
                                http_cookie = value
                            elif key in {"http-authorization", "authorization", "auth"}:
                                http_auth = value
                            elif key in {"http-accept", "accept"}:
                                http_accept = value
                    continue

                # Other comment/directive lines are ignored
                continue

            url = s
            grp_value = group or extract_group_local(name)
            channel = {
                "name": name,
                "group": grp_value,
                "url": url,
                "tvg-id": tvg_id,
                "tvg-name": tvg_name,
            }
            if provider_id:
                channel["provider-id"] = provider_id
            if provider_type:
                channel["provider-type"] = provider_type
            if tvg_logo:
                channel["tvg-logo"] = tvg_logo
            if tvg_rec:
                channel["tvg-rec"] = tvg_rec
            if timeshift:
                channel["timeshift"] = timeshift
            if catchup:
                channel["catchup"] = catchup
            if catchup_type:
                channel["catchup-type"] = catchup_type
            if catchup_days:
                channel["catchup-days"] = catchup_days
            if catchup_source:
                channel["catchup-source"] = catchup_source
            if catchup_offset:
                channel["catchup-offset"] = catchup_offset
            if http_user_agent:
                channel["http-user-agent"] = http_user_agent
            if http_referrer:
                channel["http-referrer"] = http_referrer
            if http_origin:
                channel["http-origin"] = http_origin
            if http_cookie:
                channel["http-cookie"] = http_cookie
            if http_auth:
                channel["http-authorization"] = http_auth
            if http_accept:
                channel["http-accept"] = http_accept
            if http_headers:
                # Preserve header order but drop duplicates case-insensitively.
                seen_headers = set()
                unique_headers: List[str] = []
                for hdr in http_headers:
                    key_lower = hdr.split(":", 1)[0].strip().lower() if ":" in hdr else hdr.lower()
                    if key_lower in seen_headers:
                        continue
                    seen_headers.add(key_lower)
                    unique_headers.append(hdr)
                channel["http-headers"] = unique_headers

            if provider_type == "xtream" or catchup_source:
                stream_id = stream_id_for(url)
                if stream_id:
                    channel["stream-id"] = stream_id

            append(channel)

            # Clear state after emitting the channel entry
            name = ""
            group = ""
            tvg_id = ""
            tvg_name = ""
            tvg_logo = ""
            tvg_rec = ""
            timeshift = ""
            catchup = ""
            catchup_type = ""
            catchup_days = ""
            catchup_source = ""
            catchup_offset = ""
            http_user_agent = ""
            http_referrer = ""
            http_origin = ""
            http_cookie = ""
            http_headers = []
            http_auth = ""
            http_accept = ""

        return out

    def _playlist_text_hash(self, text: str) -> str:
        if not text:
            return ""
        return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()

    def _parsed_cache_path_for_key(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()
        cache_dir = get_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"parsed_{digest}.json")

    def _load_cached_playlist(
        self,
        cache_path: str,
        text_hash: Optional[str],
        provider_meta: Optional[Dict[str, str]] = None,
        skip_hash: bool = False,
    ) -> Optional[List[Dict[str, str]]]:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not skip_hash and text_hash is not None and data.get("hash") != text_hash:
                return None
            channels = data.get("channels")
            if not isinstance(channels, list):
                return None
            if provider_meta:
                pid = provider_meta.get("provider-id")
                ptype = provider_meta.get("provider-type")
                if pid or ptype:
                    for ch in channels:
                        if pid:
                            ch["provider-id"] = pid
                        if ptype:
                            ch["provider-type"] = ptype
            return channels
        except Exception:
            return None

    def _store_cached_playlist(
        self,
        cache_path: str,
        text_hash: str,
        channels: List[Dict[str, str]],
        provider_meta: Optional[Dict[str, str]] = None,
    ) -> None:
        payload = {"hash": text_hash, "channels": channels}
        if provider_meta:
            payload["provider"] = provider_meta
        tmp_path = cache_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp_path, cache_path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _extract_stream_id(self, url: str) -> str:
        try:
            path = urllib.parse.urlparse(url).path
        except Exception:
            path = ""
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ""
        last = parts[-1]
        m = re.match(r"(\d+)", last)
        if m:
            return m.group(1)
        if len(parts) >= 2:
            m = re.match(r"(\d+)", parts[-2])
            if m:
                return m.group(1)
        return ""

    def on_group_select(self):
        sel = self.group_list.GetSelection()
        label = self.group_list.GetString(sel) if sel != wx.NOT_FOUND else "All Channels"
        if label.startswith("All Channels"):
            grp = "All Channels"
        else:
            grp = label.split(" (", 1)[0]
        self.current_group = grp

        source = self.all_channels if grp == "All Channels" else self.channels_by_group.get(grp, [])
        self._populate_channel_list_chunked(source)

    def _populate_channel_list_chunked(self, source: List[Dict[str, str]]):
        self._populate_token += 1
        token = self._populate_token

        self.displayed = []
        self.channel_list.Freeze()
        try:
            self.channel_list.Clear()
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass

        total = len(source)
        if total == 0:
            self.epg_display.SetValue("")
            self.url_display.SetValue("")
            return

        # For small lists, append in one shot for speed
        if total <= 1500:
            names = []
            for ch in source:
                self.displayed.append({"type": "channel", "data": ch})
                names.append(ch.get("name", ""))
            self.channel_list.Freeze()
            try:
                if names:
                    self.channel_list.AppendItems(names)
            finally:
                try:
                    self.channel_list.Thaw()
                except Exception:
                    pass
            self.channel_list.SetSelection(0)
            self.channel_list.SetFocus()
            self.on_highlight()
            return

        # For large lists, add in batches to keep UI responsive
        batch = 800 if total > 8000 else 500
        idx = 0

        # Provide a minimal immediate preview
        preview_end = min(200, total)
        preview_names = []
        for ch in source[:preview_end]:
            self.displayed.append({"type": "channel", "data": ch})
            preview_names.append(ch.get("name", ""))
        self.channel_list.Freeze()
        try:
            if preview_names:
                self.channel_list.AppendItems(preview_names)
        finally:
            try:
                self.channel_list.Thaw()
            except Exception:
                pass
        self.channel_list.SetSelection(0)
        self.channel_list.SetFocus()
        self.on_highlight()
        idx = preview_end
        self.epg_display.SetValue(f"Loading channels… {idx}/{total}")

        def add_next_chunk():
            nonlocal idx
            if token != self._populate_token:
                return  # canceled due to group/filter change
            end = min(idx + batch, total)
            names = []
            for ch in source[idx:end]:
                self.displayed.append({"type": "channel", "data": ch})
                names.append(ch.get("name", ""))
            if names:
                self.channel_list.Freeze()
                try:
                    self.channel_list.AppendItems(names)
                finally:
                    try:
                        self.channel_list.Thaw()
                    except Exception:
                        pass
            idx = end
            if idx >= total or token != self._populate_token:
                if token == self._populate_token:
                    self.epg_display.SetValue("")
                return
            # update progress and schedule next chunk
            self.epg_display.SetValue(f"Loading channels… {idx}/{total}")
            wx.CallLater(30, add_next_chunk)  # 30ms between chunks keeps UI smooth

        wx.CallLater(30, add_next_chunk)

    def _fmt_time(self, s):
        # s: "YYYYMMDDHHMMSS" (UTC)
        try:
            dt = datetime.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            local = utc_to_local(dt)
            return local.strftime("%H:%M")
        except Exception:
            return "?"

    def _utc_now(self) -> datetime.datetime:
        try:
            return datetime.datetime.now(datetime.timezone.utc)
        except Exception:
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def _ensure_utc_dt(self, value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
        if not isinstance(value, datetime.datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)

    def _epg_cache_needs_refresh(self, now_show, next_show, cached_at: Optional[datetime.datetime]) -> bool:
        now_utc = self._utc_now()

        cached_utc = None
        if isinstance(cached_at, datetime.datetime):
            if cached_at.tzinfo is None:
                try:
                    # Assume legacy entries were stored as local time; best-effort convert to UTC.
                    cached_utc = cached_at.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    cached_utc = None
            else:
                cached_utc = cached_at.astimezone(datetime.timezone.utc)

        if cached_utc is None:
            return True

        if (now_utc - cached_utc).total_seconds() >= self._CACHE_REFRESH_AFTER_SECS:
            return True

        if not now_show and not next_show:
            # No guide yet; re-query soon so a subsequent provider import can populate it.
            if (now_utc - cached_utc).total_seconds() >= 30:
                return True

        if now_show:
            end_utc = self._ensure_utc_dt(now_show.get('end'))
            if end_utc and now_utc >= end_utc - datetime.timedelta(seconds=15):
                return True
        if not now_show and next_show:
            start_utc = self._ensure_utc_dt(next_show.get('start'))
            if start_utc and now_utc >= start_utc - datetime.timedelta(seconds=15):
                return True

        return False

    def on_highlight(self):
        # Allow viewing cached or currently available EPG even while an import is running.
        i = self.channel_list.GetSelection()
        if i < 0 or i >= len(self.displayed):
            self.url_display.SetValue("")
            return
        item = self.displayed[i]
        if item["type"] == "channel":
            ch = item["data"]
            self.url_display.SetValue(ch.get("url", ""))
            cname = ch.get("name", "")

            if not self.config.get("epg_enabled", True):
                self.epg_display.SetValue("EPG is disabled in configuration.")
                return

            self._start_epg_poll_timer()

            # If this channel is exempt (likely has no EPG), show a clear message and do not fetch.
            if self._channel_is_epg_exempt(ch):
                self.epg_display.SetValue("No EPG data for this channel.")
                return

            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                needs_refresh = self._epg_cache_needs_refresh(now_show, next_show, ts)
                if needs_refresh:
                    with self._epg_inflight_lock:
                        if key not in self._epg_fetch_inflight:
                            threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
                msg = self._epg_msg_from_tuple(now_show, next_show)
                if needs_refresh:
                    msg += "\n\nUpdating EPG..."
                # If an import is running, indicate that data may still be arriving.
                if self.epg_importing:
                    msg = msg + "\n\nNote: EPG import in progress — newer program data may still arrive."
                self.epg_display.SetValue(msg)
            else:
                # No cached entry: fetch what exists now (reader connection to DB).
                with self._epg_inflight_lock:
                    already = canonicalize_name(cname) in self._epg_fetch_inflight
                    if not already:
                        threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
                # Provide placeholder while we wait for DB read.
                placeholder = "Loading EPG for this channel…"
                if self.epg_importing:
                    placeholder += "\n\nEPG import in progress — displaying available data as it arrives."
                self.epg_display.SetValue(placeholder)
        elif item["type"] == "epg":
            self.url_display.SetValue("")
            r = item["data"]
            url = ""
            for ch in self.all_channels:
                if canonicalize_name(ch["name"]) == canonicalize_name(r["channel_name"]):
                    url = ch.get("url", "")
                    break
            msg = (
                f"Show: {r['show_title']} | Channel: {r['channel_name']} | "
                f"Start: {self._fmt_time(r['start'])} | End: {self._fmt_time(r['end'])}"
            )
            if self.epg_importing:
                msg = msg + "\n\nNote: EPG import in progress — data may still be updating."
            self.epg_display.SetValue(msg)
            self.url_display.SetValue(url)

    def _epg_msg_from_tuple(self, now, nxt):
        def localfmt(dt):
            local = utc_to_local(dt)
            return local.strftime('%H:%M')
        msg = ""
        if now:
            msg += f"Now: {now['title']} ({localfmt(now['start'])} – {localfmt(now['end'])})"
        elif nxt:
            msg += f"Starts at {localfmt(nxt['start'])}: {nxt['title']}"
        else:
            msg += "No program currently airing."
        if nxt:
            msg += f"\nNext: {nxt['title']} ({localfmt(nxt['start'])} – {localfmt(nxt['end'])})"
        return msg

    def _fetch_and_cache_epg(self, channel, cname):
        key = canonicalize_name(cname)
        with self._epg_inflight_lock:
            if key in self._epg_fetch_inflight:
                return
            self._epg_fetch_inflight.add(key)

        def _do_work():
            try:
                if self._channel_is_epg_exempt(channel):
                    return None, None
                
                db = EPGDatabase(get_db_path(), readonly=True)
                try:
                    # Check match cache first
                    cached_id = self._epg_match_cache.get(key)
                    if cached_id is None:
                        # Resolve and cache
                        cached_id = db.resolve_best_channel_id(channel)
                        # Cache even if None to avoid repeated expensive misses
                        self._epg_match_cache[key] = cached_id or ""
                    
                    # If we have a valid ID (and it's not the empty string marker for 'no match')
                    if cached_id:
                        return db.get_now_next_by_id(cached_id)
                    return None
                finally:
                    db.close()
            except Exception:
                return None

        def _on_done(future):
            try:
                now_next = future.result()
            except Exception:
                now_next = None
            
            with self._epg_inflight_lock:
                try:
                    self._epg_fetch_inflight.discard(key)
                except Exception:
                    pass

            if not now_next:
                now_show, next_show = None, None
            else:
                now_show, next_show = now_next

            with self.epg_cache_lock:
                self.epg_cache[key] = (now_show, next_show, self._utc_now())
            
            wx.CallAfter(self._update_epg_display_if_selected, channel, now_show, next_show)

        # Submit to executor instead of spawning raw thread
        self._epg_executor.submit(_do_work).add_done_callback(_on_done)

    def _update_epg_display_if_selected(self, channel, now_show, next_show):
        i = self.channel_list.GetSelection()
        if 0 <= i < len(self.displayed):
            item = self.displayed[i]
            if item["type"] == "channel" and canonicalize_name(item["data"].get("name", "")) == canonicalize_name(channel.get("name", "")):
                if self._channel_is_epg_exempt(channel) and not (now_show or next_show):
                    msg = "No EPG data for this channel."
                else:
                    msg = self._epg_msg_from_tuple(now_show, next_show)
                if self.epg_importing:
                    msg = msg + "\n\nNote: EPG import in progress — newer program data may still arrive."
                self.epg_display.SetValue(msg)

    def _start_epg_poll_timer(self):
        try:
            if self._epg_poll_timer:
                return
            self._epg_poll_timer = wx.Timer(self)
            # Bind with timer as source so we can unbind cleanly later
            self.Bind(wx.EVT_TIMER, self._on_epg_poll_timer, self._epg_poll_timer)
            # Poll less aggressively to avoid repeated expensive matching while importer churns.
            self._epg_poll_timer.Start(8000, wx.TIMER_CONTINUOUS)  # 8s
        except Exception:
            self._epg_poll_timer = None

    def _stop_epg_poll_timer(self):
        try:
            if self._epg_poll_timer:
                try:
                    self._epg_poll_timer.Stop()
                except Exception:
                    pass
                # Unbind the specific handler for this timer source to avoid removing other EVT_TIMER bindings.
                try:
                    # Unbind signature: Unbind(event, source=timer, handler=callable)
                    self.Unbind(wx.EVT_TIMER, handler=self._on_epg_poll_timer, source=self._epg_poll_timer)
                except Exception:
                    # Fallback: attempt to unbind by event only (best-effort)
                    try:
                        self.Unbind(wx.EVT_TIMER, handler=self._on_epg_poll_timer)
                    except Exception:
                        pass
                self._epg_poll_timer = None
        except Exception:
            self._epg_poll_timer = None

    def _on_epg_poll_timer(self, event):
        # Only refresh the currently highlighted channel (cheap, targeted).
        try:
            # Skip background polling when the window is hidden/minimised to avoid idle CPU use.
            if not self.IsShownOnScreen() or self.IsIconized():
                return
            i = self.channel_list.GetSelection()
            if i < 0 or i >= len(self.displayed):
                return
            item = self.displayed[i]
            if item["type"] != "channel":
                return
            ch = item["data"]
            # Skip channels that likely have no EPG to avoid repeated DB probes/log spam.
            if self._channel_is_epg_exempt(ch):
                return
            cname = ch.get("name", "")
            key = canonicalize_name(cname)
            with self.epg_cache_lock:
                cached = self.epg_cache.get(key)
            if cached:
                now_show, next_show, ts = cached
                if not self._epg_cache_needs_refresh(now_show, next_show, ts):
                    return
            else:
                now_show = next_show = ts = None
            # Only spawn a refresh if one isn't already running for this channel.
            with self._epg_inflight_lock:
                already = key in self._epg_fetch_inflight
            if not already:
                threading.Thread(target=self._fetch_and_cache_epg, args=(ch, cname), daemon=True).start()
        except Exception:
            pass

    def _find_channel_for_epg(self, show: Dict[str, str]) -> Optional[Dict[str, str]]:
        cname = show.get("channel_name", "")
        if not cname:
            return None
        canonical = canonicalize_name(cname)
        for ch in self.all_channels:
            if canonicalize_name(ch.get("name", "")) == canonical:
                return ch
        return None

    def _channel_has_catchup(self, channel: Dict[str, str]) -> bool:
        if channel.get("catchup-source") or channel.get("catchup"):
            return True
        if channel.get("provider-type") == "stalker":
            pdata = channel.get("provider-data") or {}
            return bool(pdata.get("allow_timeshift") or pdata.get("archive"))
        return False

    def _parse_epg_time(self, value: str) -> datetime.datetime:
        dt = datetime.datetime.strptime(value, "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=datetime.timezone.utc)

    def _resolve_show_url(self, channel: Dict[str, str], show: Dict[str, str]) -> tuple:
        start_dt = self._parse_epg_time(show.get("start"))
        end_dt = self._parse_epg_time(show.get("end"))
        now = datetime.datetime.now(datetime.timezone.utc)

        if start_dt <= now <= end_dt:
            return self._resolve_live_url(channel), False

        if end_dt < now:
            if not self._channel_has_catchup(channel):
                raise ProviderError("This channel does not provide catch-up streaming.")
            if not self._within_catchup_window(channel, start_dt):
                raise ProviderError("This programme is older than the catch-up window allows.")
            url = self._resolve_catchup_url(channel, start_dt, end_dt)
            if not url:
                raise ProviderError("Unable to construct catch-up URL for this programme.")
            return url, True

        # Future programme: return live stream so playback starts when available.
        return self._resolve_live_url(channel), False

    def _within_catchup_window(self, channel: Dict[str, str], start_dt: datetime.datetime) -> bool:
        days = channel.get("catchup-days")
        if not days:
            return True
        try:
            span = int(float(days))
        except (TypeError, ValueError):
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        return start_dt >= now - datetime.timedelta(days=span)

    def _resolve_live_url(self, channel: Dict[str, str]) -> str:
        url = channel.get("url", "")
        provider_type = channel.get("provider-type")
        provider_id = channel.get("provider-id")

        if provider_type == "stalker":
            if not provider_id:
                raise ProviderError("Stalker portal entry missing provider identifier.")
            client = self.provider_clients.get(provider_id)
            if not client:
                raise ProviderError("Stalker portal client is not initialized.")
            pdata = channel.get("provider-data") or {}
            url = client.resolve_stream(pdata)
            return url

        return url

    def _resolve_catchup_url(self, channel: Dict[str, str], start_dt: datetime.datetime, end_dt: datetime.datetime) -> str:
        provider_type = channel.get("provider-type")
        provider_id = channel.get("provider-id")
        if provider_type == "stalker" and provider_id:
            client = self.provider_clients.get(provider_id)
            if not client:
                raise ProviderError("Stalker portal client is not initialized.")
            pdata = channel.get("provider-data") or {}
            start_local = utc_to_local(start_dt)
            duration = max(1, int((end_dt - start_dt).total_seconds() // 60))
            start_str = start_local.strftime("%Y-%m-%d:%H-%M")
            return client.resolve_catchup(pdata, start_str, duration)

        return self._build_generic_catchup_url(channel, start_dt, end_dt)

    def _build_generic_catchup_url(self, channel: Dict[str, str], start_dt: datetime.datetime, end_dt: datetime.datetime) -> str:
        source = channel.get("catchup-source") or ""
        if not source:
            return ""
        stream_id = channel.get("stream-id") or self._extract_stream_id(channel.get("url", ""))
        if not stream_id:
            return ""

        src = source.rstrip('/')
        if not src:
            return ""
        last_segment = src.rsplit('/', 1)[-1]
        if not last_segment.isdigit():
            src = f"{src}/{stream_id}"

        start_local = utc_to_local(start_dt)
        end_local = utc_to_local(end_dt)
        offset = channel.get("catchup-offset")
        try:
            if offset:
                hours = float(offset)
                delta = datetime.timedelta(hours=hours)
                start_local -= delta
                end_local -= delta
        except (TypeError, ValueError):
            pass

        duration = max(1, int((end_local - start_local).total_seconds() // 60))
        start_token = start_local.strftime("%Y-%m-%d:%H-%M")
        ctype = (channel.get("catchup-type") or channel.get("catchup") or "xc").lower()
        if ctype in {"", "xc", "default", "catchup"}:
            url = f"{src}/{start_token}/{duration}/"
        elif ctype == "flussonic":
            archive_stamp = start_local.strftime("%Y%m%d%H%M%S")
            url = f"{src}/{archive_stamp}-{duration}.m3u8"
        else:
            url = f"{src}/{start_token}/{duration}/"

        ua = channel.get("http-user-agent")
        if ua:
            if "|" in url:
                url = f"{url}&User-Agent={urllib.parse.quote(ua)}"
            else:
                url = f"{url}|User-Agent={urllib.parse.quote(ua)}"
        return url

    def play_selected(self, *, show_internal_player: Optional[bool] = None):
        i = self.channel_list.GetSelection()
        if not (0 <= i < len(self.displayed)):
            return
        item = self.displayed[i]

        channel = None
        show = None
        if item["type"] == "channel":
            channel = item["data"]
        elif item["type"] == "epg":
            show = item["data"]
            channel = self._find_channel_for_epg(show)
            if not channel:
                wx.MessageBox("Could not match this programme to a playlist channel.",
                              "Not Found", wx.OK | wx.ICON_WARNING)
                return
        else:
            return

        try:
            if show:
                url, _ = self._resolve_show_url(channel, show)
            else:
                url = self._resolve_live_url(channel)
        except ProviderError as err:
            wx.MessageBox(f"Provider error: {err}", "Playback Error", wx.OK | wx.ICON_ERROR)
            return
        except Exception as err:
            wx.MessageBox(f"Could not resolve stream URL:\n{err}", "Playback Error", wx.OK | wx.ICON_ERROR)
            return

        display_name = None
        if channel:
            display_name = (channel.get("name")
                            or channel.get("tvg-name")
                            or channel.get("tvg_name")
                            or channel.get("tvg-id")
                            or channel.get("tvg_id"))
        stream_kind = "catchup" if show else "live"
        if show:
            show_title = show.get("show_title") or show.get("title")
            if show_title:
                if display_name:
                    display_name = f"{show_title} - {display_name}"
                else:
                    display_name = show_title
        if not display_name:
            display_name = "IPTV Stream"
        if show_internal_player is None:
            show_internal_player = self.show_player_on_enter

        self._launch_stream(
            url,
            display_name,
            stream_kind=stream_kind,
            channel=channel,
            show_internal_player=show_internal_player,
        )

    def _on_internal_player_closed(self) -> None:
        self._internal_player_frame = None

    def _ensure_internal_player(self) -> InternalPlayerFrame:
        if InternalPlayerFrame is None:
            detail = _VLC_IMPORT_ERROR or "Built-in player is unavailable."
            raise InternalPlayerUnavailableError(str(detail))
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            try:
                if getattr(frame, "_destroyed", False):
                    frame = None
            except Exception:
                frame = None
        if frame:
            return frame
        settings = resolve_internal_player_settings(self.config)
        frame = InternalPlayerFrame(
            self,
            base_buffer_seconds=settings.base_buffer_seconds,
            max_buffer_seconds=settings.max_buffer_seconds,
            variant_max_mbps=settings.variant_max_mbps,
            on_cast=self._cast_from_internal_player,
            on_close=self._on_internal_player_closed,
        )
        self._internal_player_frame = frame
        return frame

    def _launch_stream(
        self,
        url: str,
        title: Optional[str] = None,
        *,
        stream_kind: str = "live",
        channel: Optional[Dict[str, str]] = None,
        show_internal_player: Optional[bool] = None,
    ):
        if not url:
            wx.MessageBox("Could not find stream URL for this selection.", "Not Found",
                          wx.OK | wx.ICON_WARNING)
            return
        if show_internal_player is None:
            show_internal_player = self.show_player_on_enter

        # Check if casting
        if self.caster.is_connected():
            try:
                # Run async cast play in background thread
                def do_cast():
                    try:
                        self.caster.play(url, title or "IPTV Stream", channel=channel)
                    except Exception as e:
                        err_msg = str(e)
                        wx.CallAfter(lambda: wx.MessageBox(f"Casting error: {err_msg}", "Error", wx.OK | wx.ICON_ERROR))
                
                threading.Thread(target=do_cast, daemon=True).start()
                
                wx.MessageBox(f"Casting to {self.caster.active_device.display_name}...", "Casting", wx.OK | wx.ICON_INFORMATION)
                return
            except Exception as e:
                wx.MessageBox(f"Failed to cast: {e}", "Casting Error", wx.OK | wx.ICON_ERROR)
                # Fallback to local player? No, user expects cast.
                return

        player = self.default_player
        stream_headers = channel_http_headers(channel)
        custom_path = self.config.get("custom_player_path", "")

        if player in {"Built-in Player", "player_Internal", "internal", "Internal"}:
            player = "Built-in Player"
            try:
                frame = self._ensure_internal_player()
            except InternalPlayerUnavailableError as err:
                detail = str(err)
                wx.MessageBox(f"Built-in player unavailable:\n{detail}", "Launch Error", wx.OK | wx.ICON_ERROR)
                return
            display_title = title or "IPTV Stream"
            try:
                if show_internal_player:
                    frame.Enable(True)
                    frame.Show()
                    frame.Raise()
                    frame.SetFocus()
                else:
                    # Keep frame disabled and hidden to avoid accessibility focus.
                    frame.Enable(False)
                    frame.Hide()
                frame.play(
                    url,
                    display_title,
                    stream_kind=stream_kind,
                    headers=stream_headers,
                    video_visible=show_internal_player,
                )
                if not show_internal_player:
                    wx.CallAfter(self._restore_main_focus)
            except Exception as err:
                wx.MessageBox(f"Failed to start built-in player:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)
            return

        # External player launch
        ok, err = self.player_launcher.launch(player, url, custom_path)
        if not ok:
            wx.MessageBox(f"Failed to launch {player}:\n{err}", "Launch Error", wx.OK | wx.ICON_ERROR)

    def _restore_main_focus(self) -> None:
        try:
            if self.IsShown():
                self.Raise()
                self.SetFocus()
                self.channel_list.SetFocus()
        except Exception:
            pass

    def _cast_from_internal_player(self, url: str, title: str, headers: Dict[str, object]) -> None:
        if not url:
            wx.MessageBox("No active stream to cast.", "Casting", wx.OK | wx.ICON_WARNING)
            return

        def do_cast(device: CastDevice):
            try:
                creds = self.config.get("cast_credentials", {}).get(device.identifier)
                self.caster.connect(device, credentials=creds)
                # Use the active caster directly so we can forward headers from the current stream.
                if self.caster.active_caster:
                    self.caster.dispatch(self.caster.active_caster.play(url, title, headers=headers))
                else:
                    raise RuntimeError("Caster not connected.")
                wx.CallAfter(self._handoff_internal_player_after_cast, url, title)
                wx.CallAfter(lambda: wx.MessageBox(f"Casting to {device.display_name}...", "Casting", wx.OK | wx.ICON_INFORMATION))
            except Exception as e:
                wx.CallAfter(lambda: wx.MessageBox(f"Failed to cast: {e}", "Casting Error", wx.OK | wx.ICON_ERROR))

        if self.caster.is_connected() and self.caster.active_device:
            threading.Thread(target=lambda: do_cast(self.caster.active_device), daemon=True).start()
            return

        dlg = CastDiscoveryDialog(self, self.caster)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                device = dlg.get_selected_device()
                if device:
                    threading.Thread(target=lambda: do_cast(device), daemon=True).start()
        finally:
            dlg.Destroy()

    def _handoff_internal_player_after_cast(self, url=None, title=None, stream_kind=None, channel=None):
        # Stop and hide the built-in player, then (optionally) relaunch the stream
        # in the user's preferred external player. Guard all variables to avoid
        # NameErrors when called without arguments.
        frame = getattr(self, "_internal_player_frame", None)
        if frame:
            try:
                if url is None:
                    url = getattr(frame, "_current_url", None) or getattr(frame, "_last_resolved_url", None)
                if title is None:
                    title = getattr(frame, "_current_title", None)
                if stream_kind is None:
                    stream_kind = getattr(frame, "_current_stream_kind", None)
            except Exception:
                pass
            try:
                frame.stop(manual=True)
            except Exception:
                pass
            try:
                frame.Hide()
            except Exception:
                pass

        player = self.default_player
        
        # If the user prefers the built-in player, just stop/hide and return.
        if player in {"Built-in Player", "player_Internal", "internal", "Internal"}:
            return

        # Ensure we have sane defaults.
        if not title:
            title = "IPTV Stream"
        if not stream_kind:
            stream_kind = "live"
        # If we don't have a URL, there's nothing to hand off.
        if not url:
            return

        # Use generic launch method for external player
        self._launch_stream(url, title, stream_kind=stream_kind, channel=channel, show_internal_player=False)

    def _open_catchup_dialog(self, channel: Dict[str, str]):
        programmes = self._get_catchup_programmes(channel)
        if not programmes:
            wx.MessageBox("No catch-up programmes are available for this channel.",
                          "Catch-up", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = CatchupDialog(self, channel.get("name", ""), programmes)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                selected = dlg.get_selection()
                if not selected:
                    return
                show = {
                    "channel_id": selected.get("channel_id", ""),
                    "channel_name": channel.get("name", selected.get("channel_name", "")),
                    "show_title": selected.get("title", ""),
                    "start": selected.get("start", ""),
                    "end": selected.get("end", "")
                }
                try:
                    url, _ = self._resolve_show_url(channel, show)
                except ProviderError as err:
                    wx.MessageBox(f"Provider error: {err}", "Catch-up", wx.OK | wx.ICON_ERROR)
                    return
                except Exception as err:
                    wx.MessageBox(f"Unable to prepare catch-up stream:\n{err}", "Catch-up", wx.OK | wx.ICON_ERROR)
                    return
                display = (selected.get("title") or channel.get("name", "IPTV Stream"))
                self._launch_stream(url, display, stream_kind="catchup", channel=channel)
        finally:
            dlg.Destroy()

    def show_cast_dialog(self, _):
        if self.caster.is_connected():
            msg = f"Currently connected to: {self.caster.active_device.display_name}\n\nDisconnect?"
            if wx.MessageBox(msg, "Casting", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                # Disconnect in background
                threading.Thread(target=self.caster.disconnect, daemon=True).start()
            return

        dlg = CastDiscoveryDialog(self, self.caster)
        if dlg.ShowModal() == wx.ID_OK:
            device = dlg.get_selected_device()
            if device:
                # Connect in background
                def do_connect():
                    try:
                        creds = self.config.get("cast_credentials", {}).get(device.identifier)
                        self.caster.connect(device, credentials=creds)
                        wx.CallAfter(lambda: wx.MessageBox(f"Connected to {device.display_name}", "Connected", wx.OK))
                    except Exception as e:
                        err_msg = str(e)
                        wx.CallAfter(lambda: wx.MessageBox(f"Failed to connect: {err_msg}", "Error", wx.OK | wx.ICON_ERROR))
                
                threading.Thread(target=do_connect, daemon=True).start()
        dlg.Destroy()


    def _get_catchup_programmes(self, channel: Dict[str, str]) -> List[Dict[str, str]]:
        try:
            db = EPGDatabase(get_db_path(), readonly=True)
            try:
                programmes = db.get_recent_programmes(channel, hours=72, limit=80)
            finally:
                db.close()
        except Exception:
            programmes = []
        return programmes


class CastDiscoveryDialog(wx.Dialog):
    def __init__(self, parent, caster: CastingManager):
        super().__init__(parent, title="Select Device to Cast", size=(450, 350))
        self.parent_frame = parent
        self.caster = caster
        self.devices: List[CastDevice] = []
        
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.status_lbl = wx.StaticText(panel, label="Searching for devices...")
        self.listbox = wx.ListBox(panel, style=wx.LB_SINGLE)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.pair_btn = wx.Button(panel, label="Pair...")
        self.pair_btn.Disable()
        
        self.ok_btn = wx.Button(panel, id=wx.ID_OK, label="Connect")
        self.ok_btn.Disable()
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL)
        
        btn_sizer.Add(self.pair_btn, 0, wx.ALL, 5)
        btn_sizer.AddStretchSpacer(1)
        btn_sizer.Add(self.ok_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        
        sizer.Add(self.status_lbl, 0, wx.ALL, 10)
        sizer.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        panel.SetSizer(sizer)
        
        self.listbox.Bind(wx.EVT_LISTBOX, self._on_select)
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self._on_dclick)
        self.pair_btn.Bind(wx.EVT_BUTTON, self._on_pair)
        
        self.CenterOnParent()
        
        # Start discovery
        self._start_discovery()

    def _start_discovery(self):
        def do_scan():
            try:
                # caster.discover_all() is synchronous and thread-safe (uses internal loop)
                devices = self.caster.discover_all()
                wx.CallAfter(self._update_list, devices)
            except Exception as e:
                wx.CallAfter(self.status_lbl.SetLabel, f"Error: {e}")
        
        threading.Thread(target=do_scan, daemon=True).start()

    def _update_list(self, devices: List[CastDevice]):
        self.devices = devices
        self.listbox.Clear()
        if not devices:
            self.status_lbl.SetLabel("No devices found.")
            return
            
        self.status_lbl.SetLabel(f"Found {len(devices)} devices:")
        for dev in devices:
            self.listbox.Append(dev.display_name)
        
        # Restore selection if possible (not implemented for now to keep it simple)

    def _on_select(self, event):
        sel = self.listbox.GetSelection()
        if sel != wx.NOT_FOUND:
            self.ok_btn.Enable()
            dev = self.devices[sel]
            # Enable Pair button for AirPlay devices
            self.pair_btn.Enable(dev.protocol.value == "AirPlay")
        else:
            self.ok_btn.Disable()
            self.pair_btn.Disable()

    def _on_dclick(self, event):
        if self.listbox.GetSelection() != wx.NOT_FOUND:
            self.EndModal(wx.ID_OK)

    def _on_pair(self, event):
        device = self.get_selected_device()
        if not device:
            return
        
        # Disable UI
        self.pair_btn.Disable()
        self.ok_btn.Disable()
        self.status_lbl.SetLabel(f"Starting pairing with {device.name}...")
        
        def do_pair_flow():
            handler = None
            try:
                # Step 1: Begin Pairing
                handler = self.caster.start_pairing(device) # This is sync now
                self.caster.dispatch(handler.begin())
                
                # Step 2: Ask User for PIN
                def ask_pin():
                    dlg = wx.TextEntryDialog(self, f"Enter PIN displayed on {device.name}:", "Pairing")
                    if dlg.ShowModal() == wx.ID_OK:
                        return dlg.GetValue().strip()
                    return None
                
                # We need to run the dialog on main thread
                pin_result = [None]
                evt = threading.Event()
                def show_dialog_main():
                    pin_result[0] = ask_pin()
                    evt.set()
                
                wx.CallAfter(show_dialog_main)
                evt.wait()
                
                pin = pin_result[0]
                if not pin:
                    # User cancelled
                    self.caster.dispatch(handler.close())
                    wx.CallAfter(self.status_lbl.SetLabel, "Pairing cancelled.")
                    return

                # Step 3: Submit PIN
                handler.pin(pin)
                
                # Step 4: Finish
                self.caster.dispatch(handler.finish())
                
                # Step 5: Save Credentials
                creds = handler.service.credentials
                if creds:
                    wx.CallAfter(self._save_creds_and_notify, device, creds)
                else:
                    wx.CallAfter(lambda: wx.MessageBox("Pairing finished but no credentials returned.", "Pairing Failed", wx.OK | wx.ICON_ERROR))
                    
            except Exception as e:
                err_msg = str(e)
                wx.CallAfter(lambda: wx.MessageBox(f"Pairing error: {err_msg}", "Error", wx.OK | wx.ICON_ERROR))
                wx.CallAfter(self.status_lbl.SetLabel, f"Pairing failed: {err_msg}")
                if handler:
                    try:
                        self.caster.dispatch(handler.close())
                    except Exception:
                        pass
            finally:
                wx.CallAfter(self._on_select, None) # Re-enable buttons
        
        threading.Thread(target=do_pair_flow, daemon=True).start()

    def _save_creds_and_notify(self, device, creds):
        # Save to main config
        cfg = self.parent_frame.config
        if "cast_credentials" not in cfg:
            cfg["cast_credentials"] = {}
        
        cfg["cast_credentials"][device.identifier] = creds
        save_config(cfg)
        
        wx.MessageBox(f"Successfully paired with {device.name}!", "Pairing Complete", wx.OK)
        self.status_lbl.SetLabel(f"Paired with {device.name}. Ready to connect.")

    def get_selected_device(self) -> Optional[CastDevice]:
        sel = self.listbox.GetSelection()
        if sel != wx.NOT_FOUND and 0 <= sel < len(self.devices):
            return self.devices[sel]
        return None


class CatchupDialog(wx.Dialog):
    def __init__(self, parent, channel_name: str, programmes: List[Dict[str, str]]):
        title = channel_name or "Catch-up"
        super().__init__(parent, title=f"Catch-up: {title}", size=(520, 360))
        self.programmes = programmes
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(panel, label="Select a programme to play from catch-up:")
        self.listbox = wx.ListBox(panel, style=wx.LB_SINGLE)
        for prog in programmes:
            self.listbox.Append(self._format_programme(prog))
        if programmes:
            self.listbox.SetSelection(0)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, id=wx.ID_OK, label="Play")
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL)
        btn_sizer.Add(ok_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(intro, 0, wx.ALL, 10)
        sizer.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self._on_listbox_activate)
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.SetMinSize((420, 320))
        self.Layout()
        self.CenterOnParent()

    def _format_programme(self, prog: Dict[str, str]) -> str:
        try:
            start = datetime.datetime.strptime(prog.get("start", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            end = datetime.datetime.strptime(prog.get("end", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
            start_local = utc_to_local(start)
            end_local = utc_to_local(end)
            window = f"{start_local.strftime('%Y-%m-%d %H:%M')} – {end_local.strftime('%H:%M')}"
        except Exception:
            window = prog.get("start", "")
        title = prog.get("title", "") or "(No title)"
        return f"{window}  |  {title}"

    def _on_listbox_activate(self, _):
        if self.programmes:
            self.EndModal(wx.ID_OK)

    def _on_ok(self, event):
        if self.listbox.GetSelection() == wx.NOT_FOUND and self.programmes:
            self.listbox.SetSelection(0)
        if self.listbox.GetSelection() == wx.NOT_FOUND:
            return
        self.EndModal(wx.ID_OK)

    def get_selection(self) -> Optional[Dict[str, str]]:
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.programmes):
            return None
        return self.programmes[idx]


class ChannelEPGDialog(wx.Dialog):
    def __init__(self, parent, channel_name: str, programmes: List[Dict[str, str]]):
        super().__init__(parent, title=f"EPG: {channel_name}", size=(600, 450))
        
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.list_ctrl = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "Time", width=140)
        self.list_ctrl.InsertColumn(1, "Title", width=400)
        
        self._populate_list(programmes)
        
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="Close")
        
        sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        panel.SetSizer(sizer)
        
        self.Layout()
        self.CenterOnParent()

    def _populate_list(self, programmes):
        for prog in programmes:
            try:
                start = datetime.datetime.strptime(prog.get("start", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
                end = datetime.datetime.strptime(prog.get("end", ""), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
                start_local = utc_to_local(start)
                end_local = utc_to_local(end)
                time_str = f"{start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}"
                
                # Check if this program is currently airing
                now = datetime.datetime.now(datetime.timezone.utc)
                is_now = start <= now <= end
                
                idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), time_str)
                self.list_ctrl.SetItem(idx, 1, prog.get("title", ""))
                
                if is_now:
                    # Highlight current program (bold font)
                    font = self.list_ctrl.GetItemFont(idx)
                    font.SetWeight(wx.FONTWEIGHT_BOLD)
                    self.list_ctrl.SetItemFont(idx, font)
                    self.list_ctrl.EnsureVisible(idx)
            except Exception:
                pass

if __name__ == "__main__":
    set_linux_env()
    app = wx.App()
    app.SetAppName("IPTVClient")
    IPTVClient()
    app.MainLoop()

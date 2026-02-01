
import logging
import os
import platform
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

import wx

def _prime_vlc_search_path() -> None:
    """Make sure libvlc.dll is discoverable before importing python-vlc."""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "VideoLAN", "VLC"),
        os.path.join(os.environ.get("ProgramFiles", ""), "VideoLAN", "VLC"),
    ]
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        dll_path = os.path.join(path, "libvlc.dll")
        if os.path.isfile(dll_path):
            try:
                os.add_dll_directory(path)  # type: ignore[attr-defined]
            except Exception:
                # Fallback for older Python: prepend to PATH so ctypes finds it.
                os.environ["PATH"] = f"{path};" + os.environ.get("PATH", "")


_prime_vlc_search_path()

try:
    import vlc  # type: ignore
except Exception as _err:  # pragma: no cover - import guard
    vlc = None  # type: ignore
    _VLC_IMPORT_ERROR = _err
else:
    _VLC_IMPORT_ERROR = None

LOG = logging.getLogger(__name__)

_HLS_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


class InternalPlayerUnavailableError(RuntimeError):
    """Raised when the built-in player cannot be created."""


_VLC_RUNTIME_PREPARED = False


def _prepare_vlc_runtime() -> None:
    """Ensure python-vlc is ready. Minimal guard that surfaces import issues."""
    global _VLC_RUNTIME_PREPARED
    if _VLC_RUNTIME_PREPARED:
        return
    if vlc is None:
        detail = _VLC_IMPORT_ERROR or "python-vlc (libVLC) is not installed."
        raise InternalPlayerUnavailableError(str(detail))
    _VLC_RUNTIME_PREPARED = True


def _detect_system_http_proxy() -> Optional[str]:
    """Return system HTTP(S) proxy if configured (Windows honours IE settings)."""
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return None
    for key in ("http", "https"):
        proxy = proxies.get(key)
        if proxy:
            return proxy
    return None


class InternalPlayerFrame(wx.Frame):
    """Embedded IPTV player with buffering resilience and keyboard controls."""

    _HANDLE_CHECK_INTERVAL = 0.1

    def __init__(
        self,
        parent: Optional[wx.Window],
        base_buffer_seconds: float = 0.0,
        max_buffer_seconds: Optional[float] = None,
        variant_max_mbps: Optional[float] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_cast: Optional[Callable[[str, str, Dict[str, object]], None]] = None,
    ) -> None:
        _prepare_vlc_runtime()
        if vlc is None:
            raise InternalPlayerUnavailableError("python-vlc (libVLC) is not available.")
        super().__init__(parent, title="Built-in IPTV Player", size=(960, 540))
        self._on_close_cb = on_close
        self._on_cast_cb = on_cast
        self._allow_close = False
        base_value = self._coerce_seconds(base_buffer_seconds, fallback=0.0)
        self._last_bitrate_mbps: Optional[float] = None
        self._current_url: Optional[str] = None
        self._current_title: str = ""
        self._current_stream_kind: str = "live"
        self._last_resolved_url: Optional[str] = None
        self._current_headers: Dict[str, object] = {}
        self._auto_handle_bound = False
        self._have_handle = False
        self._is_paused = False
        self._destroyed = False
        self._handle_guard = threading.Lock()
        self._fullscreen = False
        self._volume_value = 80
        self._volume_last_ts = 0.0
        self._volume_ramp = 0
        self._volume_last_dir = 0
        self._manual_stop = False
        self._gave_up = False
        self._pending_restart = False
        self._pending_xtream_refresh = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 4
        self._last_restart_ts = 0.0
        self._last_restart_reason = ""
        self._buffer_step_seconds = 0.0
        self._max_buffer_seconds = self._resolve_max_buffer(max_buffer_seconds, base_value)
        self.base_buffer_seconds = max(0.0, min(base_value, self._max_buffer_seconds))
        self._network_cache_fraction = 0.0
        self._min_network_cache_seconds = 0.0
        self._max_network_cache_seconds = 0.0
        self._ts_network_bias = 0.0
        self._xtream_buffer_refresh_seconds = 0.0
        self._detected_content_ts = False  # True if stream detected as TS via Content-Type
        self._refresh_ts_floor()
        self._update_cache_bounds()
        self._last_buffer_seconds: float = self.base_buffer_seconds
        self._variant_max_mbps: Optional[float] = self._sanitize_variant_cap(variant_max_mbps)
        self._last_variant_url: Optional[str] = None
        self._buffering_events: Deque[float] = deque(maxlen=20)
        self._choppy_window_seconds = 0.0
        self._choppy_threshold = 4
        self._choppy_cooldown = 20.0
        self._last_adjust_ts = 0.0
        self._last_state_name: Optional[str] = None
        self._last_position_ms: Optional[int] = None
        self._stall_ticks = 0
        self._stall_threshold = 8
        self._play_start_monotonic = 0.0
        self._restart_cooldown = 2.0
        self._reconnect_reset_window = 120.0
        self._end_near_threshold_ms = 10_000
        # Sensible defaults for servers that expect browser-like headers.
        self._default_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 Safari/537.36"
        )
        self._default_accept = (
            "application/x-mpegURL,application/vnd.apple.mpegurl,"
            "application/json,text/plain,*/*"
        )
        self._buffer_start_ts: Optional[float] = None
        self._early_buffer_fix_applied = False
        self._has_seen_playing = False
        self._min_buffer_event_seconds = 1.25

        # Volume throttling state
        self._last_status_prefix = "Idle"

        instance_opts = [
            "--quiet",
            "--no-video-title-show",
            "--intf=dummy",
            "--clock-synchro=0",
            "--no-drop-late-frames",
            "--no-skip-frames",
        ]
        try:
            self.instance = vlc.Instance(instance_opts)
        except Exception as err:
            LOG.warning("libVLC rejected tuning flags (%s); retrying with defaults.", err)
            self.instance = vlc.Instance()
        if not self.instance:
            raise InternalPlayerUnavailableError("Failed to initialise libVLC instance.")
        try:
            self.player = self.instance.media_player_new()
        except Exception as err:
            self.instance.release()
            raise InternalPlayerUnavailableError(f"Failed to initialise media player: {err}") from err
        if not self.player:
            self.instance.release()
            raise InternalPlayerUnavailableError("Could not create libVLC media player object.")

        try:
            current = self.player.audio_get_volume()
            if current >= 0:
                self._volume_value = current
        except Exception:
            pass

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.BLACK)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.video_panel = wx.Panel(panel)
        self.video_panel.SetBackgroundColour(wx.BLACK)
        self.video_panel.Bind(wx.EVT_SIZE, self._on_video_panel_resize)
        self.video_panel.Bind(wx.EVT_WINDOW_DESTROY, self._on_video_panel_destroy)
        self.video_panel.Bind(wx.EVT_LEFT_DOWN, self._on_video_panel_click)
        self.video_panel.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
        self.video_panel.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        self.video_panel.Bind(wx.EVT_MOUSEWHEEL, self._on_mouse_wheel)
        main_sizer.Add(self.video_panel, 1, wx.EXPAND | wx.ALL, 0)

        self.controls_panel = wx.Panel(panel, style=wx.TAB_TRAVERSAL)
        self.controls_panel.SetBackgroundColour(wx.BLACK)
        self.controls_panel.Bind(wx.EVT_MOUSEWHEEL, self._on_mouse_wheel)
        controls = wx.BoxSizer(wx.HORIZONTAL)

        self.play_pause_btn = wx.Button(self.controls_panel, label="Pause")
        self.play_pause_btn.SetName("Play or Pause")
        self.play_pause_btn.Bind(wx.EVT_BUTTON, self._on_toggle_pause)
        self.play_pause_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
        self.play_pause_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

        self.stop_btn = wx.Button(self.controls_panel, label="Stop")
        self.stop_btn.SetName("Stop Playback")
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda evt: self.stop(evt, manual=True))
        self.stop_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
        self.stop_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

        self.cast_btn = wx.Button(self.controls_panel, label="Cast")
        self.cast_btn.SetName("Cast to Device")
        self.cast_btn.Bind(wx.EVT_BUTTON, self._on_cast)
        self.cast_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
        self.cast_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

        self.fullscreen_btn = wx.Button(self.controls_panel, label="Full Screen")
        self.fullscreen_btn.SetName("Toggle Full Screen")
        self.fullscreen_btn.Bind(wx.EVT_BUTTON, self._on_toggle_fullscreen)
        self.fullscreen_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
        self.fullscreen_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

        self.volume_slider = wx.Slider(
            self.controls_panel,
            value=self._volume_value,
            minValue=0,
            maxValue=100,
            style=wx.SL_HORIZONTAL,
        )
        self.volume_slider.SetName("Volume Control")
        self.volume_slider.Bind(wx.EVT_SLIDER, self._on_volume_slider)

        controls.Add(self.play_pause_btn, 0, wx.ALL, 5)
        controls.Add(self.stop_btn, 0, wx.ALL, 5)
        controls.Add(self.cast_btn, 0, wx.ALL, 5)
        controls.Add(self.fullscreen_btn, 0, wx.ALL, 5)
        # Expand horizontally; avoid mixing ALIGN_* with EXPAND to prevent wx assertions.
        controls.Add(self.volume_slider, 1, wx.ALL | wx.EXPAND, 5)
        controls.AddStretchSpacer(1)
        self.status_label = wx.StaticText(self.controls_panel, label="Idle")
        controls.Add(self.status_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.controls_panel.SetSizer(controls)
        self.controls_panel.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        main_sizer.Add(self.controls_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        panel.SetSizer(main_sizer)
        self.SetMinSize((480, 320))
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

        self._controls_focus_order = [
            self.play_pause_btn,
            self.stop_btn,
            self.cast_btn,
            self.fullscreen_btn,
            self.volume_slider,
        ]

        self._build_menu_bar()

        self._status_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._status_timer)

        wx.CallAfter(self._ensure_player_window)
        wx.CallAfter(self.play_pause_btn.SetFocus)
        self._update_status_label("Idle")

    def _build_menu_bar(self) -> None:
        menu_bar = wx.MenuBar()

        playback_menu = wx.Menu()
        m_play_pause = playback_menu.Append(wx.ID_ANY, "Play/Pause\tCtrl+P")
        m_stop = playback_menu.Append(wx.ID_ANY, "Stop\tCtrl+S")
        playback_menu.AppendSeparator()
        m_cast = playback_menu.Append(wx.ID_ANY, "Cast...\tCtrl+C")
        m_full = playback_menu.Append(wx.ID_ANY, "Toggle Full Screen\tF11")
        playback_menu.AppendSeparator()
        m_hide = playback_menu.Append(wx.ID_ANY, "Hide Window\tCtrl+W")
        m_exit = playback_menu.Append(wx.ID_EXIT, "Exit Player\tCtrl+Q")

        self.Bind(wx.EVT_MENU, lambda _evt: self._on_toggle_pause(), m_play_pause)
        self.Bind(wx.EVT_MENU, lambda _evt: self.stop(manual=True), m_stop)
        self.Bind(wx.EVT_MENU, lambda _evt: self._on_cast(), m_cast)
        self.Bind(wx.EVT_MENU, lambda _evt: self._on_toggle_fullscreen(), m_full)
        self.Bind(wx.EVT_MENU, lambda _evt: self._hide_player(), m_hide)
        self.Bind(wx.EVT_MENU, lambda _evt: self._exit_player(), m_exit)

        menu_bar.Append(playback_menu, "&Playback")
        self.SetMenuBar(menu_bar)

    # ------------------------------------------------------------------ public
    def play(
        self,
        url: str,
        title: Optional[str] = None,
        *,
        stream_kind: Optional[str] = None,
        headers: Optional[Dict[str, object]] = None,
        _retry: bool = False,
        video_visible: bool = True,
    ) -> None:
        """Start playback of the given URL with buffering and recovery hooks."""
        if self._destroyed:
            raise InternalPlayerUnavailableError("Player window has been destroyed.")
        if not url:
            raise InternalPlayerUnavailableError("No stream URL provided.")

        if _retry:
            self._manual_stop = False
            if stream_kind is None:
                stream_kind = self._current_stream_kind
        else:
            self._reconnect_attempts = 0
            self._last_restart_reason = ""
            self._gave_up = False
        if stream_kind is None:
            stream_kind = "live"
        self._current_stream_kind = stream_kind

        header_payload = headers if headers is not None else (self._current_headers if _retry else None)
        base_url, merged_headers = self._normalise_stream_url(url, header_payload)
        self._current_headers = merged_headers
        self._current_url = base_url
        self._current_title = title or "IPTV Stream"
        self._play_start_monotonic = time.monotonic()
        self._pending_restart = False
        self._buffering_events.clear()
        self._last_state_name = None
        self._last_position_ms = None
        self._stall_ticks = 0
        self._buffer_start_ts = None
        self._early_buffer_fix_applied = False
        self._has_seen_playing = False
        self._detected_content_ts = False  # Reset content type detection

        LOG.info("Playing URL: %s (title=%s, retry=%s)", base_url, title, _retry)
        self.SetTitle(f"{self._current_title} - Built-in Player")
        playback_url, variant_bitrate = self._resolve_stream_url(base_url, headers=merged_headers)
        self._last_resolved_url = playback_url
        LOG.debug("Resolved playback URL: %s (variant_bitrate=%s)", playback_url, variant_bitrate)
        
        # Quick preflight check to catch obvious HTTP errors before VLC tries
        if not _retry:
            LOG.debug("Running preflight check...")
            preflight_error = self._preflight_check(playback_url, headers=merged_headers)
            if preflight_error:
                LOG.warning("Preflight check failed: %s", preflight_error)
                self._update_status_label("Connection failed")
                wx.CallAfter(
                    wx.MessageBox,
                    preflight_error,
                    "Stream Unavailable",
                    wx.OK | wx.ICON_WARNING,
                )
                return
            LOG.debug("Preflight check passed")
            # Detect content type to identify TS streams that don't have .ts extension
            self._detect_stream_content_type(playback_url, headers=merged_headers)
        
        target_buffer, cache_profile, bitrate = self._compute_buffer_profile(
            playback_url, bitrate_hint=variant_bitrate, headers=merged_headers
        )
        LOG.debug("Buffer profile: target=%.1fs, bitrate=%s Mbps", target_buffer, bitrate)
        self._last_buffer_seconds = target_buffer
        self._last_bitrate_mbps = bitrate

        media = self.instance.media_new(playback_url)
        self._apply_cache_options(media, cache_profile)
        self._apply_stream_headers(media, merged_headers)
        if not video_visible:
            media.add_option(":no-video")
            media.add_option(":vout=dummy")
            media.add_option(":intf=dummy")
        try:
            self.player.stop()
        except Exception:
            pass
        self.player.set_media(media)
        if video_visible:
            self._ensure_player_window()
        else:
            # Prevent libVLC from auto-spawning a video window
            try:
                self.player.set_nsobject(None)  # macOS
            except Exception:
                pass
            try:
                self.player.set_hwnd(0)  # Windows
            except Exception:
                pass
            try:
                self.player.set_xwindow(0)  # Linux
            except Exception:
                pass
        LOG.debug("Calling player.play()...")
        self.player.play()
        self._is_paused = False
        self.play_pause_btn.SetLabel("Pause")
        self._schedule_volume_apply()
        self._status_timer.Start(500)
        self._update_status_label("Buffering...")
        LOG.debug("Playback initiated, timer started")
        
        # Ensure focus returns to the controls for screen readers
        if video_visible:
            wx.CallAfter(self.play_pause_btn.SetFocus)

    def stop(self, _evt: Optional[wx.Event] = None, manual: bool = False) -> None:
        manual_stop = manual or (_evt is not None)
        if manual_stop:
            self._manual_stop = True
            self._reconnect_attempts = 0
            self._last_restart_reason = ""
            self._gave_up = False
        self._pending_restart = False
        self._status_timer.Stop()
        try:
            self.player.stop()
        except Exception:
            pass
        self._is_paused = True
        self._has_seen_playing = False
        self.play_pause_btn.SetLabel("Play")
        self._update_status_label("Stopped")

    def update_base_buffer(self, seconds: float) -> None:
        """Update default buffer target for future plays."""
        try:
            seconds = float(seconds)
        except Exception:
            return
        self.base_buffer_seconds = min(seconds, self._max_buffer_seconds)
        self._refresh_ts_floor()
        self._update_cache_bounds()

    # ---------------------------------------------------------------- internal
    def _apply_cache_options(self, media: "vlc.Media", profile: dict) -> None:
        media.add_option(":http-reconnect=true")
        media.add_option(":rtsp-tcp")
        media.add_option(":clock-jitter=0")
        media.add_option(":clock-synchro=0")
        media.add_option(":drop-late-frames=0")
        media.add_option(":skip-frames=0")

        proxy_url = _detect_system_http_proxy()
        if proxy_url:
            media.add_option(f":http-proxy={proxy_url}")

        media.add_option(f":network-caching={profile['network_ms']}")
        media.add_option(f":live-caching={profile['live_ms']}")
        media.add_option(f":file-caching={profile['file_ms']}")
        media.add_option(f":disc-caching={profile['disc_ms']}")

        if profile.get("demux_read_ahead"):
            media.add_option(f":demux-read-ahead={profile['demux_read_ahead']}")
        if profile.get("adaptive_cache"):
            media.add_option(":adaptive-use-access=1")

    # ---------------------------------------------------------------- helpers
    def _normalise_stream_url(
        self, url: str, header_overrides: Optional[Dict[str, object]]
    ) -> Tuple[str, Dict[str, object]]:
        base, parsed_headers = self._parse_stream_modifiers(url)
        merged: Dict[str, object] = {}
        if parsed_headers:
            merged.update(parsed_headers)
        if header_overrides:
            for key, value in header_overrides.items():
                if key == "_extra":
                    if value:
                        merged.setdefault("_extra", [])
                        merged["_extra"] += [v for v in value if v]
                elif value:
                    merged[key.lower()] = value
        merged["_extra"] = self._dedupe_headers_list(merged.get("_extra"))
        return base, merged

    def _parse_stream_modifiers(self, url: str) -> Tuple[str, Dict[str, object]]:
        if not url:
            return "", {}
        base, sep, tail = url.partition("|")
        headers: Dict[str, object] = {}
        extras: List[str] = []
        if sep:
            for part in tail.split("|"):
                token = part.strip()
                if not token or "=" not in token:
                    continue
                key, value = token.split("=", 1)
                key = key.strip().lower()
                value = urllib.parse.unquote_plus(value.strip())
                if not value:
                    continue
                if key in ("user-agent", "ua", "http-user-agent"):
                    headers["user-agent"] = value
                elif key in ("referer", "referrer", "http-referrer", "http-referer"):
                    headers["referer"] = value
                elif key in ("origin", "http-origin"):
                    headers["origin"] = value
                elif key in ("cookie", "http-cookie"):
                    headers["cookie"] = value
                elif key in ("authorization", "auth", "http-authorization"):
                    headers["authorization"] = value
                elif key in ("bearer", "token"):
                    headers["authorization"] = f"Bearer {value}"
                elif key in ("x-forwarded-for", "xff"):
                    headers["x-forwarded-for"] = value
                elif key in ("accept", "http-accept"):
                    headers["accept"] = value
                elif key in ("range", "http-range"):
                    headers["range"] = value
                elif key in ("host", "http-host"):
                    extras.append(f"Host: {value}")
                else:
                    extras.append(f"{self._normalize_header_name(key)}: {value}")
        if extras:
            headers["_extra"] = extras
        return base.strip(), headers

    @staticmethod
    def _normalize_header_name(key: str) -> str:
        return "-".join(part.capitalize() for part in key.split("-") if part)

    @staticmethod
    def _dedupe_headers_list(headers: Optional[List[str]]) -> List[str]:
        if not headers:
            return []
        seen = set()
        deduped: List[str] = []
        for hdr in headers:
            if not hdr:
                continue
            prefix = hdr.split(":", 1)[0].strip().lower() if ":" in hdr else hdr.lower()
            if prefix in seen:
                continue
            seen.add(prefix)
            deduped.append(hdr)
        return deduped

    def _apply_stream_headers(self, media: "vlc.Media", headers: Dict[str, object]) -> None:
        # Always send browser-like defaults to reduce CDN disconnects.
        ua = (headers or {}).get("user-agent") or self._default_user_agent
        media.add_option(f":http-user-agent={ua}")

        ref = headers.get("referer") if headers else None
        if ref:
            media.add_option(f":http-referrer={ref}")
        cookie = headers.get("cookie") if headers else None
        if cookie:
            media.add_option(f":http-cookie={cookie}")
        origin = headers.get("origin") if headers else None
        if origin:
            media.add_option(f":http-header=Origin: {origin}")
        auth = headers.get("authorization") if headers else None
        if auth:
            media.add_option(f":http-header=Authorization: {auth}")
        accept = (headers or {}).get("accept") or self._default_accept
        if accept:
            media.add_option(f":http-header=Accept: {accept}")
        xff = headers.get("x-forwarded-for") if headers else None
        if xff:
            media.add_option(f":http-header=X-Forwarded-For: {xff}")
        range_header = headers.get("range") if headers else None
        if range_header:
            media.add_option(f":http-header=Range: {range_header}")
        extras = headers.get("_extra") if headers else None
        if isinstance(extras, list):
            for hdr in extras:
                media.add_option(f":http-header={hdr}")

    def _request_headers(self, headers: Optional[Dict[str, object]], *, include_accept: bool = True) -> dict:
        req_headers: Dict[str, str] = {}
        ua = (headers or {}).get("user-agent") or self._default_user_agent
        req_headers["User-Agent"] = str(ua)
        if include_accept:
            req_headers["Accept"] = str((headers or {}).get("accept") or self._default_accept)
        if headers:
            ref = headers.get("referer")
            if ref:
                req_headers["Referer"] = str(ref)
            origin = headers.get("origin")
            if origin:
                req_headers["Origin"] = str(origin)
            cookie = headers.get("cookie")
            if cookie:
                req_headers["Cookie"] = str(cookie)
            auth = headers.get("authorization")
            if auth:
                req_headers["Authorization"] = str(auth)
            extras = headers.get("_extra")
            if isinstance(extras, list):
                for hdr in extras:
                    if ":" not in str(hdr):
                        continue
                    name, val = str(hdr).split(":", 1)
                    name = name.strip()
                    val = val.strip()
                    if name and val and name not in req_headers:
                        req_headers[name] = val
        return req_headers

    @staticmethod
    def _coerce_seconds(value: Optional[float], *, fallback: float) -> float:
        try:
            coerced = float(value) if value is not None else float(fallback)
        except Exception:
            coerced = float(fallback)
        return max(0.0, coerced)

    def _resolve_max_buffer(self, supplied: Optional[float], base_value: float) -> float:
        raw_max = self._coerce_seconds(supplied if supplied is not None else 30.0, fallback=30.0)
        if raw_max < 0.0:
            raw_max = 0.0
        resolved = raw_max
        if resolved < base_value:
            return base_value
        return resolved

    def _update_cache_bounds(self) -> None:
        self._max_network_cache_seconds = self._max_buffer_seconds
        self._min_network_cache_seconds = self.base_buffer_seconds

    def _refresh_ts_floor(self) -> None:
        self._ts_buffer_floor = min(self._max_buffer_seconds, self.base_buffer_seconds)

    @staticmethod
    def _sanitize_variant_cap(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        try:
            cap = float(value)
        except Exception:
            return None
        if cap <= 0:
            return None
        return max(0.25, cap)

    def update_variant_cap(self, max_mbps: Optional[float]) -> None:
        self._variant_max_mbps = self._sanitize_variant_cap(max_mbps)

    @staticmethod
    def _is_linear_ts(url: str) -> bool:
        if not url:
            return False
        lower = url.lower()
        if ".ts?" in lower or lower.endswith(".ts"):
            return True
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower() if parsed.path else ""
        if path.endswith(".ts"):
            return True
        return False

    @staticmethod
    def _strip_stream_modifiers(url: str) -> str:
        if not url:
            return ""
        base, _, _ = url.partition("|")
        return base.strip()

    def _looks_like_xtream_live_ts(self) -> bool:
        """Check if current stream looks like an Xtream-style live TS stream.
        
        Returns True if:
        - URL matches Xtream pattern with .ts extension, OR
        - Content-Type was detected as video/mp2t (MPEG-TS)
        """
        target = self._strip_stream_modifiers(self._last_resolved_url or self._current_url or "")
        if self._looks_like_xtream_live_ts_url(target):
            return True
        # Also check if we detected TS via content-type (for URLs without .ts extension)
        if self._detected_content_ts and self._current_stream_kind == "live":
            # Check it's not a known non-live pattern
            lower = target.lower()
            if any(token in lower for token in ("/timeshift/", "/movie/", "/series/", "/archive/", "catchup", "recording")):
                return False
            return True
        return False

    @staticmethod
    def _looks_like_xtream_live_ts_url(url: str) -> bool:
        if not url:
            return False
        if not InternalPlayerFrame._is_linear_ts(url):
            return False
        lower = url.lower()
        if any(token in lower for token in ("/timeshift/", "/movie/", "/series/", "/archive/", "catchup", "recording")):
            return False
        parsed = urllib.parse.urlparse(url)
        parts = [part for part in (parsed.path or "").split("/") if part]
        if len(parts) < 3:
            return False
        stem = parts[-1].rsplit(".", 1)[0]
        if stem.isdigit():
            return True
        if len(parts) >= 4 and parts[-2].isdigit():
            return True
        return False

    def _restart_expected_xtream_live(self) -> bool:
        if (
            self._destroyed
            or self._manual_stop
            or self._gave_up
            or not self._current_url
            or self._current_stream_kind != "live"
            or not self._looks_like_xtream_live_ts()
        ):
            self._pending_xtream_refresh = False
            return False
        if self._pending_restart:
            return True
        self._pending_restart = True
        self._pending_xtream_refresh = True
        self._last_restart_reason = "xtream segment rollover"
        self._last_restart_ts = time.monotonic()
        LOG.info("Xtream TS segment ended; refreshing stream without consuming retries.")
        self._update_status_label("Refreshing stream...")

        def _do_restart() -> None:
            self._pending_restart = False
            self._pending_xtream_refresh = False
            if self._destroyed or self._manual_stop or not self._current_url:
                return
            try:
                self.play(
                    self._current_url,
                    self._current_title,
                    stream_kind=self._current_stream_kind,
                    _retry=True,
                )
            except Exception as err:
                LOG.error("Xtream stream refresh failed: %s", err)

        wx.CallLater(200, _do_restart)
        return True

    def _resolve_stream_url(self, url: str, headers: Optional[Dict[str, object]] = None) -> Tuple[str, Optional[float]]:
        cap = self._variant_max_mbps
        if not cap:
            self._last_variant_url = None
            return url, None
        lower = url.lower()
        if "m3u8" not in lower and "manifest" not in lower:
            self._last_variant_url = None
            return url, None

        manifest_text = self._fetch_hls_manifest(url, headers=headers)
        if not manifest_text:
            self._last_variant_url = None
            return url, None
        variants = self._parse_hls_variants(manifest_text, url)
        if not variants:
            self._last_variant_url = None
            return url, None

        selected = self._select_hls_variant(variants, cap)
        if not selected:
            self._last_variant_url = None
            return url, None
        self._last_variant_url = selected["url"]
        return selected["url"], selected.get("bandwidth_mbps")

    def _fetch_hls_manifest(self, url: str, headers: Optional[Dict[str, object]] = None) -> Optional[str]:
        req = urllib.request.Request(url, headers=self._request_headers(headers))
        try:
            with urllib.request.urlopen(req, timeout=4) as response:
                data = response.read(512_000)
        except Exception as err:
            LOG.debug("Failed to fetch HLS manifest %s: %s", url, err)
            return None
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def _parse_hls_variants(self, manifest_text: str, base_url: str) -> List[dict]:
        variants: List[dict] = []
        if not manifest_text:
            return variants
        lines = manifest_text.splitlines()
        total = len(lines)
        idx = 0
        while idx < total:
            line = lines[idx].strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                attrs = {k: v.strip('"') for k, v in _HLS_ATTR_RE.findall(line)}
                bandwidth_val = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH")
                bandwidth_mbps: Optional[float] = None
                if bandwidth_val:
                    try:
                        bandwidth_mbps = max(float(bandwidth_val) / 1_000_000.0, 0.0)
                    except Exception:
                        bandwidth_mbps = None
                uri = ""
                look_ahead = idx + 1
                while look_ahead < total:
                    next_line = lines[look_ahead].strip()
                    if not next_line:
                        look_ahead += 1
                        continue
                    if next_line.startswith("#"):
                        if next_line.startswith("#EXT-X-STREAM-INF"):
                            break
                        look_ahead += 1
                        continue
                    uri = next_line
                    break
                if uri:
                    resolved = urllib.parse.urljoin(base_url, uri)
                    variants.append(
                        {
                            "url": resolved,
                            "bandwidth_mbps": bandwidth_mbps,
                            "raw_bandwidth": bandwidth_val,
                        }
                    )
                idx = look_ahead
                continue
            idx += 1
        return variants

    @staticmethod
    def _select_hls_variant(variants: List[dict], cap_mbps: float) -> Optional[dict]:
        if not variants:
            return None
        eligible = [v for v in variants if v.get("bandwidth_mbps") and v["bandwidth_mbps"] <= cap_mbps]
        if eligible:
            return max(eligible, key=lambda v: v["bandwidth_mbps"] or 0.0)
        with_bandwidth = [v for v in variants if v.get("bandwidth_mbps")]
        if with_bandwidth:
            return min(with_bandwidth, key=lambda v: v["bandwidth_mbps"] or 0.0)
        return variants[0]

    def _ensure_player_window(self) -> None:
        if self._have_handle:
            return
        if not self.video_panel:
            return
        with self._handle_guard:
            if self._have_handle:
                return
            handle = self.video_panel.GetHandle()
            if not handle:
                if not self._auto_handle_bound:
                    self._auto_handle_bound = True
                    wx.CallLater(int(self._HANDLE_CHECK_INTERVAL * 1000), self._ensure_player_window)
                return
            try:
                if platform.system() == "Windows":
                    self.player.set_hwnd(handle)  # type: ignore[attr-defined]
                elif platform.system() == "Linux":
                    self.player.set_xwindow(handle)  # type: ignore[attr-defined]
                elif platform.system() == "Darwin":
                    self.player.set_nsobject(int(handle))  # type: ignore[attr-defined]
                else:
                    self.player.set_hwnd(handle)  # type: ignore[attr-defined]
                self._have_handle = True
            except Exception as err:
                LOG.warning("Failed to bind video surface: %s", err)
                wx.CallLater(int(self._HANDLE_CHECK_INTERVAL * 1000), self._ensure_player_window)

    @staticmethod
    def _is_likely_audio(url: str) -> bool:
        if not url:
            return False
        lower = url.lower()
        # Common audio extensions
        if lower.endswith((".mp3", ".aac", ".ogg", ".opus", ".flac", ".wav", ".m4a", ".wma")):
            return True
        # Common radio keywords
        if any(token in lower for token in ("radio", "icecast", "shoutcast", "streamon.fm")):
            if ".m3u8" not in lower and ".ts" not in lower:
                return True
        return False

    def _is_current_stream_ts(self, url: str) -> bool:
        """Check if current stream is TS (either by URL extension or detected content-type)."""
        if self._is_linear_ts(url):
            return True
        return self._detected_content_ts

    def _compute_buffer_profile(
        self,
        url: str,
        *,
        bitrate_hint: Optional[float] = None,
        headers: Optional[Dict[str, object]] = None,
    ) -> Tuple[float, dict, Optional[float]]:
        bitrate = bitrate_hint if bitrate_hint and bitrate_hint > 0 else self._estimate_stream_bitrate(url, headers=headers)
        base = self.base_buffer_seconds
        is_linear_ts = self._is_current_stream_ts(url)
        is_audio = self._is_likely_audio(url)
        
        cache_fraction = self._network_cache_fraction
        if is_linear_ts:
            cache_fraction = max(cache_fraction, self._ts_network_bias)

        # Fast startup: use minimal initial buffer, rely on reconnect logic for stability
        # User can increase base_buffer_seconds in config if they have slow internet
        if is_audio:
            # Audio streams: very fast start
            raw_target = max(base, 2.0)
        elif bitrate is None:
            # Unknown bitrate: use base + small margin for fast startup
            raw_target = max(base, 3.0)
        elif bitrate <= 3.0:
            # Low bitrate (SD): quick start
            raw_target = max(base, 3.0)
        elif bitrate <= 8.0:
            # Medium bitrate (720p-1080p): slightly more buffer
            raw_target = max(base, 4.0)
        else:
            # High bitrate (HD/4K): a bit more to absorb startup jitter
            raw_target = max(base, 5.0)
            
        if is_linear_ts:
            raw_target = max(raw_target, self._ts_buffer_floor)

        target = min(self._max_buffer_seconds, max(base, raw_target))
        
        # Allow network cache to take up most of the buffer time
        network_candidate = max(target - 0.5, target * cache_fraction)
        network_target = max(self._min_network_cache_seconds, network_candidate)
        network_target = min(network_target, self._max_network_cache_seconds, target)
        network_ms = int(network_target * 1000)
        
        file_pad = 6.0 if is_linear_ts else 4.0
        live_pad = 4.0 if is_linear_ts else 2.5
        disc_pad = file_pad
        
        # Calculate file caching layer
        file_cache_target = max(target + file_pad, network_target + (file_pad + 2.0))
        file_cache = max(4000, int(file_cache_target * 1000))
        
        demux_cap = 45.0 if is_linear_ts else 40.0
        profile = {
            "network_ms": network_ms,
            "live_ms": int(max(target + live_pad, network_target + (live_pad + 1.5)) * 1000),
            "file_ms": file_cache,
            "disc_ms": max(file_cache, int((target + disc_pad) * 1000)),
            "demux_read_ahead": max(8, int(min(target, demux_cap) * 0.85)),
            "adaptive_cache": True,
        }
        return network_target, profile, bitrate

    def _estimate_stream_bitrate(self, url: str, headers: Optional[Dict[str, object]] = None) -> Optional[float]:
        parsed = urllib.parse.urlparse(url)
        query_hint = self._extract_bitrate_from_query(parsed.query)
        if query_hint:
            return query_hint

        lower = url.lower()
        if "m3u8" not in lower and "manifest" not in lower:
            return None

        req = urllib.request.Request(url, headers=self._request_headers(headers))
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                data = response.read(65536)
        except Exception as err:
            LOG.debug("Bitrate probe failed for %s: %s", url, err)
            return None

        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            return None

        matches = re.findall(r"BANDWIDTH=(\d+)", text)
        if not matches:
            matches = re.findall(r"AVERAGE-BANDWIDTH=(\d+)", text)
        if not matches:
            return None

        try:
            highest = max(int(m) for m in matches)
        except Exception:
            return None
        if highest <= 0:
            return None
        return highest / 1_000_000

    def _preflight_check(self, url: str, headers: Optional[Dict[str, object]] = None) -> Optional[str]:
        """Quick HTTP check to detect obvious errors before VLC tries to open.
        
        Returns None if OK, or an error message string if the stream is unreachable.
        Only checks the initial HTTP response - does not follow redirects or wait for stream data.
        VLC handles redirects and auth tokens differently, so we just verify basic connectivity.
        """
        if not url or not url.startswith(("http://", "https://")):
            return None  # Skip check for non-HTTP URLs
        
        req_headers = self._request_headers(headers, include_accept=True)
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        
        # Don't follow redirects - just check the initial response
        # VLC handles redirects internally and may handle auth tokens differently
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None  # Don't follow redirects
        
        opener = urllib.request.build_opener(NoRedirectHandler)
        
        try:
            # Short timeout - just verify we can connect and get HTTP status
            resp = opener.open(req, timeout=5)
            status = resp.status
            resp.close()
            if status >= 400:
                return f"HTTP error {status}"
            return None  # Success - got 2xx/3xx response (3xx means redirect, let VLC handle it)
        except urllib.error.HTTPError as e:
            # 3xx redirects are fine - VLC will follow them
            if 300 <= e.code < 400:
                return None  # Redirect is OK
            LOG.warning("Stream preflight failed: HTTP %d %s for %s", e.code, e.reason, url)
            if e.code == 404:
                return f"Stream not found (HTTP 404). The channel may be offline or the URL may have expired."
            elif e.code == 403:
                return f"Access denied (HTTP 403). Authentication may have expired."
            elif e.code == 401:
                return f"Authentication required (HTTP 401). Please check your credentials."
            elif e.code == 502:
                return f"Bad gateway (HTTP 502). The provider's server may be having issues."
            elif e.code == 503:
                return f"Service unavailable (HTTP 503). The provider may be overloaded."
            elif e.code >= 500:
                return f"Server error (HTTP {e.code}). Try again later."
            else:
                return f"HTTP error {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            reason = str(e.reason) if e.reason else "Unknown error"
            LOG.warning("Stream preflight failed: %s for %s", reason, url)
            if "ssl" in reason.lower() or "certificate" in reason.lower():
                return f"SSL/TLS error: {reason}"
            elif "timeout" in reason.lower() or "timed out" in reason.lower():
                return None  # Timeouts are OK - let VLC try with its own buffering
            elif "refused" in reason.lower():
                return f"Connection refused. The server may be down."
            else:
                return f"Connection error: {reason}"
        except Exception as e:
            LOG.debug("Preflight check exception (non-fatal): %s", e)
            return None  # Let VLC try anyway for unknown errors

    def _detect_stream_content_type(self, url: str, headers: Optional[Dict[str, object]] = None) -> Optional[str]:
        """Follow redirects and detect the content type of the final URL.
        
        Returns the content type string if detected, or None on error.
        Also sets self._detected_content_ts if the content is video/mp2t.
        Uses GET with early close to follow redirects (HEAD may not be supported).
        """
        if not url or not url.startswith(("http://", "https://")):
            return None
        
        req_headers = self._request_headers(headers, include_accept=True)
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        
        try:
            # Use GET and close immediately after reading headers
            # (HEAD may not follow redirects properly on some servers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                content_type = resp.getheader("Content-Type", "")
                final_url = resp.geturl()
                # Don't read the body, just close
                LOG.debug("Stream content type: %s (final URL: %s)", content_type, final_url[:100] if final_url else "?")
                
                # Check for MPEG-TS content types
                ct_lower = content_type.lower()
                if any(ts_type in ct_lower for ts_type in ("video/mp2t", "video/mpeg", "application/octet-stream")):
                    self._detected_content_ts = True
                    LOG.debug("Detected TS stream via Content-Type: %s", content_type)
                
                return content_type
        except Exception as e:
            LOG.debug("Content type detection failed: %s", e)
            return None

    @staticmethod
    def _extract_bitrate_from_query(query: str) -> Optional[float]:
        if not query:
            return None
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        for key, value in pairs:
            key_l = key.lower()
            if "bitrate" in key_l or "bandwidth" in key_l:
                try:
                    numeric = float(value)
                except Exception:
                    continue
                if numeric > 10_000:
                    return numeric / 1_000_000
                if numeric > 500:
                    return numeric / 1000.0
                if numeric > 0:
                    return numeric
        return None

    def _schedule_restart(self, reason: str, adjust_buffer: bool = False) -> None:
        if self._destroyed or not self._current_url or self._manual_stop or self._gave_up:
            return
        self._pending_xtream_refresh = False
        now = time.monotonic()
        if self._last_restart_ts and (now - self._last_restart_ts) >= self._reconnect_reset_window:
            if self._reconnect_attempts:
                LOG.debug(
                    "Resetting reconnect attempts after %.1fs without retries.",
                    now - self._last_restart_ts,
                )
            self._reconnect_attempts = 0
        if self._pending_restart:
            return
        if now - self._last_restart_ts < self._restart_cooldown:
            return
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            if not self._gave_up:
                self._gave_up = True
                self._manual_stop = True
                self._current_url = None
                # Build informative error message
                reason_hint = ""
                if self._last_restart_reason:
                    reason_hint = f"\n\nLast error: {self._last_restart_reason}"
                wx.CallAfter(
                    wx.MessageBox,
                    f"Stream disconnected after {self._max_reconnect_attempts} retries. "
                    f"The stream may be offline or experiencing issues.{reason_hint}\n\n"
                    "Please try another channel or try again later.",
                    "Stream Lost",
                    wx.OK | wx.ICON_WARNING,
                )
                self._update_status_label("Stream lost")
            return

        self._pending_restart = True
        self._reconnect_attempts += 1
        self._last_restart_ts = now
        self._last_restart_reason = reason
        LOG.info(
            "Attempting stream recovery (%s) [%d/%d]",
            reason,
            self._reconnect_attempts,
            self._max_reconnect_attempts,
        )
        self._update_status_label("Reconnecting...")

        def _do_restart() -> None:
            self._pending_restart = False
            if self._destroyed or not self._current_url or self._manual_stop or self._gave_up:
                return
            if adjust_buffer:
                new_base = min(self.base_buffer_seconds + self._buffer_step_seconds, self._max_buffer_seconds)
                if new_base > self.base_buffer_seconds:
                    LOG.info("Increasing base buffer to %.1fs to stabilise playback.", new_base)
                    self.base_buffer_seconds = new_base
                    self._refresh_ts_floor()
                    self._update_cache_bounds()
            try:
                self.play(self._current_url, self._current_title, _retry=True)
            except Exception as err:
                LOG.error("Stream restart failed: %s", err)

        wx.CallLater(400, _do_restart)

    def _prune_buffer_events(self, now: float) -> None:
        while self._buffering_events and now - self._buffering_events[0] > self._choppy_window_seconds:
            self._buffering_events.popleft()

    def _record_buffer_event(self, now: float) -> None:
        if self._buffer_start_ts is None:
            return
        duration = now - self._buffer_start_ts
        if duration < self._min_buffer_event_seconds:
            return
        self._buffering_events.append(now)
        self._maybe_handle_choppy(now)

    def _maybe_handle_choppy(self, now: float) -> None:
        if not self._has_seen_playing:
            return
        self._prune_buffer_events(now)
        if len(self._buffering_events) < self._choppy_threshold:
            return
        if now - self._last_adjust_ts < self._choppy_cooldown:
            return
        self._last_adjust_ts = now
        self._schedule_restart("detected choppy playback", adjust_buffer=True)

    def _monitor_playback_progress(self, now: float, state_key: str) -> None:
        if not self._has_seen_playing or state_key != "playing":
            if state_key != "playing":
                self._stall_ticks = 0
            return
        try:
            position = self.player.get_time()
        except Exception:
            position = None
        if position is None or position < 0:
            self._stall_ticks = 0
            self._last_position_ms = None
            return
        if self._last_position_ms is None:
            self._last_position_ms = position
            self._stall_ticks = 0
            return
        if position <= self._last_position_ms + 200:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0
        self._last_position_ms = position
        if self._stall_ticks >= self._stall_threshold:
            self._stall_ticks = 0
            self._schedule_restart("playback stalled", adjust_buffer=True)

    def _should_auto_recover_on_end(self) -> bool:
        if self._manual_stop or not self._current_url:
            return False
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            return False
        try:
            length = self.player.get_length()
        except Exception:
            length = -1
        if length <= 0:
            return True
        try:
            current = self.player.get_time()
        except Exception:
            current = -1
        if current < 0:
            return True
        return (length - current) > self._end_near_threshold_ms

    def _focus_control_step(self, forward: bool) -> None:
        order = [ctrl for ctrl in self._controls_focus_order if ctrl]
        if not order:
            return
        current = wx.Window.FindFocus()
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0 if forward else len(order) - 1
        else:
            idx = (idx + 1) % len(order) if forward else (idx - 1) % len(order)
        target = order[idx]
        if target:
            target.SetFocus()

    def _on_toggle_pause(self, _event: Optional[wx.Event] = None) -> None:
        state = None
        try:
            state = self.player.get_state()
        except Exception:
            state = None

        can_resume = bool(self._current_url or self._last_resolved_url)
        is_stopped_state = False
        try:
            is_stopped_state = bool(state in (vlc.State.Stopped, vlc.State.Ended))  # type: ignore[attr-defined]
        except Exception:
            # If vlc.State is unavailable, fall back to simple string check
            is_stopped_state = str(state).lower() in {"state.stopped", "stopped", "ended"}

        if self._manual_stop and can_resume:
            # Manual stop: treat the button as Play to resume the last media.
            self._manual_stop = False
            self._gave_up = False
            self._pending_restart = False
            self._reconnect_attempts = 0
            self._status_timer.Start(500)
            try:
                self.player.play()
                self._is_paused = False
                self.play_pause_btn.SetLabel("Pause")
                self._update_status_label("Buffering...")
            except Exception:
                pass
            return

        if is_stopped_state and can_resume:
            # VLC reports stopped but not from a manual stop; try to restart.
            try:
                self.player.play()
                self._is_paused = False
                self.play_pause_btn.SetLabel("Pause")
                self._status_timer.Start(500)
                self._update_status_label("Buffering...")
            except Exception:
                pass
            return

        try:
            self.player.pause()
            self._is_paused = not self._is_paused
            self.play_pause_btn.SetLabel("Resume" if self._is_paused else "Pause")
        except Exception:
            pass

    def _on_cast(self, _event: Optional[wx.Event] = None) -> None:
        if not self._on_cast_cb:
            wx.MessageBox("Casting is unavailable because the caster is not initialised.", "Casting", wx.OK | wx.ICON_INFORMATION)
            return
        url = self._current_url or self._last_resolved_url or ""
        if not url:
            wx.MessageBox("No active stream to cast.", "Casting", wx.OK | wx.ICON_WARNING)
            return
        try:
            self._on_cast_cb(url, self._current_title or "IPTV Stream", self._current_headers)
        except Exception as exc:
            LOG.error("Cast callback failed: %s", exc)

    def _update_status_label(self, prefix: str = "", volume_override: Optional[int] = None) -> None:
        bitrate_txt = ""
        if self._last_bitrate_mbps:
            bitrate_txt = f" | ~{self._last_bitrate_mbps:.1f} Mbps"
        buf_txt = f"{self._last_buffer_seconds:.1f}s buffer"
        
        vol = int(self._volume_value) if volume_override is None else volume_override
        vol_txt = f" | Vol {vol}%"
        
        # If prefix is empty, try to preserve existing prefix from label? 
        # No, caller usually knows state. If called from volume update, we might lose state info ("Buffering...").
        # But `_perform_update` doesn't know the state.
        # We should store `_current_state_prefix` in the class to update label correctly.
        # But for now, let's just update if we can.
        # Actually, if prefix is "", we might be erasing "Buffering...".
        # Let's add `self._last_status_prefix` to __init__ and use it.
        
        # NOTE: current_lbl was used for debugging; removed to silence lint warning.
        # Hacky heuristic: if prefix arg is empty, try to keep the existing prefix text (everything before " X.Xs buffer")
        # But simpler is to just use a stored state variable.
        
        # Let's assume the user is okay with just "Vol XX%" updating if we don't have state.
        # Or better: `buf_txt` is always generated fresh.
        
        real_prefix = prefix if prefix else getattr(self, "_last_status_prefix", "Idle")
        if prefix:
            self._last_status_prefix = prefix
            
        label = f"{real_prefix}{(' ' if real_prefix else '')}{buf_txt}{bitrate_txt}{vol_txt}"
        self.status_label.SetLabel(label.strip())

    def _on_timer(self, _event: wx.TimerEvent) -> None:
        try:
            state = self.player.get_state()
        except Exception:
            state = None
        state_name = "Unknown"
        if state is not None:
            try:
                state_name = state.name  # type: ignore[attr-defined]
            except Exception:
                state_name = str(state)
        state_key = state_name.lower()
        now = time.monotonic()

        if self._last_state_name != state_key:
            self._last_state_name = state_key
            if state_key == "playing":
                self._has_seen_playing = True
                self._stall_ticks = 0
            elif state_key != "playing":
                self._stall_ticks = 0

        self._monitor_playback_progress(now, state_key)
        xtream_live = self._current_stream_kind == "live" and self._looks_like_xtream_live_ts()

        if state_key == "buffering":
            if self._buffer_start_ts is None:
                self._buffer_start_ts = now
            buffer_duration = now - self._buffer_start_ts
            since_start = now - self._play_start_monotonic if self._play_start_monotonic else float("inf")
            allow_recovery = self._has_seen_playing
            handled_xtream_refresh = False
            if (
                allow_recovery
                and xtream_live
                and not self._pending_restart
                and buffer_duration >= self._xtream_buffer_refresh_seconds
            ):
                handled_xtream_refresh = self._restart_expected_xtream_live()
            if (
                allow_recovery
                and not handled_xtream_refresh
                and not self._pending_restart
                and not self._early_buffer_fix_applied
                and since_start <= 45.0
                and buffer_duration >= 3.0
            ):
                self._early_buffer_fix_applied = True
                self._schedule_restart("early buffering detected", adjust_buffer=True)
            elif allow_recovery and not handled_xtream_refresh and not self._pending_restart and buffer_duration >= 6.0:
                self._schedule_restart("prolonged buffering", adjust_buffer=True)
        else:
            if self._buffer_start_ts is not None:
                self._record_buffer_event(now)
            self._buffer_start_ts = None

        prefix = "Paused" if self._is_paused else state_name.capitalize()

        if state_key == "error":
            if not self._restart_expected_xtream_live():
                self._schedule_restart("playback error", adjust_buffer=True)
                prefix = "Reconnecting..."
            else:
                prefix = "Refreshing stream..."
        elif state_key == "stopped":
            if not self._manual_stop:
                if not self._restart_expected_xtream_live():
                    self._schedule_restart("stream stopped unexpectedly")
                    prefix = "Reconnecting..."
                else:
                    prefix = "Refreshing stream..."
        elif state_key == "ended":
            if self._restart_expected_xtream_live():
                prefix = "Refreshing stream..."
            elif self._should_auto_recover_on_end():
                self._schedule_restart("stream ended unexpectedly")
                prefix = "Reconnecting..."
            else:
                self.stop(manual=False)
                prefix = "Ended"
        elif state_key == "buffering":
            prefix = "Buffering..."

        if self._pending_restart:
            prefix = "Refreshing stream..." if self._pending_xtream_refresh else "Reconnecting..."
        elif self._gave_up:
            prefix = "Stream lost"

        self._update_status_label(prefix)

    def _on_video_panel_resize(self, event: wx.Event) -> None:
        self._ensure_player_window()
        event.Skip()

    def _on_video_panel_destroy(self, _event: wx.Event) -> None:
        self._have_handle = False

    def _on_video_panel_click(self, _event: wx.Event) -> None:
        self.video_panel.SetFocus()

    def _on_navigation_key(self, event: wx.NavigationKeyEvent) -> None:
        self._focus_control_step(event.GetDirection())
        event.Skip(False)

    def _on_mouse_wheel(self, event: wx.MouseEvent) -> None:
        rot = event.GetWheelRotation()
        if rot > 0:
            self._adjust_volume(2)
        elif rot < 0:
            self._adjust_volume(-2)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        key = event.GetKeyCode()
        
        # Volume control with optional Ctrl modifier for speed
        if key in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
            step = 5 if event.ControlDown() else 2
            self._adjust_volume(step)
            return
        if key in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
            step = 5 if event.ControlDown() else 2
            self._adjust_volume(-step)
            return
            
        if key == wx.WXK_F11:
            self._set_fullscreen(not self._fullscreen)
            return
        if key == wx.WXK_ESCAPE and self._fullscreen:
            self._set_fullscreen(False)
            return
        event.Skip()

    def _schedule_volume_apply(self) -> None:
        def _apply() -> None:
            try:
                # Ensure audio is not muted (can happen with --intf=dummy)
                self.player.audio_set_mute(False)
                current = self.player.audio_get_volume()
                if current >= 0:
                    self._volume_value = current
                self.player.audio_set_volume(self._volume_value)
                self._sync_volume_slider()
            except Exception:
                pass

        wx.CallLater(150, _apply)

    def _sync_volume_slider(self) -> None:
        if hasattr(self, "volume_slider") and self.volume_slider:
            try:
                self.volume_slider.SetValue(int(min(100, max(0, self._volume_value))))
            except Exception:
                pass

    def _on_volume_slider(self, event: wx.CommandEvent) -> None:
        try:
            value = int(self.volume_slider.GetValue())
        except Exception:
            return
        self._apply_volume(value)
        event.Skip(False)

    def _adjust_volume(self, delta: int) -> None:
        new_val = max(0, min(100, self._volume_value + delta))
        if new_val != self._volume_value:
            self._apply_volume(new_val)

    def _apply_volume(self, value: float) -> None:
        self._volume_value = value
        ival = int(value)
        
        # 1. Update UI immediately
        self._update_status_label()
        if self.volume_slider.GetValue() != ival:
            self.volume_slider.SetValue(ival)
            
        # 2. Apply to VLC immediately
        # Simple, direct, synchronous call.
        try:
            self.player.audio_set_volume(ival)
        except Exception:
            pass

    def _set_fullscreen(self, enable: bool) -> None:
        enable = bool(enable)
        if enable == self._fullscreen:
            return
        self._fullscreen = enable
        self.ShowFullScreen(enable, style=wx.FULLSCREEN_ALL)
        self.controls_panel.Show(not enable)
        self.fullscreen_btn.SetLabel("Exit Full Screen" if enable else "Full Screen")
        self.Layout()
        wx.CallAfter(self.video_panel.SetFocus)

    def _on_toggle_fullscreen(self, _event: Optional[wx.Event] = None) -> None:
        self._set_fullscreen(not self._fullscreen)

    def _hide_player(self) -> None:
        # Keep playback running; just hide the window.
        try:
            self.Hide()
        except Exception:
            pass
        try:
            self._status_timer.Start(500)
        except Exception:
            pass

    def _exit_player(self) -> None:
        # Stop playback and destroy window explicitly.
        self._allow_close = True
        try:
            self._status_timer.Stop()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        if self._on_close_cb:
            try:
                self._on_close_cb()
            except Exception:
                pass
        self.Destroy()

    def _on_close(self, event: wx.CloseEvent) -> None:
        if not self._allow_close and event.CanVeto():
            event.Veto()
            self._hide_player()
            return
        self._status_timer.Stop()
        try:
            self.player.stop()
        except Exception:
            pass
        if self._on_close_cb:
            try:
                self._on_close_cb()
            except Exception:
                pass
        self._destroyed = True
        event.Skip()

    def Destroy(self) -> bool:
        if self._destroyed:
            return super().Destroy()
        self._destroyed = True
        self._allow_close = True
        self._status_timer.Stop()
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.release()
        except Exception:
            pass
        try:
            self.instance.release()
        except Exception:
            pass
        return super().Destroy()


__all__ = [
    "InternalPlayerFrame",
    "InternalPlayerUnavailableError",
    "_VLC_IMPORT_ERROR",
]

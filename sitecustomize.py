"""Runtime patches for the IPTV client."""

import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

try:
    import wx  # type: ignore
except Exception:  # pragma: no cover - wx not installed in some environments
    wx = None

try:
    import internal_player as _ip  # type: ignore
except Exception:  # pragma: no cover - module not available for some tools
    _ip = None

if wx is not None and _ip is not None and hasattr(_ip, "InternalPlayerFrame"):
    LOG = logging.getLogger(__name__)

    _HLS_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')

    InternalPlayerUnavailableError = _ip.InternalPlayerUnavailableError
    vlc = _ip.vlc
    _VLC_IMPORT_ERROR = getattr(_ip, "_VLC_IMPORT_ERROR", None)

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

    class PatchedInternalPlayerFrame(wx.Frame):
        """Embedded IPTV player with improved resilience and keyboard control."""

        _HANDLE_CHECK_INTERVAL = 0.1

        def __init__(
            self,
            parent: Optional[wx.Window],
            base_buffer_seconds: float = 12.0,
            max_buffer_seconds: Optional[float] = None,
            variant_max_mbps: Optional[float] = None,
            on_close: Optional[Callable[[], None]] = None,
        ) -> None:
            _prepare_vlc_runtime()
            if vlc is None:
                raise InternalPlayerUnavailableError("python-vlc (libVLC) is not available.")
            super().__init__(parent, title="Built-in IPTV Player", size=(960, 540))
            self._on_close_cb = on_close
            base_value = self._coerce_seconds(base_buffer_seconds, fallback=12.0)
            self._last_bitrate_mbps: Optional[float] = None
            self._current_url: Optional[str] = None
            self._current_title: str = ""
            self._current_stream_kind: str = "live"
            self._last_resolved_url: Optional[str] = None
            self._auto_handle_bound = False
            self._have_handle = False
            self._is_paused = False
            self._destroyed = False
            self._handle_guard = threading.Lock()
            self._fullscreen = False
            self._volume_value = 80
            self._manual_stop = False
            self._gave_up = False
            self._pending_restart = False
            self._pending_xtream_refresh = False
            self._reconnect_attempts = 0
            self._max_reconnect_attempts = 4
            self._last_restart_ts = 0.0
            self._last_restart_reason = ""
            self._buffer_step_seconds = 2.0
            self._max_buffer_seconds = self._resolve_max_buffer(max_buffer_seconds, base_value)
            self.base_buffer_seconds = max(6.0, min(base_value, self._max_buffer_seconds))
            self._network_cache_fraction = 0.85
            self._min_network_cache_seconds = 5.0
            self._max_network_cache_seconds = 18.0
            self._ts_network_bias = 0.92
            self._refresh_ts_floor()
            self._update_cache_bounds()
            self._last_buffer_seconds: float = self.base_buffer_seconds
            self._variant_max_mbps: Optional[float] = self._sanitize_variant_cap(variant_max_mbps)
            self._last_variant_url: Optional[str] = None
            self._buffering_events: Deque[float] = deque(maxlen=20)
            self._choppy_window_seconds = 25.0
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
            self._buffer_start_ts: Optional[float] = None
            self._early_buffer_fix_applied = False
            self._has_seen_playing = False
            self._buffer_pause_active = False
            self._buffer_pause_until = 0.0
            self._auto_pausing = False
            self._buffer_pause_failures = 0
            self._min_buffer_event_seconds = 1.25
            self._xtream_buffer_refresh_seconds = 4.5

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
            main_sizer.Add(self.video_panel, 1, wx.EXPAND | wx.ALL, 0)

            self.controls_panel = wx.Panel(panel, style=wx.TAB_TRAVERSAL)
            self.controls_panel.SetBackgroundColour(wx.BLACK)
            controls = wx.BoxSizer(wx.HORIZONTAL)

            self.play_pause_btn = wx.Button(self.controls_panel, label="Pause")
            self.play_pause_btn.Bind(wx.EVT_BUTTON, self._on_toggle_pause)
            self.play_pause_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
            self.play_pause_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

            self.stop_btn = wx.Button(self.controls_panel, label="Stop")
            self.stop_btn.Bind(wx.EVT_BUTTON, lambda evt: self.stop(evt, manual=True))
            self.stop_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
            self.stop_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

            self.fullscreen_btn = wx.Button(self.controls_panel, label="Full Screen")
            self.fullscreen_btn.Bind(wx.EVT_BUTTON, self._on_toggle_fullscreen)
            self.fullscreen_btn.Bind(wx.EVT_NAVIGATION_KEY, self._on_navigation_key)
            self.fullscreen_btn.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

            controls.Add(self.play_pause_btn, 0, wx.ALL, 5)
            controls.Add(self.stop_btn, 0, wx.ALL, 5)
            controls.Add(self.fullscreen_btn, 0, wx.ALL, 5)
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
                self.fullscreen_btn,
            ]

            self._status_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_timer, self._status_timer)

            wx.CallAfter(self._ensure_player_window)
            wx.CallAfter(self.play_pause_btn.SetFocus)
            self._update_status_label("Idle")

        # ------------------------------------------------------------------ public
        def play(
            self,
            url: str,
            title: Optional[str] = None,
            *,
            stream_kind: Optional[str] = None,
            _retry: bool = False,
        ) -> None:
            """Start playback of the given URL with buffering and recovery hooks."""
            if self._destroyed:
                raise InternalPlayerUnavailableError("Player window has been destroyed.")
            if self._buffer_pause_active:
                self._cancel_buffer_pause(resume=False)
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

            self._current_url = url
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
            self._buffer_pause_active = False
            self._buffer_pause_until = 0.0
            self._auto_pausing = False
            self._buffer_pause_failures = 0

            self.SetTitle(f"{self._current_title} - Built-in Player")
            playback_url, variant_bitrate = self._resolve_stream_url(url)
            self._last_resolved_url = playback_url
            target_buffer, cache_profile, bitrate = self._compute_buffer_profile(
                playback_url, bitrate_hint=variant_bitrate
            )
            self._last_buffer_seconds = target_buffer
            self._last_bitrate_mbps = bitrate

            media = self.instance.media_new(playback_url)
            self._apply_cache_options(media, cache_profile)
            try:
                self.player.stop()
            except Exception:
                pass
            self.player.set_media(media)
            self._ensure_player_window()
            self.player.play()
            self._is_paused = False
            self.play_pause_btn.SetLabel("Pause")
            self._schedule_volume_apply()
            self._status_timer.Start(500)
            self._update_status_label("Buffering...")

        def stop(self, _evt: Optional[wx.Event] = None, manual: bool = False) -> None:
            manual_stop = manual or (_evt is not None)
            if manual_stop:
                self._manual_stop = True
                self._reconnect_attempts = 0
                self._last_restart_reason = ""
                self._current_url = None
                self._gave_up = False
            self._pending_restart = False
            self._buffer_pause_failures = 0
            self._cancel_buffer_pause(resume=False)
            self._status_timer.Stop()
            try:
                self.player.stop()
            except Exception:
                pass
            self._is_paused = False
            self._has_seen_playing = False
            self.play_pause_btn.SetLabel("Pause")
            self._update_status_label("Stopped")

        def update_base_buffer(self, seconds: float) -> None:
            """Update default buffer target for future plays."""
            try:
                seconds = float(seconds)
            except Exception:
                return
            self.base_buffer_seconds = max(6.0, min(seconds, self._max_buffer_seconds))
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

            media.add_option(f":network-caching={profile['network_ms']}")
            media.add_option(f":live-caching={profile['live_ms']}")
            media.add_option(f":file-caching={profile['file_ms']}")
            media.add_option(f":disc-caching={profile['disc_ms']}")

            if profile.get("demux_read_ahead"):
                media.add_option(f":demux-read-ahead={profile['demux_read_ahead']}")
            if profile.get("adaptive_cache"):
                media.add_option(":adaptive-use-access=1")

        # ---------------------------------------------------------------- helpers
        @staticmethod
        def _coerce_seconds(value: Optional[float], *, fallback: float) -> float:
            try:
                coerced = float(value) if value is not None else float(fallback)
            except Exception:
                coerced = float(fallback)
            return max(0.0, coerced)

        def _resolve_max_buffer(self, supplied: Optional[float], base_value: float) -> float:
            raw_max = self._coerce_seconds(supplied if supplied is not None else 18.0, fallback=18.0)
            if raw_max <= 0.0:
                raw_max = 18.0
            resolved = max(6.0, raw_max)
            if resolved < 6.0:
                resolved = 6.0
            if resolved < base_value:
                return max(6.0, base_value)
            return resolved

        def _update_cache_bounds(self) -> None:
            upper = max(self.base_buffer_seconds * 0.95, self.base_buffer_seconds + 8.0, 12.0)
            ts_floor = getattr(self, "_ts_buffer_floor", None)
            if ts_floor:
                upper = max(upper, ts_floor)
            upper = min(self._max_buffer_seconds, upper)
            lower = min(upper - 2.0, upper * 0.6)
            lower = max(5.0, lower)
            self._max_network_cache_seconds = upper
            self._min_network_cache_seconds = lower

        def _refresh_ts_floor(self) -> None:
            self._ts_buffer_floor = min(self._max_buffer_seconds, max(self.base_buffer_seconds + 6.0, 18.0))

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
            target = self._strip_stream_modifiers(self._last_resolved_url or self._current_url or "")
            return self._looks_like_xtream_live_ts_url(target)

        @staticmethod
        def _looks_like_xtream_live_ts_url(url: str) -> bool:
            if not url:
                return False
            if not PatchedInternalPlayerFrame._is_linear_ts(url):
                return False
            lower = url.lower()
            if any(
                token in lower for token in ("/timeshift/", "/movie/", "/series/", "/archive/", "catchup", "recording")
            ):
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
            self._cancel_buffer_pause(resume=True)
            self._buffer_pause_failures = 0
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

        def _resolve_stream_url(self, url: str) -> Tuple[str, Optional[float]]:
            cap = self._variant_max_mbps
            if not cap:
                self._last_variant_url = None
                return url, None
            lower = url.lower()
            if "m3u8" not in lower and "manifest" not in lower:
                self._last_variant_url = None
                return url, None

            manifest_text = self._fetch_hls_manifest(url)
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

        def _fetch_hls_manifest(self, url: str) -> Optional[str]:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "IPTVClient/1.0",
                    "Accept": "application/x-mpegURL,application/vnd.apple.mpegurl,text/plain,*/*",
                },
            )
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
                    if wx.Platform == "__WXMSW__":
                        self.player.set_hwnd(handle)  # type: ignore[attr-defined]
                    elif wx.Platform == "__WXGTK__":
                        self.player.set_xwindow(handle)  # type: ignore[attr-defined]
                    elif wx.Platform == "__WXMAC__":
                        self.player.set_nsobject(int(handle))  # type: ignore[attr-defined]
                    else:
                        self.player.set_hwnd(handle)  # type: ignore[attr-defined]
                    self._have_handle = True
                except Exception as err:
                    LOG.warning("Failed to bind video surface: %s", err)
                    wx.CallLater(int(self._HANDLE_CHECK_INTERVAL * 1000), self._ensure_player_window)

        def _compute_buffer_profile(
            self, url: str, *, bitrate_hint: Optional[float] = None
        ) -> Tuple[float, dict, Optional[float]]:
            bitrate = bitrate_hint if bitrate_hint and bitrate_hint > 0 else self._estimate_stream_bitrate(url)
            base = self.base_buffer_seconds
            is_linear_ts = self._is_linear_ts(url)
            cache_fraction = self._network_cache_fraction
            if is_linear_ts:
                cache_fraction = max(cache_fraction, self._ts_network_bias)

            if bitrate is None:
                raw_target = max(base, 12.0)
                if is_linear_ts:
                    raw_target = max(raw_target, self._ts_buffer_floor)
            elif bitrate <= 1.5:
                raw_target = max(base, 20.0)
            elif bitrate <= 3.0:
                raw_target = max(base, 16.0)
            elif bitrate <= 8.0:
                raw_target = max(base, 12.0)
            elif bitrate <= 25.0:
                raw_target = max(base, 10.0)
            else:
                raw_target = max(8.0, min(base, 12.0))
            if is_linear_ts and bitrate is not None and bitrate <= 12.0:
                raw_target = max(raw_target, self._ts_buffer_floor)

            target = min(self._max_buffer_seconds, max(base, raw_target))
            network_candidate = max(target - 0.75, target * cache_fraction)
            network_target = max(self._min_network_cache_seconds, network_candidate)
            network_target = min(network_target, self._max_network_cache_seconds, target)
            network_ms = int(network_target * 1000)
            file_pad = 6.0 if is_linear_ts else 4.0
            live_pad = 4.0 if is_linear_ts else 2.5
            disc_pad = file_pad
            file_cache_target = max(target + file_pad, network_target + (file_pad + 2.0))
            file_cache = max(4000, int(file_cache_target * 1000))
            demux_cap = 28.0 if is_linear_ts else 24.0
            profile = {
                "network_ms": network_ms,
                "live_ms": int(max(target + live_pad, network_target + (live_pad + 1.5)) * 1000),
                "file_ms": file_cache,
                "disc_ms": max(file_cache, int((target + disc_pad) * 1000)),
                "demux_read_ahead": max(8, int(min(target, demux_cap) * 0.85)),
                "adaptive_cache": True,
            }
            return network_target, profile, bitrate

        def _estimate_stream_bitrate(self, url: str) -> Optional[float]:
            parsed = urllib.parse.urlparse(url)
            query_hint = self._extract_bitrate_from_query(parsed.query)
            if query_hint:
                return query_hint

            lower = url.lower()
            if "m3u8" not in lower and "manifest" not in lower:
                return None

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "IPTVClient/1.0",
                    "Accept": "application/x-mpegURL,application/vnd.apple.mpegurl,text/plain,*/*",
                },
            )
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

            matches = re.findall(r"BANDWIDTH=(\\d+)", text)
            if not matches:
                matches = re.findall(r"AVERAGE-BANDWIDTH=(\\d+)", text)
            if not matches:
                return None

            try:
                highest = max(int(m) for m in matches)
            except Exception:
                return None
            if highest <= 0:
                return None
            return highest / 1_000_000

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

        def _begin_buffer_pause(self, duration: float) -> bool:
            if duration <= 0 or self._destroyed or self._manual_stop:
                return False
            if self._is_paused and not self._buffer_pause_active:
                return False
            now = time.monotonic()
            if self._buffer_pause_active:
                self._buffer_pause_until = max(self._buffer_pause_until, now + duration)
                return True
            try:
                self.player.set_pause(True)
            except Exception:
                return False
            self._buffer_pause_active = True
            self._auto_pausing = True
            self._buffer_pause_until = now + duration
            self._buffer_pause_failures = 0
            self._is_paused = True
            self.play_pause_btn.SetLabel("Resume")
            self._update_status_label("Pre-buffering...")
            wx.CallLater(int(max(duration, 0.1) * 1000), self._maybe_end_buffer_pause)
            return True

        def _maybe_end_buffer_pause(self) -> None:
            if not self._buffer_pause_active:
                return
            remaining = self._buffer_pause_until - time.monotonic()
            if remaining > 0.2:
                wx.CallLater(int(max(remaining, 0.2) * 1000), self._maybe_end_buffer_pause)
                return
            self._cancel_buffer_pause(resume=True)

        def _cancel_buffer_pause(self, resume: bool) -> None:
            if not self._buffer_pause_active:
                return
            self._buffer_pause_active = False
            self._auto_pausing = False
            self._buffer_pause_until = 0.0
            if resume:
                try:
                    self.player.set_pause(False)
                except Exception:
                    pass
                self._is_paused = False
                self.play_pause_btn.SetLabel("Pause")
                self._update_status_label()
            else:
                self._update_status_label("Paused" if self._is_paused else "")

        def _handle_buffer_autopause(self, now: float, buffer_duration: float, since_start: float) -> bool:
            if self._destroyed or self._manual_stop or self._pending_restart:
                return False
            max_pause = min(max(self._last_buffer_seconds, 6.0), 12.0)
            if self._buffer_pause_active:
                if now < self._buffer_pause_until:
                    return True
                self._cancel_buffer_pause(resume=True)
                self._buffer_pause_failures += 1
                if self._buffer_pause_failures >= 2:
                    return False
                longer = min(max_pause * 1.5, 16.0)
                if self._begin_buffer_pause(longer):
                    return True
                return False
            trigger = 2.5 if since_start <= 45.0 else 4.0
            if buffer_duration >= trigger and self._buffer_pause_failures < 3:
                duration = min(max(max_pause, 4.0), 12.0)
                if self._begin_buffer_pause(duration):
                    return True
                self._buffer_pause_failures += 1
            return False

        def _schedule_restart(self, reason: str, adjust_buffer: bool = False) -> None:
            if self._destroyed or not self._current_url or self._manual_stop or self._gave_up:
                return
            self._pending_xtream_refresh = False
            self._cancel_buffer_pause(resume=True)
            self._buffer_pause_failures = 0
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
                    wx.CallAfter(
                        wx.MessageBox,
                        "Stream disconnected after multiple retries. Please try another channel or try again later.",
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
            if self._buffer_pause_active:
                self._cancel_buffer_pause(resume=False)
            try:
                self.player.pause()
                self._is_paused = not self._is_paused
                self.play_pause_btn.SetLabel("Resume" if self._is_paused else "Pause")
            except Exception:
                pass

        def _update_status_label(self, prefix: str = "") -> None:
            bitrate_txt = ""
            if self._last_bitrate_mbps:
                bitrate_txt = f" | ~{self._last_bitrate_mbps:.1f} Mbps"
            buf_txt = f"{self._last_buffer_seconds:.1f}s buffer"
            vol_txt = f" | Vol {self._volume_value}%"
            label = f"{prefix}{(' ' if prefix else '')}{buf_txt}{bitrate_txt}{vol_txt}"
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
                used_autopause = False
                handled_xtream_refresh = False
                if (
                    allow_recovery
                    and xtream_live
                    and not self._pending_restart
                    and buffer_duration >= self._xtream_buffer_refresh_seconds
                ):
                    handled_xtream_refresh = self._restart_expected_xtream_live()
                if allow_recovery and not handled_xtream_refresh and not self._pending_restart:
                    used_autopause = self._handle_buffer_autopause(now, buffer_duration, since_start)
                if allow_recovery and not handled_xtream_refresh and not self._pending_restart and not used_autopause:
                    if (
                        not self._early_buffer_fix_applied
                        and since_start <= 45.0
                        and buffer_duration >= 3.0
                    ):
                        self._early_buffer_fix_applied = True
                        self._schedule_restart("early buffering detected", adjust_buffer=True)
                    elif buffer_duration >= 6.0:
                        self._schedule_restart("prolonged buffering", adjust_buffer=True)
            else:
                if self._buffer_start_ts is not None:
                    self._record_buffer_event(now)
                self._buffer_start_ts = None
                if self._buffer_pause_active and self._auto_pausing:
                    self._cancel_buffer_pause(resume=True)

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

        def _on_key_down(self, event: wx.KeyEvent) -> None:
            key = event.GetKeyCode()
            if key == wx.WXK_TAB:
                self._focus_control_step(not event.ShiftDown())
                return
            if key in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
                self._adjust_volume(+5)
                return
            if key in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
                self._adjust_volume(-5)
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
                    current = self.player.audio_get_volume()
                    if current >= 0:
                        self._volume_value = current
                    self.player.audio_set_volume(self._volume_value)
                except Exception:
                    pass

            wx.CallLater(150, _apply)

        def _adjust_volume(self, delta: int) -> None:
            try:
                current = self.player.audio_get_volume()
            except Exception:
                current = -1
            if current >= 0:
                self._volume_value = current
            self._volume_value = max(0, min(200, self._volume_value + delta))
            try:
                self.player.audio_set_volume(self._volume_value)
            except Exception:
                pass
            self._update_status_label()

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

        def _on_close(self, event: wx.CloseEvent) -> None:
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

    if isinstance(getattr(_ip, "__all__", None), list):
        exported = set(_ip.__all__)
        exported.add("InternalPlayerFrame")
        _ip.__all__ = list(exported)

    _ip.InternalPlayerFrame = PatchedInternalPlayerFrame  # type: ignore[attr-defined]
    _ip._prepare_vlc_runtime = _prepare_vlc_runtime  # type: ignore[attr-defined]
    _ip._VLC_RUNTIME_PREPARED = False  # type: ignore[attr-defined]

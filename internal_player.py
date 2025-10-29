
import logging
import platform
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable, Deque, Optional, Tuple

import wx

try:
    import vlc  # type: ignore
except Exception as _err:  # pragma: no cover - import guard
    vlc = None  # type: ignore
    _VLC_IMPORT_ERROR = _err
else:
    _VLC_IMPORT_ERROR = None

LOG = logging.getLogger(__name__)


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


class InternalPlayerFrame(wx.Frame):
    """Embedded IPTV player with buffering resilience and keyboard controls."""

    _HANDLE_CHECK_INTERVAL = 0.1

    def __init__(
        self,
        parent: Optional[wx.Window],
        base_buffer_seconds: float = 12.0,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        _prepare_vlc_runtime()
        if vlc is None:
            raise InternalPlayerUnavailableError("python-vlc (libVLC) is not available.")
        super().__init__(parent, title="Built-in IPTV Player", size=(960, 540))
        self._on_close_cb = on_close
        self.base_buffer_seconds = max(6.0, float(base_buffer_seconds or 0.0))
        self._last_buffer_seconds: float = self.base_buffer_seconds
        self._last_bitrate_mbps: Optional[float] = None
        self._current_url: Optional[str] = None
        self._current_title: str = ""
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
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 4
        self._last_restart_ts = 0.0
        self._last_restart_reason = ""
        self._buffer_step_seconds = 2.0
        self._max_buffer_seconds = 18.0
        self._max_network_cache_seconds = 8.0
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
        self._end_near_threshold_ms = 10_000
        self._buffer_start_ts: Optional[float] = None
        self._early_buffer_fix_applied = False
        self._has_seen_playing = False

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
    def play(self, url: str, title: Optional[str] = None, *, _retry: bool = False) -> None:
        """Start playback of the given URL with buffering and recovery hooks."""
        if self._destroyed:
            raise InternalPlayerUnavailableError("Player window has been destroyed.")
        if not url:
            raise InternalPlayerUnavailableError("No stream URL provided.")

        if _retry:
            self._manual_stop = False
        else:
            self._reconnect_attempts = 0
            self._last_restart_reason = ""
            self._gave_up = False

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

        self.SetTitle(f"{self._current_title} - Built-in Player")
        target_buffer, cache_profile, bitrate = self._compute_buffer_profile(url)
        self._last_buffer_seconds = target_buffer
        self._last_bitrate_mbps = bitrate

        media = self.instance.media_new(url)
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
        self.base_buffer_seconds = max(6.0, seconds)

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

    def _compute_buffer_profile(self, url: str) -> Tuple[float, dict, Optional[float]]:
        bitrate = self._estimate_stream_bitrate(url)
        base = self.base_buffer_seconds

        if bitrate is None:
            raw_target = max(base, 12.0)
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

        target = min(raw_target, self._max_buffer_seconds)
        network_target = min(target, self._max_network_cache_seconds)
        network_ms = int(network_target * 1000)
        file_cache = max(2000, int(max(target / 2.0, 3.0) * 1000))
        profile = {
            "network_ms": network_ms,
            "live_ms": int((max(network_target, target) + 2.0) * 1000),
            "file_ms": file_cache,
            "disc_ms": file_cache,
            "demux_read_ahead": max(4, int(target / 2.0)),
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
        now = time.monotonic()
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
            try:
                self.play(self._current_url, self._current_title, _retry=True)
            except Exception as err:
                LOG.error("Stream restart failed: %s", err)

        wx.CallLater(400, _do_restart)

    def _prune_buffer_events(self, now: float) -> None:
        while self._buffering_events and now - self._buffering_events[0] > self._choppy_window_seconds:
            self._buffering_events.popleft()

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
            if state_key == "buffering":
                self._buffering_events.append(now)
                self._maybe_handle_choppy(now)
            self._last_state_name = state_key
            if state_key == "playing":
                self._has_seen_playing = True
                self._stall_ticks = 0
            elif state_key != "playing":
                self._stall_ticks = 0
        elif state_key == "buffering":
            self._maybe_handle_choppy(now)

        self._monitor_playback_progress(now, state_key)

        if state_key == "buffering":
            if self._buffer_start_ts is None:
                self._buffer_start_ts = now
            buffer_duration = now - self._buffer_start_ts
            since_start = now - self._play_start_monotonic if self._play_start_monotonic else float("inf")
            allow_recovery = self._has_seen_playing
            if allow_recovery and (
                not self._pending_restart
                and not self._early_buffer_fix_applied
                and since_start <= 45.0
                and buffer_duration >= 3.0
            ):
                self._early_buffer_fix_applied = True
                self._schedule_restart("early buffering detected", adjust_buffer=True)
            elif allow_recovery and not self._pending_restart and buffer_duration >= 6.0:
                self._schedule_restart("prolonged buffering", adjust_buffer=True)
        else:
            self._buffer_start_ts = None

        prefix = "Paused" if self._is_paused else state_name.capitalize()

        if state_key == "error":
            self._schedule_restart("playback error", adjust_buffer=True)
            prefix = "Reconnecting..."
        elif state_key == "stopped":
            if not self._manual_stop:
                self._schedule_restart("stream stopped unexpectedly")
                prefix = "Reconnecting..."
        elif state_key == "ended":
            if self._should_auto_recover_on_end():
                self._schedule_restart("stream ended unexpectedly")
                prefix = "Reconnecting..."
            else:
                self.stop(manual=False)
                prefix = "Ended"
        elif state_key == "buffering":
            prefix = "Buffering..."

        if self._pending_restart:
            prefix = "Reconnecting..."
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


__all__ = [
    "InternalPlayerFrame",
    "InternalPlayerUnavailableError",
    "_VLC_IMPORT_ERROR",
]

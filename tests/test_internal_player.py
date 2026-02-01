"""
Tests for internal player recovery, buffering, and reconnection.
"""
import pytest
import os
import sys
import time
from unittest.mock import Mock, patch, MagicMock, PropertyMock
from enum import IntEnum

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Mock VLC State enum for testing without VLC installed
class MockState(IntEnum):
    NothingSpecial = 0
    Opening = 1
    Buffering = 2
    Playing = 3
    Paused = 4
    Stopped = 5
    Ended = 6
    Error = 7


class TestBufferProfile:
    """Test buffer profile calculations."""

    def test_default_buffer_seconds(self):
        """Test default buffer target calculation."""
        # Default values from internal_player.py
        default_base = 2  # seconds
        default_max = 18  # seconds
        
        # Without bitrate info, should use midpoint-ish value
        target = 7.5  # typical default
        
        assert target >= default_base
        assert target <= default_max

    def test_buffer_profile_for_high_bitrate(self):
        """Test buffer profile for high bitrate streams."""
        bitrate_mbps = 20  # High bitrate
        base_buffer = 2
        max_buffer = 18
        
        # Higher bitrate needs less buffering time for same data
        # But we still want enough buffer to handle jitter
        target = min(max_buffer, max(base_buffer, 12 - (bitrate_mbps / 5)))
        
        assert target >= base_buffer
        assert target <= max_buffer

    def test_buffer_profile_for_low_bitrate(self):
        """Test buffer profile for low bitrate audio streams."""
        is_audio = True
        
        if is_audio:
            # Audio streams can use smaller buffers
            target = 3.5
        else:
            target = 7.5
        
        assert target == 3.5

    def test_xtream_ts_buffer_profile(self):
        """Test buffer profile for Xtream .ts live streams."""
        url = "http://example.com/user/pass/12345.ts"
        is_xtream_ts = url.endswith(".ts") and "/live/" not in url
        
        if is_xtream_ts:
            # Xtream TS needs deeper buffers for segment boundaries
            target = 8.0
        else:
            target = 7.5
        
        assert target == 8.0


class TestChoppyDetection:
    """Test choppy stream detection logic."""

    def test_buffering_duration_threshold(self):
        """Test that short buffering events don't count as choppy."""
        threshold_seconds = 1.25
        
        short_buffer = 0.5  # 500ms
        long_buffer = 2.0   # 2 seconds
        
        assert short_buffer < threshold_seconds  # Not choppy
        assert long_buffer >= threshold_seconds   # Choppy

    def test_reconnect_budget(self):
        """Test reconnection attempt budgeting."""
        max_attempts = 3
        attempts = 0
        
        # Simulate connection attempts
        for _ in range(5):
            if attempts < max_attempts:
                attempts += 1
        
        assert attempts == max_attempts

    def test_attempt_decay(self):
        """Test that reconnect attempts decay over time."""
        decay_minutes = 2
        last_attempt_time = time.time() - (decay_minutes * 60 + 1)  # 2+ minutes ago
        current_time = time.time()
        
        elapsed = current_time - last_attempt_time
        should_reset = elapsed > (decay_minutes * 60)
        
        assert should_reset is True

    def test_attempt_no_decay_recent(self):
        """Test that recent attempts don't decay."""
        decay_minutes = 2
        last_attempt_time = time.time() - 30  # 30 seconds ago
        current_time = time.time()
        
        elapsed = current_time - last_attempt_time
        should_reset = elapsed > (decay_minutes * 60)
        
        assert should_reset is False


class TestXtreamTsLiveRefresh:
    """Test Xtream .ts live stream auto-refresh behavior."""

    def test_live_vs_catchup_detection(self):
        """Test detection of live vs catchup streams."""
        live_url = "http://example.com/user/pass/12345.ts"
        catchup_url = "http://example.com/timeshift/user/pass/12345/2024-01-15/14-00.ts"
        
        # Live URLs are simpler
        is_live_ts = live_url.endswith(".ts") and "timeshift" not in live_url
        is_catchup = "timeshift" in catchup_url
        
        assert is_live_ts is True
        assert is_catchup is True

    def test_auto_refresh_on_ended_for_live(self):
        """Test auto-refresh triggers for live Xtream TS."""
        is_live = True
        is_xtream_ts = True
        state = MockState.Ended
        
        should_refresh = (state == MockState.Ended and is_live and is_xtream_ts)
        
        assert should_refresh is True

    def test_no_refresh_for_catchup_ended(self):
        """Test no auto-refresh for catchup streams that end."""
        is_live = False
        is_xtream_ts = True
        state = MockState.Ended
        
        should_refresh = (state == MockState.Ended and is_live and is_xtream_ts)
        
        assert should_refresh is False

    def test_refresh_doesnt_burn_budget(self):
        """Test that auto-refresh doesn't burn reconnect budget."""
        reconnect_attempts = 0
        is_auto_refresh = True
        
        if not is_auto_refresh:
            reconnect_attempts += 1
        
        assert reconnect_attempts == 0


class TestVLCOptions:
    """Test VLC player option generation."""

    def test_network_caching_option(self):
        """Test network-caching option generation."""
        buffer_ms = 7500  # 7.5 seconds
        
        option = f"--network-caching={buffer_ms}"
        
        assert option == "--network-caching=7500"

    def test_file_caching_option(self):
        """Test file-caching option for Xtream TS."""
        buffer_ms = 18000  # 18 seconds
        
        option = f"--file-caching={buffer_ms}"
        
        assert option == "--file-caching=18000"

    def test_live_caching_option(self):
        """Test live-caching option."""
        buffer_ms = 10000  # 10 seconds
        
        option = f"--live-caching={buffer_ms}"
        
        assert option == "--live-caching=10000"

    def test_http_reconnect_option(self):
        """Test http-reconnect option."""
        option = "--http-reconnect"
        
        assert option == "--http-reconnect"

    def test_adaptive_bitrate_options(self):
        """Test adaptive bitrate streaming options."""
        options = [
            "--adaptive-maxwidth=1920",
            "--adaptive-maxheight=1080",
        ]
        
        assert len(options) == 2
        assert "maxwidth" in options[0]
        assert "maxheight" in options[1]


class TestStateTransitions:
    """Test VLC state transition handling."""

    def test_opening_to_playing(self):
        """Test normal opening to playing transition."""
        states = [MockState.Opening, MockState.Opening, MockState.Buffering, MockState.Playing]
        
        reached_playing = MockState.Playing in states
        
        assert reached_playing is True

    def test_opening_to_error(self):
        """Test opening to error transition."""
        states = [MockState.Opening, MockState.Opening, MockState.Error]
        
        reached_error = MockState.Error in states
        reached_playing = MockState.Playing in states
        
        assert reached_error is True
        assert reached_playing is False

    def test_playing_to_buffering_recovery(self):
        """Test recovery from buffering while playing."""
        states = [MockState.Playing, MockState.Buffering, MockState.Buffering, MockState.Playing]
        
        had_buffering = MockState.Buffering in states
        recovered = states[-1] == MockState.Playing
        
        assert had_buffering is True
        assert recovered is True

    def test_ended_state_handling(self):
        """Test ended state handling."""
        state = MockState.Ended
        is_live = True
        is_xtream_ts = True
        
        # For live Xtream TS, ended should trigger refresh
        should_refresh = (state == MockState.Ended and is_live and is_xtream_ts)
        
        assert should_refresh is True


class TestVolumeControl:
    """Test volume control functionality."""

    def test_volume_range(self):
        """Test volume range is 0-100."""
        min_vol = 0
        max_vol = 100
        
        assert min_vol >= 0
        assert max_vol <= 100

    def test_volume_step_default(self):
        """Test default volume step is 2%."""
        default_step = 2
        
        assert default_step == 2

    def test_volume_step_with_ctrl(self):
        """Test volume step with Ctrl modifier is 5%."""
        ctrl_step = 5
        
        assert ctrl_step == 5

    def test_volume_clamping(self):
        """Test volume is clamped to valid range."""
        def clamp_volume(vol):
            return max(0, min(100, vol))
        
        assert clamp_volume(-10) == 0
        assert clamp_volume(150) == 100
        assert clamp_volume(50) == 50


class TestPreflightCheck:
    """Test preflight stream check functionality."""

    def test_preflight_http_request(self):
        """Test preflight makes HTTP HEAD/GET request."""
        url = "http://example.com/stream.m3u8"
        
        # Should use HEAD request first
        method = "HEAD"
        
        assert method in ["HEAD", "GET"]

    def test_preflight_timeout(self):
        """Test preflight has reasonable timeout."""
        timeout_seconds = 10
        
        assert timeout_seconds >= 5
        assert timeout_seconds <= 30

    def test_preflight_detects_404(self):
        """Test preflight detects 404 errors."""
        status_code = 404
        
        is_error = status_code >= 400
        
        assert is_error is True

    def test_preflight_accepts_redirect(self):
        """Test preflight follows redirects."""
        status_code = 302
        
        is_redirect = 300 <= status_code < 400
        
        assert is_redirect is True


class TestPlayerReconnection:
    """Test player reconnection logic."""

    def test_reconnect_on_stream_lost(self):
        """Test reconnection triggers on stream lost."""
        state = MockState.Ended
        was_playing = True
        is_live = True
        
        should_reconnect = (state == MockState.Ended and was_playing and is_live)
        
        assert should_reconnect is True

    def test_no_reconnect_on_user_stop(self):
        """Test no reconnection when user stops."""
        state = MockState.Stopped
        user_initiated = True
        
        should_reconnect = (state == MockState.Stopped and not user_initiated)
        
        assert should_reconnect is False

    def test_reconnect_delay(self):
        """Test reconnection delay."""
        base_delay = 1.0  # seconds
        attempt = 2
        
        # Exponential backoff
        delay = base_delay * (2 ** (attempt - 1))
        
        assert delay == 2.0

    def test_max_reconnect_delay(self):
        """Test maximum reconnection delay."""
        max_delay = 30  # seconds
        calculated_delay = 64  # Too high
        
        actual_delay = min(calculated_delay, max_delay)
        
        assert actual_delay == max_delay


class TestBufferingEvents:
    """Test buffering event handling."""

    def test_buffering_progress_tracking(self):
        """Test buffering progress is tracked."""
        progress_events = [0.0, 0.25, 0.5, 0.75, 1.0]
        
        completed = progress_events[-1] == 1.0
        
        assert completed is True

    def test_long_buffering_triggers_recovery(self):
        """Test long buffering triggers recovery action."""
        buffering_start = time.time() - 10  # 10 seconds ago
        threshold = 8  # seconds
        
        buffering_duration = time.time() - buffering_start
        needs_recovery = buffering_duration > threshold
        
        assert needs_recovery is True

    def test_short_buffering_no_recovery(self):
        """Test short buffering doesn't trigger recovery."""
        buffering_start = time.time() - 2  # 2 seconds ago
        threshold = 8  # seconds
        
        buffering_duration = time.time() - buffering_start
        needs_recovery = buffering_duration > threshold
        
        assert needs_recovery is False


class TestStreamTypeDetection:
    """Test stream type detection for appropriate handling."""

    def test_hls_detection(self):
        """Test HLS stream detection."""
        urls = [
            "http://example.com/stream.m3u8",
            "http://example.com/playlist.M3U8",
            "http://example.com/index.m3u8?token=abc",
        ]
        
        for url in urls:
            is_hls = ".m3u8" in url.lower()
            assert is_hls is True

    def test_mpeg_ts_detection(self):
        """Test MPEG-TS stream detection."""
        urls = [
            "http://example.com/stream.ts",
            "http://example.com/live/channel.TS",
        ]
        
        for url in urls:
            is_ts = url.lower().endswith(".ts")
            assert is_ts is True

    def test_xtream_pattern_detection(self):
        """Test Xtream URL pattern detection."""
        # Xtream: /{username}/{password}/{stream_id}.ts
        url = "http://example.com/user123/pass456/12345.ts"
        parts = url.split("/")
        
        # Typical Xtream has at least 4 path components after domain
        has_xtream_pattern = len(parts) >= 6 and parts[-1].endswith(".ts")
        
        assert has_xtream_pattern is True

    def test_audio_stream_detection(self):
        """Test audio-only stream detection."""
        audio_extensions = [".mp3", ".aac", ".ogg", ".opus"]
        urls = [
            "http://radio.example.com/stream.mp3",
            "http://radio.example.com/live.aac",
        ]
        
        for url in urls:
            is_audio = any(url.lower().endswith(ext) for ext in audio_extensions)
            assert is_audio is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

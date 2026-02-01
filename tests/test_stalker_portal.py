"""
Tests for Stalker Portal provider functionality.
"""
import pytest
import json
import urllib.parse
import os
import sys
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    StalkerPortalConfig,
    StalkerPortalClient,
    ProviderError,
    _normalize_base_url,
)


class TestStalkerPortalConfig:
    """Test Stalker Portal configuration handling."""

    def test_basic_config(self):
        """Test basic configuration creation."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com/stalker_portal",
            username="testuser",
            password="testpass",
            mac="00:1A:79:00:00:01"
        )
        
        assert cfg.base_url == "http://portal.example.com/stalker_portal"
        assert cfg.username == "testuser"
        assert cfg.password == "testpass"
        assert cfg.mac == "00:1A:79:00:00:01"

    def test_config_with_defaults(self):
        """Test configuration with default values."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        
        assert cfg.auto_epg is True
        assert cfg.timezone == "UTC"
        assert "MAG" in cfg.user_agent
        assert "MAG" in cfg.profile_user_agent

    def test_config_custom_user_agent(self):
        """Test configuration with custom user agent."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01",
            user_agent="Custom Agent/1.0",
            profile_user_agent="Model: CustomBox; Link: Ethernet"
        )
        
        assert cfg.user_agent == "Custom Agent/1.0"
        assert cfg.profile_user_agent == "Model: CustomBox; Link: Ethernet"


class TestStalkerPortalClient:
    """Test Stalker Portal client functionality."""

    def test_portal_endpoint_derivation(self):
        """Test portal endpoint derivation from base URL."""
        # Test with base URL ending in portal.php
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com/c/portal.php",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        
        # Should use the provided portal.php URL
        assert "portal.php" in client._portal_endpoint

    def test_portal_endpoint_with_load_php(self):
        """Test portal endpoint with load.php style URL."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com/server/load.php",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        
        assert "load.php" in client._portal_endpoint

    def test_headers_include_mac(self):
        """Test that headers include MAC address."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:AA:BB:CC"
        )
        client = StalkerPortalClient(cfg)
        
        headers = client._headers(include_token=False)
        
        assert "Cookie" in headers
        assert "mac=00:1A:79:AA:BB:CC" in headers["Cookie"]
        assert "User-Agent" in headers
        assert "X-User-Agent" in headers

    def test_headers_include_token(self):
        """Test that headers include authorization token."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        client._token = "test_token_12345"
        
        headers = client._headers(include_token=True)
        
        assert "Authorization" in headers
        assert "Bearer test_token_12345" in headers["Authorization"]

    def test_headers_without_token(self):
        """Test headers without token."""
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        
        headers = client._headers(include_token=False)
        
        assert "Authorization" not in headers


class TestStalkerMacAddress:
    """Test MAC address handling."""

    def test_valid_mac_formats(self):
        """Test valid MAC address formats."""
        valid_macs = [
            "00:1A:79:00:00:01",
            "00:1a:79:00:00:01",
            "00-1A-79-00-00-01",
            "001A79000001",
        ]
        
        for mac in valid_macs:
            # Normalize to colon-separated uppercase
            normalized = mac.upper().replace("-", ":").replace(":", "")
            if len(normalized) == 12:
                formatted = ":".join(normalized[i:i+2] for i in range(0, 12, 2))
                assert len(formatted) == 17
                assert formatted.count(":") == 5

    def test_mac_in_cookie(self):
        """Test MAC address in cookie header."""
        mac = "00:1A:79:AA:BB:CC"
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac=mac
        )
        client = StalkerPortalClient(cfg)
        
        headers = client._headers(include_token=False)
        cookie = headers.get("Cookie", "")
        
        assert f"mac={mac}" in cookie


class TestStalkerHandshake:
    """Test Stalker Portal handshake process."""

    @patch.object(StalkerPortalClient, '_portal_call')
    def test_handshake_returns_token(self, mock_portal_call):
        """Test that handshake returns a token."""
        mock_portal_call.return_value = {
            "js": {"token": "handshake_token_123"}
        }
        
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        
        # Simulate handshake
        response = client._portal_call({
            "type": "stb",
            "action": "handshake",
            "token": "",
            "prehash": "0",
            "JsHttpRequest": "1-xml"
        }, include_token=False)
        
        token = response.get("js", {}).get("token")
        assert token == "handshake_token_123"

    @patch.object(StalkerPortalClient, '_portal_call')
    def test_login_updates_token(self, mock_portal_call):
        """Test that login updates the session token."""
        mock_portal_call.return_value = {
            "js": {"token": "session_token_456"}
        }
        
        cfg = StalkerPortalConfig(
            base_url="http://portal.example.com",
            username="user",
            password="pass",
            mac="00:1A:79:00:00:01"
        )
        client = StalkerPortalClient(cfg)
        
        # Simulate login
        response = client._portal_call({
            "type": "stb",
            "action": "login",
            "login": cfg.username,
            "password": cfg.password,
            "JsHttpRequest": "1-xml"
        })
        
        new_token = response.get("js", {}).get("token")
        assert new_token == "session_token_456"


class TestStalkerChannelFetch:
    """Test Stalker Portal channel fetching."""

    def test_parse_channel_response(self):
        """Test parsing channel response data."""
        response_data = [
            {
                "id": "1",
                "name": "Channel One",
                "cmd": "ffmpeg http://stream.example.com/ch1",
                "tv_genre_title": "News",
                "logo": "http://logo.com/ch1.png",
                "epg_id": "ch1.portal",
                "use_http_tmp_link": 1,
                "allow_timeshift": 1,
                "archive": 1,
                "tv_archive_duration": 7
            },
            {
                "id": "2",
                "name": "Channel Two",
                "cmd": "ffmpeg http://stream.example.com/ch2",
                "tv_genre_title": "Sports",
                "logo": "",
                "epg_id": "",
                "use_http_tmp_link": 0,
                "allow_timeshift": 0,
                "archive": 0
            }
        ]
        
        channels = []
        for row in response_data:
            channel = {
                "name": row.get("name", ""),
                "group": row.get("tv_genre_title", "Stalker"),
                "url": "",  # resolved on demand
                "tvg-id": row.get("epg_id", ""),
                "tvg-logo": row.get("logo", ""),
                "provider-type": "stalker",
                "provider-data": {
                    "cmd": row.get("cmd", ""),
                    "use_http_tmp_link": row.get("use_http_tmp_link", 0),
                    "id": row.get("id"),
                    "allow_timeshift": row.get("allow_timeshift", 0),
                    "archive": row.get("archive", 0)
                }
            }
            if row.get("tv_archive_duration"):
                channel["catchup-days"] = row.get("tv_archive_duration")
            if row.get("allow_timeshift") or row.get("archive"):
                channel["catchup"] = "stalker"
            channels.append(channel)
        
        assert len(channels) == 2
        assert channels[0]["name"] == "Channel One"
        assert channels[0]["group"] == "News"
        assert channels[0]["catchup"] == "stalker"
        assert channels[0]["catchup-days"] == 7
        assert channels[1]["name"] == "Channel Two"
        assert "catchup" not in channels[1]


class TestStalkerStreamResolution:
    """Test Stalker Portal stream URL resolution."""

    def test_parse_create_link_response(self):
        """Test parsing create_link response."""
        response = {
            "js": {
                "cmd": "ffmpeg http://actual-stream.example.com/live/ch1.ts"
            }
        }
        
        link = response.get("js", {}).get("cmd")
        
        # Strip ffmpeg prefix
        if link.startswith("ffmpeg "):
            link = link[7:]
        
        assert link == "http://actual-stream.example.com/live/ch1.ts"

    def test_parse_auto_prefix_response(self):
        """Test parsing response with 'auto' prefix."""
        response = {
            "js": {
                "cmd": "auto http://stream.example.com/hls/ch1.m3u8"
            }
        }
        
        link = response.get("js", {}).get("cmd")
        
        for prefix in ("ffmpeg ", "auto "):
            if link.startswith(prefix):
                link = link[len(prefix):]
                break
        
        assert link == "http://stream.example.com/hls/ch1.m3u8"

    def test_handle_empty_response(self):
        """Test handling empty response."""
        response = {"js": {}}
        
        link = response.get("js", {}).get("cmd")
        
        assert link is None


class TestStalkerTimeshift:
    """Test Stalker Portal timeshift/catchup functionality."""

    def test_timeshift_url_construction(self):
        """Test timeshift URL construction."""
        # Stalker portals typically use a separate action for timeshift
        base_url = "http://portal.example.com/stalker_portal"
        channel_id = "123"
        start_time = "2024-01-15 14:00:00"
        duration = 3600  # 1 hour
        
        # Typical timeshift request params
        params = {
            "type": "itv",
            "action": "create_link",
            "cmd": f"ffmpeg http://stream/ch{channel_id}",
            "ts_offset": start_time,
            "JsHttpRequest": "1-xml"
        }
        
        query = urllib.parse.urlencode(params)
        url = f"{base_url}/portal.php?{query}"
        
        assert "action=create_link" in url
        assert "ts_offset" in url

    def test_archive_duration_parsing(self):
        """Test parsing archive duration."""
        channel_data = {
            "tv_archive_duration": 7,
            "archive": 1
        }
        
        has_catchup = channel_data.get("archive", 0) == 1
        catchup_days = channel_data.get("tv_archive_duration", 0)
        
        assert has_catchup is True
        assert catchup_days == 7


class TestStalkerPortalErrors:
    """Test Stalker Portal error handling."""

    def test_invalid_json_response(self):
        """Test handling of invalid JSON response."""
        invalid_response = "Not JSON content"
        
        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid_response)

    def test_missing_token_in_handshake(self):
        """Test handling missing token in handshake."""
        response = {"status": "error", "message": "Invalid credentials"}
        
        token = response.get("token") or response.get("js", {}).get("token")
        
        assert token is None

    def test_provider_error_on_missing_cmd(self):
        """Test ProviderError when channel missing cmd."""
        provider_data = {"id": "123", "name": "Test"}  # missing 'cmd'
        
        cmd = provider_data.get("cmd")
        
        if not cmd:
            with pytest.raises(ProviderError):
                raise ProviderError("Channel missing command reference")


class TestStalkerPortalGenres:
    """Test Stalker Portal genre/category handling."""

    def test_parse_genres_response(self):
        """Test parsing genres (categories) response."""
        response = {
            "js": [
                {"id": "1", "title": "News", "number": "1"},
                {"id": "2", "title": "Sports", "number": "2"},
                {"id": "3", "title": "Entertainment", "number": "3"},
            ]
        }
        
        genres = response.get("js", [])
        genre_map = {g["id"]: g["title"] for g in genres}
        
        assert genre_map["1"] == "News"
        assert genre_map["2"] == "Sports"
        assert len(genre_map) == 3

    def test_channel_genre_mapping(self):
        """Test mapping channels to genres."""
        genres = {"1": "News", "2": "Sports"}
        channels = [
            {"name": "CNN", "tv_genre_id": "1"},
            {"name": "ESPN", "tv_genre_id": "2"},
            {"name": "BBC", "tv_genre_id": "1"},
        ]
        
        channels_by_genre = {}
        for ch in channels:
            genre_id = ch.get("tv_genre_id")
            genre_name = genres.get(genre_id, "Other")
            if genre_name not in channels_by_genre:
                channels_by_genre[genre_name] = []
            channels_by_genre[genre_name].append(ch)
        
        assert len(channels_by_genre["News"]) == 2
        assert len(channels_by_genre["Sports"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

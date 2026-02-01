"""
Tests for XtreamCodes provider functionality.
"""
import pytest
import json
import urllib.parse
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    XtreamCodesConfig,
    XtreamCodesClient,
    ProviderError,
    _normalize_base_url,
)


class TestXtreamCodesConfig:
    """Test XtreamCodes configuration handling."""

    def test_basic_config(self):
        """Test basic configuration creation."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com:8080",
            username="testuser",
            password="testpass"
        )
        
        assert cfg.base_url == "http://provider.com:8080"
        assert cfg.username == "testuser"
        assert cfg.password == "testpass"
        assert cfg.stream_type == "m3u_plus"  # default
        assert cfg.output == "ts"  # default

    def test_config_with_options(self):
        """Test configuration with custom options."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass",
            stream_type="m3u_plus",
            output="hls",
            name="My Provider",
            auto_epg=False
        )
        
        assert cfg.output == "hls"
        assert cfg.name == "My Provider"
        assert cfg.auto_epg is False

    def test_config_with_provider_id(self):
        """Test configuration with provider ID."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass",
            provider_id="my-provider-123"
        )
        
        assert cfg.provider_id == "my-provider-123"


class TestXtreamCodesClient:
    """Test XtreamCodes client functionality."""

    def test_playlist_url_generation(self):
        """Test playlist URL generation."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com:8080",
            username="testuser",
            password="testpass"
        )
        client = XtreamCodesClient(cfg)
        
        url = client.playlist_url()
        
        assert "http://provider.com:8080/get.php" in url
        assert "username=testuser" in url
        assert "password=testpass" in url
        assert "type=m3u_plus" in url
        assert "output=ts" in url

    def test_playlist_url_with_hls_output(self):
        """Test playlist URL with HLS output."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass",
            output="hls"
        )
        client = XtreamCodesClient(cfg)
        
        url = client.playlist_url()
        assert "output=hls" in url

    def test_epg_url_generation(self):
        """Test EPG URL generation."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com:8080",
            username="testuser",
            password="testpass",
            auto_epg=True
        )
        client = XtreamCodesClient(cfg)
        
        epg_urls = client.epg_urls()
        
        assert len(epg_urls) == 1
        assert "xmltv.php" in epg_urls[0]
        assert "username=testuser" in epg_urls[0]
        assert "password=testpass" in epg_urls[0]

    def test_epg_url_disabled(self):
        """Test EPG URL when auto_epg is disabled."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass",
            auto_epg=False
        )
        client = XtreamCodesClient(cfg)
        
        epg_urls = client.epg_urls()
        assert len(epg_urls) == 0

    def test_describe(self):
        """Test provider description."""
        cfg = XtreamCodesConfig(
            base_url="http://myprovider.com",
            username="user",
            password="pass",
            name="My IPTV Provider"
        )
        client = XtreamCodesClient(cfg)
        
        desc = client.describe()
        assert "Xtream Codes" in desc
        assert "My IPTV Provider" in desc

    def test_describe_without_name(self):
        """Test provider description without custom name."""
        cfg = XtreamCodesConfig(
            base_url="http://myprovider.com:8080",
            username="user",
            password="pass"
        )
        client = XtreamCodesClient(cfg)
        
        desc = client.describe()
        assert "myprovider.com" in desc

    @patch('urllib.request.urlopen')
    def test_fetch_playlist_success(self, mock_urlopen):
        """Test successful playlist fetch."""
        mock_response = Mock()
        mock_response.read.return_value = b'#EXTM3U\n#EXTINF:-1,Test Channel\nhttp://stream/test'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass"
        )
        client = XtreamCodesClient(cfg)
        
        playlist = client.fetch_playlist()
        
        assert "#EXTM3U" in playlist
        assert "Test Channel" in playlist

    @patch('urllib.request.urlopen')
    def test_fetch_playlist_latin1_encoding(self, mock_urlopen):
        """Test playlist fetch with Latin-1 encoded content."""
        # Content that's not valid UTF-8
        content = b'#EXTM3U\n#EXTINF:-1,Cha\xeene Fran\xe7aise\nhttp://stream/fr'
        mock_response = Mock()
        mock_response.read.return_value = content
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user",
            password="pass"
        )
        client = XtreamCodesClient(cfg)
        
        playlist = client.fetch_playlist()
        
        assert "#EXTM3U" in playlist
        # Should handle the encoding gracefully
        assert "http://stream/fr" in playlist


class TestBaseUrlNormalization:
    """Test base URL normalization."""

    def test_normalize_simple_url(self):
        """Test normalizing a simple URL."""
        result = _normalize_base_url("http://provider.com")
        assert result == "http://provider.com"

    def test_normalize_url_with_port(self):
        """Test normalizing URL with port."""
        result = _normalize_base_url("http://provider.com:8080")
        assert result == "http://provider.com:8080"

    def test_normalize_url_trailing_slash(self):
        """Test normalizing URL with trailing slash."""
        result = _normalize_base_url("http://provider.com/")
        assert result == "http://provider.com"

    def test_normalize_url_with_path(self):
        """Test normalizing URL with path."""
        result = _normalize_base_url("http://provider.com/stalker_portal")
        assert "provider.com" in result
        assert "stalker_portal" in result

    def test_normalize_url_without_scheme(self):
        """Test normalizing URL without scheme."""
        # Note: URLs with port like "host:8080" are ambiguous - urlparse thinks port is scheme
        # Test with clean hostname first
        result = _normalize_base_url("provider.example.com")
        assert result.startswith("http://")
        assert "provider.example.com" in result

    def test_normalize_empty_url(self):
        """Test normalizing empty URL."""
        result = _normalize_base_url("")
        assert result == ""

    def test_normalize_url_with_portal_php(self):
        """Test normalizing URL ending with portal.php."""
        result = _normalize_base_url("http://provider.com/stalker_portal/portal.php")
        # Should strip portal.php but keep the directory
        assert "portal.php" not in result or result.endswith("stalker_portal")


class TestXtreamUrlPatterns:
    """Test Xtream-style URL patterns."""

    def test_live_stream_url_pattern(self):
        """Test live stream URL pattern."""
        url = "http://provider.com/live/user/pass/12345.ts"
        
        # Parse the URL
        parsed = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]
        
        assert path_parts[0] == "live"
        assert path_parts[-1].endswith(".ts")

    def test_vod_stream_url_pattern(self):
        """Test VOD stream URL pattern."""
        url = "http://provider.com/movie/user/pass/12345.mp4"
        
        parsed = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]
        
        assert path_parts[0] == "movie"
        assert path_parts[-1].endswith(".mp4")

    def test_series_stream_url_pattern(self):
        """Test series stream URL pattern."""
        url = "http://provider.com/series/user/pass/12345.mp4"
        
        parsed = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]
        
        assert path_parts[0] == "series"

    def test_hls_stream_url_pattern(self):
        """Test HLS stream URL pattern."""
        url = "http://provider.com/live/user/pass/12345.m3u8"
        
        assert ".m3u8" in url
        
        parsed = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]
        
        assert path_parts[0] == "live"
        assert path_parts[-1].endswith(".m3u8")

    def test_detect_xtream_live_ts(self):
        """Test detection of Xtream live TS URLs."""
        live_urls = [
            "http://provider.com/user/pass/12345",
            "http://provider.com/live/user/pass/12345.ts",
            "http://provider.com:8080/user/pass/999999",
        ]
        
        non_live_urls = [
            "http://provider.com/movie/user/pass/12345.mp4",
            "http://provider.com/series/user/pass/12345.mp4",
            "http://provider.com/timeshift/user/pass/12345",
        ]
        
        for url in live_urls:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.lower()
            # Live streams don't have movie/series/timeshift in path
            is_live = not any(x in path for x in ['/movie/', '/series/', '/timeshift/'])
            assert is_live, f"Expected {url} to be detected as live"
        
        for url in non_live_urls:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.lower()
            is_live = not any(x in path for x in ['/movie/', '/series/', '/timeshift/'])
            assert not is_live, f"Expected {url} to NOT be detected as live"


class TestXtreamApiResponses:
    """Test handling of Xtream API responses."""

    def test_parse_category_response(self):
        """Test parsing category/group response."""
        response = [
            {"category_id": "1", "category_name": "USA News", "parent_id": 0},
            {"category_id": "2", "category_name": "Sports", "parent_id": 0},
            {"category_id": "3", "category_name": "Movies", "parent_id": 0},
        ]
        
        categories = {cat["category_id"]: cat["category_name"] for cat in response}
        
        assert categories["1"] == "USA News"
        assert categories["2"] == "Sports"
        assert len(categories) == 3

    def test_parse_live_stream_response(self):
        """Test parsing live stream response."""
        response = [
            {
                "num": 1,
                "name": "CNN",
                "stream_type": "live",
                "stream_id": 12345,
                "stream_icon": "http://logo.com/cnn.png",
                "epg_channel_id": "cnn.us",
                "category_id": "1",
                "tv_archive": 1,
                "tv_archive_duration": 7
            }
        ]
        
        stream = response[0]
        assert stream["name"] == "CNN"
        assert stream["stream_id"] == 12345
        assert stream["tv_archive"] == 1
        assert stream["tv_archive_duration"] == 7

    def test_parse_vod_response(self):
        """Test parsing VOD response."""
        response = [
            {
                "num": 1,
                "name": "Movie Title",
                "stream_type": "movie",
                "stream_id": 99999,
                "stream_icon": "http://poster.com/movie.jpg",
                "rating": "8.5",
                "container_extension": "mkv"
            }
        ]
        
        vod = response[0]
        assert vod["stream_type"] == "movie"
        assert vod["container_extension"] == "mkv"


class TestXtreamAuthentication:
    """Test Xtream authentication handling."""

    def test_auth_in_url(self):
        """Test authentication via URL parameters."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="my_user",
            password="my_pass"
        )
        client = XtreamCodesClient(cfg)
        
        url = client.playlist_url()
        
        # Credentials should be URL-encoded in query string
        assert "username=my_user" in url
        assert "password=my_pass" in url

    def test_special_chars_in_credentials(self):
        """Test handling special characters in credentials."""
        cfg = XtreamCodesConfig(
            base_url="http://provider.com",
            username="user@domain.com",
            password="p@ss!word#123"
        )
        client = XtreamCodesClient(cfg)
        
        url = client.playlist_url()
        
        # Special characters should be URL-encoded
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        
        assert query["username"][0] == "user@domain.com"
        assert query["password"][0] == "p@ss!word#123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

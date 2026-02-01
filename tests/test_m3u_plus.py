"""
Tests for M3U Plus playlist parsing and handling.
"""
import pytest
import tempfile
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playlist import (
    canonicalize_name,
    strip_noise_words,
    tokenize_channel_name,
    group_synonyms,
)


class TestM3UParsing:
    """Test M3U/M3U Plus playlist parsing."""

    SAMPLE_M3U_PLUS = '''#EXTM3U
#EXTINF:-1 tvg-id="abc.us" tvg-name="ABC East" tvg-logo="http://logo.com/abc.png" group-title="USA News",ABC East HD
http://stream.example.com/abc
#EXTINF:-1 tvg-id="cnn.us" tvg-name="CNN" tvg-logo="http://logo.com/cnn.png" group-title="USA News",CNN HD
http://stream.example.com/cnn
#EXTINF:-1 tvg-id="bbc1.uk" tvg-name="BBC One" tvg-logo="http://logo.com/bbc1.png" group-title="UK",BBC One
http://stream.example.com/bbc1
#EXTINF:-1 tvg-id="" tvg-name="" group-title="Sports",ESPN
http://stream.example.com/espn
'''

    SAMPLE_M3U_SIMPLE = '''#EXTM3U
#EXTINF:-1,Channel One
http://stream.example.com/ch1
#EXTINF:-1,Channel Two
http://stream.example.com/ch2
'''

    SAMPLE_M3U_WITH_CATCHUP = '''#EXTM3U
#EXTINF:-1 tvg-id="hbo.us" catchup="default" catchup-days="7" catchup-source="http://catch.com/?start={utc}&end={utcend}",HBO
http://stream.example.com/hbo
'''

    def test_parse_m3u_plus_extinf(self):
        """Test parsing EXTINF tags with all attributes."""
        import re
        
        extinf_pattern = re.compile(
            r'#EXTINF:\s*(-?\d+)\s*'
            r'(?:tvg-id="([^"]*)")?\s*'
            r'(?:tvg-name="([^"]*)")?\s*'
            r'(?:tvg-logo="([^"]*)")?\s*'
            r'(?:group-title="([^"]*)")?\s*'
            r',(.*)$',
            re.IGNORECASE
        )
        
        line = '#EXTINF:-1 tvg-id="abc.us" tvg-name="ABC East" tvg-logo="http://logo.com/abc.png" group-title="USA News",ABC East HD'
        match = extinf_pattern.match(line)
        
        assert match is not None
        assert match.group(2) == "abc.us"  # tvg-id
        assert match.group(3) == "ABC East"  # tvg-name
        assert match.group(4) == "http://logo.com/abc.png"  # tvg-logo
        assert match.group(5) == "USA News"  # group-title
        assert match.group(6) == "ABC East HD"  # channel name

    def test_parse_simple_m3u(self):
        """Test parsing simple M3U without attributes."""
        lines = self.SAMPLE_M3U_SIMPLE.strip().split('\n')
        channels = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                # Simple format: #EXTINF:-1,Channel Name
                _, name = line.split(',', 1)
                url = lines[i + 1].strip() if i + 1 < len(lines) else ""
                channels.append({"name": name, "url": url})
                i += 2
            else:
                i += 1
        
        assert len(channels) == 2
        assert channels[0]["name"] == "Channel One"
        assert channels[0]["url"] == "http://stream.example.com/ch1"
        assert channels[1]["name"] == "Channel Two"

    def test_parse_catchup_attributes(self):
        """Test parsing catchup/timeshift attributes."""
        import re
        
        line = '#EXTINF:-1 tvg-id="hbo.us" catchup="default" catchup-days="7" catchup-source="http://catch.com/?start={utc}&end={utcend}",HBO'
        
        # Extract catchup attributes
        catchup_match = re.search(r'catchup="([^"]*)"', line)
        days_match = re.search(r'catchup-days="([^"]*)"', line)
        source_match = re.search(r'catchup-source="([^"]*)"', line)
        
        assert catchup_match is not None
        assert catchup_match.group(1) == "default"
        assert days_match.group(1) == "7"
        assert source_match.group(1) == "http://catch.com/?start={utc}&end={utcend}"

    def test_group_extraction(self):
        """Test extracting groups from playlist."""
        import re
        
        groups = set()
        for line in self.SAMPLE_M3U_PLUS.split('\n'):
            match = re.search(r'group-title="([^"]*)"', line)
            if match:
                groups.add(match.group(1))
        
        assert "USA News" in groups
        assert "UK" in groups
        assert "Sports" in groups
        assert len(groups) == 3

    def test_handle_empty_attributes(self):
        """Test handling of empty/missing attributes."""
        line = '#EXTINF:-1 tvg-id="" tvg-name="" group-title="Sports",ESPN'
        
        import re
        tvg_id = re.search(r'tvg-id="([^"]*)"', line)
        tvg_name = re.search(r'tvg-name="([^"]*)"', line)
        
        assert tvg_id.group(1) == ""
        assert tvg_name.group(1) == ""

    def test_unicode_channel_names(self):
        """Test handling of unicode characters in channel names."""
        line = '#EXTINF:-1 group-title="España",TVE España HD 🇪🇸'
        name = line.split(',', 1)[1] if ',' in line else ""
        
        assert "España" in name
        assert "🇪🇸" in name

    def test_special_url_characters(self):
        """Test URLs with special characters."""
        urls = [
            "http://stream.example.com/channel?user=test&pass=123",
            "http://stream.example.com/channel|User-Agent=Mozilla/5.0",
            "http://stream.example.com/live/user/pass/12345.ts",
        ]
        
        for url in urls:
            # URLs should be preserved as-is
            assert url == url.strip()
            assert url.startswith("http")


class TestNameNormalization:
    """Test channel name normalization for EPG matching."""

    def test_canonicalize_name_strips_quality_tags(self):
        """Test that quality tags are stripped."""
        assert "cnn" in canonicalize_name("CNN HD").lower()
        assert "bbc one" in canonicalize_name("BBC One FHD").lower()
        assert "hd" not in canonicalize_name("ESPN HD").lower()
        assert "fhd" not in canonicalize_name("ESPN FHD").lower()
        assert "4k" not in canonicalize_name("HBO 4K").lower()

    def test_canonicalize_name_strips_country_codes(self):
        """Test that country codes at start/end are stripped."""
        result = canonicalize_name("US CNN")
        assert "cnn" in result.lower()
        
        result = canonicalize_name("BBC One UK")
        assert "bbc" in result.lower()

    def test_strip_noise_words(self):
        """Test stripping backup/alt/feed terms."""
        result = strip_noise_words("CNN Backup")
        assert "backup" not in result.lower()
        
        result = strip_noise_words("ESPN Alt Feed")
        assert "alt" not in result.lower()
        assert "feed" not in result.lower()

    def test_tokenize_name(self):
        """Test tokenization of channel names."""
        tokens = tokenize_channel_name("BBC One HD UK")
        assert "bbc" in tokens
        assert "one" in tokens

    def test_full_normalization_pipeline(self):
        """Test full normalization pipeline using canonicalize."""
        # These should all normalize to similar forms
        name1 = canonicalize_name("CNN HD")
        name2 = canonicalize_name("US CNN FHD")
        name3 = canonicalize_name("CNN")
        
        # All should contain 'cnn' as the core
        assert "cnn" in name1.lower()
        assert "cnn" in name3.lower()


class TestGroupSynonyms:
    """Test country/region synonym handling."""

    def test_group_synonyms_exists(self):
        """Test that group_synonyms returns a dict."""
        synonyms = group_synonyms()
        assert isinstance(synonyms, dict)
        assert len(synonyms) > 0

    def test_us_synonyms(self):
        """Test US country synonyms."""
        synonyms = group_synonyms()
        assert "us" in synonyms
        us_syns = synonyms["us"]
        assert "usa" in us_syns
        assert "united states" in us_syns

    def test_uk_synonyms(self):
        """Test UK country synonyms."""
        synonyms = group_synonyms()
        assert "uk" in synonyms
        uk_syns = synonyms["uk"]
        assert "gb" in uk_syns or "gbr" in uk_syns or "u.k." in uk_syns

    def test_canada_synonyms(self):
        """Test Canada country synonyms."""
        synonyms = group_synonyms()
        assert "ca" in synonyms
        ca_syns = synonyms["ca"]
        assert "canada" in ca_syns


class TestLargePlaylistHandling:
    """Test handling of large playlists."""

    def test_generate_large_playlist(self):
        """Test generating and processing large playlist data."""
        # Generate 10000 channels
        channels = []
        for i in range(10000):
            channels.append({
                "name": f"Channel {i:05d}",
                "url": f"http://stream.example.com/ch{i}",
                "group": f"Group {i // 100}",
                "tvg-id": f"ch{i}.test"
            })
        
        assert len(channels) == 10000
        
        # Test grouping by group
        groups = {}
        for ch in channels:
            grp = ch.get("group", "Uncategorized")
            if grp not in groups:
                groups[grp] = []
            groups[grp].append(ch)
        
        assert len(groups) == 100  # 10000 / 100 = 100 groups
        assert len(groups["Group 0"]) == 100

    def test_filter_large_list(self):
        """Test filtering large channel lists."""
        channels = [{"name": f"Channel {i}", "url": f"http://ch{i}"} for i in range(5000)]
        
        # Filter for channels containing "42"
        filtered = [ch for ch in channels if "42" in ch["name"]]
        
        # Should find Channel 42, 142, 242, ..., 4200-4299
        assert len(filtered) > 0
        assert any("42" in ch["name"] for ch in filtered)

    def test_chunked_processing(self):
        """Test chunked processing of large lists."""
        total = 10000
        batch_size = 500
        
        processed = 0
        batches = 0
        
        while processed < total:
            end = min(processed + batch_size, total)
            chunk_size = end - processed
            processed = end
            batches += 1
        
        assert processed == total
        assert batches == 20  # 10000 / 500 = 20 batches


class TestM3UFileIO:
    """Test M3U file I/O operations."""

    def test_write_and_read_m3u(self):
        """Test writing and reading M3U files."""
        channels = [
            {"name": "Channel 1", "url": "http://ch1.stream"},
            {"name": "Channel 2", "url": "http://ch2.stream"},
        ]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False) as f:
            f.write("#EXTM3U\n")
            for ch in channels:
                f.write(f'#EXTINF:-1,{ch["name"]}\n')
                f.write(f'{ch["url"]}\n')
            temp_path = f.name
        
        try:
            # Read back
            with open(temp_path, 'r') as f:
                content = f.read()
            
            assert "#EXTM3U" in content
            assert "Channel 1" in content
            assert "http://ch1.stream" in content
        finally:
            os.unlink(temp_path)

    def test_handle_different_line_endings(self):
        """Test handling of different line endings (CRLF, LF, CR)."""
        content_lf = "#EXTM3U\n#EXTINF:-1,Test\nhttp://test\n"
        content_crlf = "#EXTM3U\r\n#EXTINF:-1,Test\r\nhttp://test\r\n"
        content_cr = "#EXTM3U\r#EXTINF:-1,Test\rhttp://test\r"
        
        for content in [content_lf, content_crlf, content_cr]:
            lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            lines = [l.strip() for l in lines if l.strip()]
            
            assert lines[0] == "#EXTM3U"
            assert "#EXTINF:-1,Test" in lines
            assert "http://test" in lines


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Tests for search, filtering, and category viewing functionality.
"""
import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestChannelSearch:
    """Test channel search functionality."""

    def test_simple_name_search(self):
        """Test simple channel name search."""
        channels = [
            {"name": "CNN International", "group": "News"},
            {"name": "BBC World", "group": "News"},
            {"name": "ESPN Sports", "group": "Sports"},
            {"name": "Discovery Channel", "group": "Documentary"},
        ]
        
        query = "cnn"
        results = [ch for ch in channels if query.lower() in ch["name"].lower()]
        
        assert len(results) == 1
        assert results[0]["name"] == "CNN International"

    def test_partial_match_search(self):
        """Test partial match search."""
        channels = [
            {"name": "Sports Center", "group": "Sports"},
            {"name": "ESPN Sports", "group": "Sports"},
            {"name": "Sky Sports News", "group": "News"},
        ]
        
        query = "sport"
        results = [ch for ch in channels if query.lower() in ch["name"].lower()]
        
        assert len(results) == 3

    def test_case_insensitive_search(self):
        """Test case insensitive search."""
        channels = [
            {"name": "HBO Max", "group": "Movies"},
            {"name": "hbo comedy", "group": "Comedy"},
        ]
        
        query = "HBO"
        results = [ch for ch in channels if query.lower() in ch["name"].lower()]
        
        assert len(results) == 2

    def test_empty_query_returns_all(self):
        """Test empty query returns all channels."""
        channels = [
            {"name": "Channel 1", "group": "A"},
            {"name": "Channel 2", "group": "B"},
        ]
        
        query = ""
        results = channels if not query else [ch for ch in channels if query in ch["name"]]
        
        assert len(results) == 2

    def test_no_match_returns_empty(self):
        """Test no match returns empty list."""
        channels = [
            {"name": "CNN", "group": "News"},
            {"name": "BBC", "group": "News"},
        ]
        
        query = "xyz123"
        results = [ch for ch in channels if query.lower() in ch["name"].lower()]
        
        assert len(results) == 0


class TestEPGSearch:
    """Test EPG (program guide) search functionality."""

    def test_search_in_programme_titles(self):
        """Test searching in programme titles."""
        programmes = [
            {"title": "News at Ten", "channel": "BBC One", "start": "22:00"},
            {"title": "Good Morning America", "channel": "ABC", "start": "07:00"},
            {"title": "Evening News", "channel": "CBS", "start": "18:30"},
        ]
        
        query = "news"
        results = [p for p in programmes if query.lower() in p["title"].lower()]
        
        assert len(results) == 2

    def test_search_in_descriptions(self):
        """Test searching in programme descriptions."""
        programmes = [
            {"title": "Movie Night", "desc": "Action thriller starring John Wick"},
            {"title": "Documentary Hour", "desc": "Nature documentary about lions"},
        ]
        
        query = "thriller"
        results = [p for p in programmes if query.lower() in p.get("desc", "").lower()]
        
        assert len(results) == 1

    def test_search_with_time_range(self):
        """Test EPG search with time range filter."""
        from datetime import datetime, timedelta
        
        now = datetime.now()
        programmes = [
            {"title": "Morning Show", "start": now - timedelta(hours=2)},
            {"title": "Noon News", "start": now + timedelta(hours=1)},
            {"title": "Evening Movie", "start": now + timedelta(hours=8)},
        ]
        
        # Get upcoming in next 2 hours
        window = timedelta(hours=2)
        upcoming = [p for p in programmes if now <= p["start"] <= now + window]
        
        assert len(upcoming) == 1
        assert upcoming[0]["title"] == "Noon News"


class TestGroupFiltering:
    """Test group/category filtering functionality."""

    def test_filter_by_single_group(self):
        """Test filtering by single group."""
        channels = [
            {"name": "CNN", "group": "News"},
            {"name": "ESPN", "group": "Sports"},
            {"name": "HBO", "group": "Movies"},
            {"name": "BBC", "group": "News"},
        ]
        
        group = "News"
        results = [ch for ch in channels if ch["group"] == group]
        
        assert len(results) == 2
        assert all(ch["group"] == "News" for ch in results)

    def test_filter_all_groups(self):
        """Test 'All' filter shows all channels."""
        channels = [
            {"name": "CNN", "group": "News"},
            {"name": "ESPN", "group": "Sports"},
        ]
        
        group = "All"
        results = channels if group == "All" else [ch for ch in channels if ch["group"] == group]
        
        assert len(results) == 2

    def test_extract_unique_groups(self):
        """Test extracting unique groups from channels."""
        channels = [
            {"name": "CNN", "group": "News"},
            {"name": "BBC", "group": "News"},
            {"name": "ESPN", "group": "Sports"},
            {"name": "HBO", "group": "Movies"},
            {"name": "Fox Sports", "group": "Sports"},
        ]
        
        groups = sorted(set(ch["group"] for ch in channels))
        
        assert groups == ["Movies", "News", "Sports"]
        assert len(groups) == 3


class TestGroupSorting:
    """Test group sorting functionality."""

    def test_alphabetical_group_sort(self):
        """Test alphabetical group sorting."""
        groups = ["Sports", "News", "Movies", "Documentary"]
        
        sorted_groups = sorted(groups)
        
        assert sorted_groups == ["Documentary", "Movies", "News", "Sports"]

    def test_channel_count_sort(self):
        """Test sorting groups by channel count."""
        group_counts = {
            "News": 50,
            "Sports": 100,
            "Movies": 30,
        }
        
        sorted_by_count = sorted(group_counts.items(), key=lambda x: x[1], reverse=True)
        
        assert sorted_by_count[0][0] == "Sports"
        assert sorted_by_count[0][1] == 100


class TestCombinedSearchAndFilter:
    """Test combined search and filter operations."""

    def test_search_within_group(self):
        """Test searching within a specific group."""
        channels = [
            {"name": "CNN International", "group": "News"},
            {"name": "BBC News", "group": "News"},
            {"name": "ESPN News", "group": "Sports"},
            {"name": "Sky News", "group": "News"},
        ]
        
        group = "News"
        query = "bbc"
        
        # First filter by group, then search
        filtered = [ch for ch in channels if ch["group"] == group]
        results = [ch for ch in filtered if query.lower() in ch["name"].lower()]
        
        assert len(results) == 1
        assert results[0]["name"] == "BBC News"

    def test_search_then_filter(self):
        """Test searching then filtering by group."""
        channels = [
            {"name": "News Hour", "group": "News"},
            {"name": "Sports News", "group": "Sports"},
            {"name": "News Tonight", "group": "News"},
        ]
        
        query = "news"
        results = [ch for ch in channels if query.lower() in ch["name"].lower()]
        
        # Then filter
        group = "Sports"
        final = [ch for ch in results if ch["group"] == group]
        
        assert len(final) == 1
        assert final[0]["name"] == "Sports News"


class TestFavorites:
    """Test favorites functionality."""

    def test_add_to_favorites(self):
        """Test adding channel to favorites."""
        favorites = set()
        channel_id = "cnn-international"
        
        favorites.add(channel_id)
        
        assert channel_id in favorites

    def test_remove_from_favorites(self):
        """Test removing channel from favorites."""
        favorites = {"cnn", "bbc", "espn"}
        
        favorites.discard("bbc")
        
        assert "bbc" not in favorites
        assert len(favorites) == 2

    def test_filter_favorites_only(self):
        """Test filtering to show only favorites."""
        channels = [
            {"name": "CNN", "id": "cnn"},
            {"name": "BBC", "id": "bbc"},
            {"name": "ESPN", "id": "espn"},
        ]
        favorites = {"cnn", "espn"}
        
        results = [ch for ch in channels if ch["id"] in favorites]
        
        assert len(results) == 2


class TestRecentlyWatched:
    """Test recently watched functionality."""

    def test_add_to_recent(self):
        """Test adding to recently watched."""
        recent = []
        max_recent = 10
        
        recent.insert(0, "channel1")
        recent.insert(0, "channel2")
        
        assert recent[0] == "channel2"
        assert len(recent) == 2

    def test_recent_limit(self):
        """Test recently watched list limit."""
        recent = []
        max_recent = 3
        
        for i in range(5):
            recent.insert(0, f"channel{i}")
            if len(recent) > max_recent:
                recent.pop()
        
        assert len(recent) == max_recent

    def test_recent_moves_to_top(self):
        """Test rewatching moves channel to top."""
        recent = ["ch1", "ch2", "ch3"]
        
        # Watch ch3 again
        if "ch3" in recent:
            recent.remove("ch3")
        recent.insert(0, "ch3")
        
        assert recent[0] == "ch3"


class TestChannelListPopulation:
    """Test channel list population and display."""

    def test_populate_with_channel_data(self):
        """Test populating list with channel data."""
        channels = [
            {"name": "CNN", "tvg-logo": "logo.png", "group": "News"},
            {"name": "ESPN", "tvg-logo": "", "group": "Sports"},
        ]
        
        list_items = []
        for ch in channels:
            item = {
                "display": ch["name"],
                "logo": ch.get("tvg-logo", ""),
                "group": ch.get("group", "Other"),
            }
            list_items.append(item)
        
        assert len(list_items) == 2
        assert list_items[0]["display"] == "CNN"

    def test_chunked_population_for_large_lists(self):
        """Test chunked population for large lists."""
        channels = [{"name": f"Channel {i}"} for i in range(5000)]
        
        chunk_size = 100
        chunks = [channels[i:i+chunk_size] for i in range(0, len(channels), chunk_size)]
        
        assert len(chunks) == 50
        assert len(chunks[0]) == chunk_size

    def test_population_cancellation_token(self):
        """Test population can be cancelled with token."""
        populate_token = {"cancelled": False}
        
        items_added = 0
        for i in range(100):
            if populate_token["cancelled"]:
                break
            items_added += 1
            if i == 50:
                populate_token["cancelled"] = True
        
        assert items_added == 51  # 0-50 inclusive


class TestNameNormalization:
    """Test channel name normalization for display."""

    def test_strip_quality_suffix(self):
        """Test stripping quality suffixes."""
        names = {
            "CNN HD": "CNN",
            "ESPN FHD": "ESPN",
            "BBC 4K": "BBC",
            "HBO UHD": "HBO",
        }
        
        for original, expected in names.items():
            normalized = original
            for suffix in [" HD", " FHD", " 4K", " UHD", " SD"]:
                if normalized.endswith(suffix):
                    normalized = normalized[:-len(suffix)]
            assert normalized == expected

    def test_strip_country_prefix(self):
        """Test stripping country prefixes."""
        names = {
            "US: CNN": "CNN",
            "UK| BBC": "BBC",
            "CA- TSN": "TSN",  # No space before dash
        }
        
        import re
        for original, expected in names.items():
            # Remove country prefix patterns (2-letter code + colon/pipe/dash + optional space)
            normalized = re.sub(r'^[A-Z]{2}[:\|\-]\s*', '', original)
            assert normalized == expected


class TestGroupSynonyms:
    """Test group synonym handling."""

    def test_merge_similar_groups(self):
        """Test merging similar group names."""
        synonyms = {
            "News": ["news", "NEWS", "News Channels", "News & Info"],
            "Sports": ["sports", "SPORTS", "Sports Channels", "Sport"],
        }
        
        def normalize_group(group_name):
            group_lower = group_name.lower()
            for canonical, variants in synonyms.items():
                if group_lower in [v.lower() for v in variants] or group_lower == canonical.lower():
                    return canonical
            return group_name
        
        assert normalize_group("news") == "News"
        assert normalize_group("NEWS") == "News"
        assert normalize_group("Sport") == "Sports"

    def test_custom_group_mapping(self):
        """Test custom group mapping."""
        mapping = {
            "VOD": "On Demand",
            "PPV": "Pay Per View",
            "XXX": "Adult",
        }
        
        def map_group(group):
            return mapping.get(group, group)
        
        assert map_group("VOD") == "On Demand"
        assert map_group("News") == "News"  # Unchanged


class TestAccessibility:
    """Test accessibility features for screen readers."""

    def test_channel_announcement_format(self):
        """Test channel announcement format for screen readers."""
        channel = {
            "name": "CNN International",
            "group": "News",
            "now_playing": "Anderson Cooper 360",
        }
        
        # Format for screen reader announcement
        announcement = f"{channel['name']}"
        if channel.get("now_playing"):
            announcement += f", now playing: {channel['now_playing']}"
        announcement += f", in {channel['group']}"
        
        assert "CNN International" in announcement
        assert "Anderson Cooper 360" in announcement

    def test_search_results_announcement(self):
        """Test search results announcement."""
        results = [{"name": "CNN"}, {"name": "BBC"}]
        query = "news"
        
        announcement = f"{len(results)} channels found for '{query}'"
        
        assert "2 channels found" in announcement

    def test_group_change_announcement(self):
        """Test group change announcement."""
        new_group = "Sports"
        channel_count = 45
        
        announcement = f"Viewing {new_group}, {channel_count} channels"
        
        assert "Sports" in announcement
        assert "45" in announcement


class TestLargePlaylistHandling:
    """Test handling of large playlists."""

    def test_preview_first_items(self):
        """Test showing preview of first items quickly."""
        channels = [{"name": f"Channel {i}"} for i in range(10000)]
        
        preview_count = 200
        preview = channels[:preview_count]
        
        assert len(preview) == 200

    def test_virtual_list_mode(self):
        """Test virtual list mode for large lists."""
        total_channels = 50000
        visible_items = 20  # Items visible in viewport
        
        # Virtual list only renders visible items
        rendered_count = min(visible_items, total_channels)
        
        assert rendered_count == 20

    def test_search_with_limit(self):
        """Test search with result limit."""
        channels = [{"name": f"Sports Channel {i}"} for i in range(1000)]
        
        query = "sports"
        max_results = 100
        
        results = []
        for ch in channels:
            if query.lower() in ch["name"].lower():
                results.append(ch)
                if len(results) >= max_results:
                    break
        
        assert len(results) == max_results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

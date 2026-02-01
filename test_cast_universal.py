
import time
import logging
import pychromecast
from stream_proxy import get_proxy

# Configure logging
logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger("UniversalTest")

# Target details
TARGET_NAME = "RB Room"
# Radio URL
SOURCE_URL = "https://radio.serrebiradio.com/listen/serrebiradio/SerrebiRadio"
# Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def test_cast():
    LOG.info(f"Starting UNIVERSAL test cast to {TARGET_NAME}...")
    LOG.info(f"Source URL: {SOURCE_URL}")

    proxy = get_proxy()
    proxy.start()

    # 1. Discover
    casts, browser = pychromecast.get_chromecasts(timeout=5)
    target_cast = None
    for cast in casts:
        if TARGET_NAME.lower() in cast.cast_info.friendly_name.lower():
            target_cast = cast
            break

    if not target_cast:
        LOG.error(f"Device '{TARGET_NAME}' not found.")
        browser.stop_discovery()
        return

    LOG.info(f"Connecting to {target_cast.cast_info.friendly_name}...")
    target_cast.wait()

    # 2. Get Proxied URL
    # Using the same logic as casting.py
    proxied_url = proxy.get_audio_url(SOURCE_URL, headers=HEADERS)
    LOG.info(f"Proxied URL: {proxied_url}")

    # 3. Play
    mc = target_cast.media_controller
    # Use standard HLS MIME type
    mc.play_media(proxied_url, "application/vnd.apple.mpegurl", title="Radio Test - RB Room")
    mc.block_until_active()

    LOG.info("Playback command sent. Waiting 20 seconds to confirm transition from bootstrap to live...")
    time.sleep(20)

    LOG.info("Test complete. Stopping...")
    mc.stop()
    browser.stop_discovery()
    proxy.stop()

if __name__ == "__main__":
    test_cast()

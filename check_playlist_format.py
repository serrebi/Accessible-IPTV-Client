
import urllib.request
import sys

url = "http://live.iptvcanada.tv//playlist/jb8gdjke8y/9879569749/m3u8"

print(f"Fetching {url}...")
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        content = resp.read(2048).decode("utf-8", errors="ignore")
        print("--- First 20 lines ---")
        print("\n".join(content.splitlines()[:20]))
except Exception as e:
    print(f"Error: {e}")

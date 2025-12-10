
import urllib.request
import sys

base = "http://live.iptvcanada.tv/get.php?username=jb8gdjke8y&password=9879569749&type=m3u_plus"
variants = [
    ("&output=m3u8", "Standard HLS (m3u8)"),
    ("&output=hls", "Alternative HLS (hls)"),
    ("&output=ts", "Standard TS (ts)") # Control
]

for suffix, name in variants:
    url = base + suffix
    print(f"\nTesting {name}: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read(1024).decode("utf-8", errors="ignore")
            lines = content.splitlines()
            valid_lines = [l for l in lines if l.strip() and not l.startswith('#')]
            if valid_lines:
                print(f"Sample stream URL: {valid_lines[0]}")
            else:
                print("Playlist empty or comments only.")
    except Exception as e:
        print(f"Error: {e}")

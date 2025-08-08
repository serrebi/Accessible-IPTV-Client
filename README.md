# Accessible IPTV Client

A keyboard-first IPTV client I vibe coded  with ChatGPT. It is using wxPython. I made it for myself. It’s fast, simple, and works with screen readers.

**Platforms:** Windows and Linux (tested).  
**macOS:** should work, but I didn’t test it.

---

## What it does

- Works with screen readers (NVDA, JAWS, Narrator, Orca).
- Reads M3U and M3U Plus.
- Lets you add and remove playlists.
- Groups channels by category.
- Search box for channels.
- Full keyboard control. Mouse is optional.
- Remembers your last playlist and channel.
- Saves everything to the app’s folder:
  - `iptvclient.conf` for options and playlists.
  - `epg.sqlite` for the TV guide (XMLTV).
- Opens streams in your external media player.

---

## What you need

- **Python 3.8+**
- **wxPython** (`pip install wxpython`)
- **One or more media players** (VLC, MPC-HC, MPV, etc.)
- **At least one playlist URL or file**
- (Optional) **XMLTV EPG** URL or file (`.xml` or `.xml.gz`)

---

## Quick start

### Windows (PowerShell or CMD)

1) Install Python:

```bat
winget install -e --id Python.Python.3.12

# Accessible IPTV Client

Keyboard-first IPTV client built with wxPython. Works with screen readers. Runs on Windows and Linux. macOS is not supported.

---

## Features

- Screen reader friendly: NVDA, JAWS, Narrator, Orca
- Reads M3U and M3U Plus playlists
- Add or remove playlists
- Group channels by category
- Search channels
- Open streams in an external media player
- Optional XMLTV EPG stored in RAM

---

## Requirements

- Python 3.8+
- wxPython (`pip install wxpython`)
- One or more media players: VLC, MPC-HC, MPV, etc.
- At least one playlist URL or file
- Optional XMLTV EPG URL or file (`.xml` or `.xml.gz`)

---

## Install

### Windows

1. Install Python:
   ```bat
   winget install -e --id Python.Python.3.12

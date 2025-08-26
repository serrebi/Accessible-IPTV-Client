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

### Running on windows or Linux:
Most people can just download release from https://github.com/serrebi/accessible-IPTV-Client/releases
, but if you want to build your own or fork, install python, and then...
pip3 install --upgrade pip
pip3 install wxpython
Clone this repository, and cd into the directory: make sure you are in the directory of the repo, and then:
python3 ./main.py
### Building on windows or Linux:
Make sure you are in the directory of the repo, then:
pip install pyinstaller
pyinstaller --noconsole --onefile main.py
You will find an executable in the ./dist directory.

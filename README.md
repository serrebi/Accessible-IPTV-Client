````markdown
# Accessible IPTV Client

A keyboard-first IPTV client built with wxPython. I made it for myself. It’s fast, simple, and works with screen readers.

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
````

2. Get wxPython:

```bat
python -m pip install --upgrade pip
pip install wxpython
```

3. Run the app from the project folder:

```bat
python .\main.py
```

### Linux

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-pip
# Option A: pip (usually fine)
pip install --upgrade pip
pip install wxpython
# Option B: distro package (alternative)
sudo apt install -y python3-wxgtk4.0
python ./main.py
```

Fedora:

```bash
sudo dnf install -y python3 python3-pip wxPython
python ./main.py
```

Arch:

```bash
sudo pacman -S --noconfirm python python-pip python-wxpython
python ./main.py
```

openSUSE:

```bash
sudo zypper install -y python3 python3-pip python3-wxPython
python ./main.py
```

### macOS (untested)

```bash
python3 -m pip install --upgrade pip
pip install wxpython
python ./main.py
```

---

## Install media players (Windows)

Pick what you like. Install as many as you want.

```bat
:: VLC
winget install -e --id VideoLAN.VLC

:: MPC-HC (Media Player Classic - Home Cinema)
winget install -e --id clsid2.mpc-hc

:: MPC-BE (Media Player Classic - Black Edition)
winget install -e --id MPC-BE.MPC-BE

:: mpv.net (GUI for mpv on Windows)
winget install -e --id mpv.net

:: SMPlayer
winget install -e --id SMPlayer.SMPlayer

:: Kodi
winget install -e --id XBMCFoundation.Kodi

:: PotPlayer
winget install -e --id Daum.PotPlayer

:: GOM Player
winget install -e --id GOMLab.GOMPlayer
```

Linux (examples):

```bash
# Debian/Ubuntu
sudo apt install -y vlc mpv smplayer kodi

# Fedora
sudo dnf install -y vlc mpv smplayer kodi

# Arch
sudo pacman -S --noconfirm vlc mpv smplayer kodi
```

Set your preferred player in **Options → Media Player** inside the app.

---

## How to use it

1. **Run the app.**
2. **Add a playlist:** Menu → **Playlists** → **Add URL** or **Add File**.
3. **Pick a channel group** with arrow keys.
4. **Pick a channel** and press **Enter** to play in your external player.
5. **Set your player path (once):** **Options → Media Player**.
6. **EPG (TV guide):** Menu → **EPG** → **Add URL** or **Add File**. XMLTV `.xml` or `.xml.gz` works.

**Navigation (keyboard):**

* **Tab / Shift+Tab:** move between controls
* **Arrow keys / Page Up / Page Down:** move lists
* **Enter:** activate / play
* **Esc:** close dialogs
* **Alt key:** open menu bar

Everything saves automatically to `iptvclient.conf`. The guide is stored in `epg.sqlite`.

---

## Building a single-file EXE (Windows)

From the project folder:

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile main.py
```

You’ll get the build in `dist\main.exe`.

---

## EPG details (XMLTV)

* Add XMLTV sources (URL or file). `.gz` is fine.
* The app imports XMLTV on startup and in the background.
* Data is stored in `epg.sqlite`. Old data is cleaned up.
* Now/Next is matched to channels automatically.

**If your guide shows “No program currently airing”:**

* Your EPG is empty or out of date. Add a better XMLTV source.
* The channel name is weird. Try another playlist or name variant.
* Give it a minute after adding a big EPG. It parses in the background.

---

## Tips that actually help

* Install your media player **before** first run so auto-detect can find it.
* VLC and MPC family handle most formats. MPV is very robust too.
* If a stream fails in one player, try another. The app doesn’t fix bad streams.
* Keep your playlists and EPG sources clean. Garbage in, garbage out.

---

## Known limits

* No DVR. No catchup. I didn’t need them.
* EPG quality depends on your XMLTV source. Some channels won’t have guide data.
* Streams open in external players by design.

---

## Problem solving (blunt)

* **Nothing plays:** your player path is wrong, the stream is dead, or your firewall is blocking it.
* **Guide is blank:** your XMLTV source is bad or stale.
* **Wrong guide on CW/locals:** your playlist names are messy. Use a source with clear station names/callsigns.

---

## License and support

I wrote this for me. Use it if it helps. No formal support.
Bugs and PRs welcome if you want to improve it.

```

Sources for Winget package IDs and usage: VideoLAN.VLC, MPC-HC (clsid2.mpc-hc), MPC-BE (MPC-BE.MPC-BE), mpv.net (mpv.net), SMPlayer (SMPlayer.SMPlayer), Kodi (XBMCFoundation.Kodi), PotPlayer (Daum.PotPlayer), and Winget docs. :contentReference[oaicite:0]{index=0}
::contentReference[oaicite:1]{index=1}
```

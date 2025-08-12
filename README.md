````markdown
# Accessible IPTV Client

This is a keyboard-first IPTV client vibe coded with wxPython. I made it for myself. It’s fast, simple, and works with screen readers.

You deffinitly can run this on Windows, and Linux, but your milage may vary if you want to run it on MacOS. I don't need this on that platform, so I haven't tried it.
---

## What it does

- Works with screen readers (NVDA, JAWS, Narrator, Orca).
- Reads M3U and M3U Plus.
- Lets you add and remove playlists.
- Groups channels by category.
- Search box for channels.
- Saves everything to the app’s folder:
  - `iptvclient.conf` for options and playlists.
  - `EPG is stored in ram.
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

1) Install Python: Either this way, directly from python.org  or through the windows store.

```bat
winget install -e --id Python.Python.3.12
````

2. Get wxPython:

```bat
pip3 install --upgrade pip
pip3 install wxpython
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
pip3 install --upgrade pip
pip3 install wxpython
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

Everything saves automatically to `iptvclient.conf`. The guide is stored in ram.

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

* Give it a minute after adding a big EPG. It parses in the background.

---

## Tips that actually help

* Install your media player **before** first run so auto-detect can find it.
* VLC and MPC family handle most formats. MPV and Fauxdacious are very robust too.
* If a stream fails in one player, try another. The app doesn’t fix bad streams.


---

## Known limits

* No DVR. No catchup. I didn’t need them. It's beyond the scope of this app.
* EPG quality depends on your XMLTV source. Some channels won’t have guide data, and I've tried to work around this, but it might not be perfect..
* Streams open in external players by design.

---

## License and support

I wrote this for me. Use it if it helps. No formal support.
Bugs and PRs welcome if you want to help improve it.

## Install media players (Windows)

This is an optional section, I'm including just to be useful. Pick what you like. Install as many as you want.

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

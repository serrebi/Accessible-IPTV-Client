````markdown
# Accessible IPTV Client

Keyboard-first IPTV client VibeCoded with wxPython. Works with screen readers. Runs on Windows and Linux.

---

## Features
- Screen reader friendly (NVDA, JAWS, Narrator, Orca)
- Reads M3U and M3U Plus
- Add or remove playlists
- Channel groups by category
- Channel search
- Opens streams in your external media player
- Optional XMLTV EPG (stored in RAM)

---

## Requirements
- Python 3.8+
- wxPython (`pip install wxpython`)
- One or more media players (VLC, MPC-HC, MPV, etc.)
- At least one playlist file or URL
- (Optional) XMLTV EPG URL or file (`.xml` or `.xml.gz`)

---

## Install

### Windows
1. Install Python:
   ```bat
   winget install -e --id Python.Python.3.12
````

2. Install dependencies:

   ```bat
   pip3 install --upgrade pip
   pip3 install wxpython
   ```
3. Run:

   ```bat
   python .\main.py
   ```

### Linux

1. Install Python and pip using your distro tools.
2. Install wxPython:

   ```bash
   pip3 install --upgrade pip
   pip3 install wxpython
   ```
3. Run:

   ```bash
   python3 ./main.py
   ```

If your distro ships wxPython, you may use it instead:

```bash
# Debian/Ubuntu
sudo apt install -y python3-wxgtk4.0
# Fedora
sudo dnf install -y wxPython
# Arch
sudo pacman -S --noconfirm python-wxpython
# openSUSE
sudo zypper install -y python3-wxPython
python3 ./main.py
```

---

## Use

1. Start the app.
2. Add a playlist: **Menu → Playlists → Add URL** or **Add File**.
3. Select a channel group with arrow keys.
4. Select a channel and press **Enter** to play in your media player.
5. Set the player path once: **Menu → Options → Media Player**.
6. Add EPG (optional): **Menu → EPG → Add URL** or **Add File**.

**Keyboard**

* **Tab / Shift+Tab**: move focus
* **Arrow keys / Page Up / Page Down**: navigate lists
* **Enter**: activate / play
* **Esc**: close dialog
* **Alt**: open menu bar

**Storage**

* Settings and playlists: `iptvclient.conf` in the app folder
* EPG: in RAM

---

## Build a single-file EXE (Windows)

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile main.py
```

Output: `dist\main.exe`

---

## EPG (XMLTV)

* Accepts `.xml` and `.xml.gz`
* Parsed on startup and in background
* Now/Next matched to channels
* Large EPGs may need a minute to load

---

## Tips

* Install your media player before first run for auto-detect
* If a stream fails in one player, try another
* VLC, MPC variants, and MPV handle most streams well

---

## Known limits

* No DVR or catchup
* EPG quality depends on your XMLTV source
* Streams always open in external players

---

## License and support

Personal project. Use at your own discretion. Bug reports and PRs are welcome.

---

## Optional: install media players

### Windows

```bat
:: VLC
winget install -e --id VideoLAN.VLC
:: MPC-HC
winget install -e --id clsid2.mpc-hc
:: MPC-BE
winget install -e --id MPC-BE.MPC-BE
:: mpv.net
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

### Linux

```bash
# Debian/Ubuntu
sudo apt install -y vlc mpv smplayer kodi
# Fedora
sudo dnf install -y vlc mpv smplayer kodi
# Arch
sudo pacman -S --noconfirm vlc mpv smplayer kodi
```

Set your player in **Options → Media Player**.

```
```

# Accessible IPTV Client

An accessible IPTV client VibeCoded using wxPython. 

## Features

- Fully accessible with all major screen readers on Windows (NVDA, JAWS, etc).
- Supports m3u and m3u_plus playlists.
- Playlist Manager: add, remove, and manage playlists. EPG: add xml, or xml gz EPG sources. All playlists and EPG are stored in `iptvclient.conf` and EPG.DB in the same folder as the executable.
- Supports external media players on windows, Mac, and Linux
- Category/Group navigation: browse channels by group.
- Channel search and filter field.
- Keyboard navigation for everything. Mouse not required.
- Remembers channel and playlist selection after refresh.
- Settings and playlists are always saved in the app’s folder.
- Fast and responsive.

## Requirements

- Windows 10 or 11
- Python 3.8+
- wxPython
- VLC or any supported player
- a playlist


## Installation

```sh
pip install wxpython
```
download the .py or clone. CD,  and run
```sh
python3 ./iptvclient.py
Download and install VLC or other players if needed.

## Building the Executable
Clone this repo, or download the zip. Then

```sh
pip install pyinstaller
pyinstaller --noconsole --onefile iptvclient.py
```
or if you want to build using the split version
```sh
pyinstaller --noconsole --onefile main.py
Your `.exe` will be in the `dist` folder.

## Usage

- Run the app.
- Use menus to add or remove playlists (local files or URLs).
- Set your preferred media player in the Options > Media Player submenu.
- Browse channels and groups using the keyboard. Press Enter to play.
- All settings save automatically in `iptvclient.conf`.

## Known Limitations

- Tentative EPG
- No recording or catchup support.
- App opens streams in external player windows, because that's all I need it to do.

## Tips

- Player paths are auto-detected from default install locations, so make sure they are installed first.
-  You are restricted by what the player you choose supports. VLC and Media player classic support a wide variety of formats. I use MPC BE myself. Make sure you have installed your player of choice.

## Support

I wrote this for myself so there isn't really support. Sorry! Would still love to know if something isn't working, though.

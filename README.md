# Accessible IPTV Client
An accessible IPTV client I VibeCoded using Wx Python
#features
Fully accessible with screen readers on windows.
Supports both m3u, and m3u_plus
Includes a playlist manager, which will store your playlists in a conf file in the same place as the exe.
Allows to use VLC, Foobar2000, Media player classic, etc as media player.

#Instructions for building
pip install pyinstaller
pip install wxpython).
pyinstaller --noconsole --onefile iptvclient.py

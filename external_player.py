import os
import platform
import subprocess
import json
import socket
import threading
import time
import tempfile
from typing import Tuple

class ExternalPlayerLauncher:
    def __init__(self):
        self._launch_guard_lock = threading.Lock()
        self._last_launch_ts = 0.0

    def launch(self, player_name: str, url: str, custom_path: str = "") -> Tuple[bool, str]:
        """
        Launches the specified external player with the given URL.
        Returns (success, error_message).
        """
        # Guard against accidental double-invocation
        with self._launch_guard_lock:
            now = time.time()
            if (now - self._last_launch_ts) < 0.75:
                return True, "" # Debounced
            self._last_launch_ts = now

        # If using mpv, try to reuse an existing instance via IPC first.
        if player_name == "MPV":
            if self._mpv_try_send(url):
                return True, ""

        win_paths = {
            "VLC": [r"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe", r"C:\\Program Files (x86)\\VideoLAN\\VLC\\vlc.exe"],
            "MPC": [r"C:\\Program Files\\MPC-HC\\mpc-hc64.exe", r"C:\\Program Files (x86)\\K-Lite Codec Pack\\MPC-HC64\\mpc-hc64.exe", r"C:\\Program Files (x86)\\MPC-HC\\mpc-hc.exe"],
            "MPC-BE": [r"C:\\Program Files\\MPC-BE\\mpc-be64.exe", r"C:\\Program Files (x86)\\MPC-BE\\mpc-be.exe", r"C:\\Program Files\\MPC-BE x64\\mpc-be64.exe", r"C:\\Program Files\\MPC-BE\\mpc-be.exe"],
            "Kodi": [r"C:\\Program Files\\Kodi\\kodi.exe"],
            "Winamp": [r"C:\\Program Files\\Winamp\\winamp.exe"],
            "Foobar2000": [r"C:\\Program Files\\foobar2000\\foobar2000.exe"],
            "MPV": [r"C:\\Program Files\\mpv\\mpv.exe", r"C:\\Program Files (x86)\\mpv\\mpv.exe"],
            "SMPlayer": [r"C:\\Program Files\\SMPlayer\\smplayer.exe", r"C:\\Program Files (x86)\\SMPlayer\\smplayer.exe"],
            "QuickTime": [r"C:\\Program Files\\QuickTime\\QuickTimePlayer.exe", r"C:\\Program Files (x86)\\QuickTime\\QuickTimePlayer.exe"],
            "iTunes/Apple Music": [r"C:\\Program Files\\iTunes\\iTunes.exe", r"C:\\Program Files (x86)\\iTunes\\iTunes.exe", r"C:\\Program Files\\Apple\\Music\\AppleMusic.exe"],
            "PotPlayer": [r"C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe", r"C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini.exe"],
            "KMPlayer": [r"C:\\Program Files\\KMP Media\\KMPlayer\\KMPlayer64.exe", r"C:\\Program Files (x86)\\KMP Media\\KMPlayer\\KMPlayer.exe"],
            "AIMP": [r"C:\\Program Files\\AIMP\\AIMP.exe", r"C:\\Program Files (x86)\\AIMP\\AIMP.exe"],
            "QMPlay2": [r"C:\\Program Files\\QMPlay2\\QMPlay2.exe", r"C:\\Program Files (x86)\\QMPlay2\\QMPlay2.exe"],
            "GOM Player": [r"C:\\Program Files\\GRETECH\\GomPlayer\\GOM.exe", r"C:\\Program Files (x86)\\GRETECH\\GomPlayer\\GOM.exe"],
            "Clementine": [r"C:\\Program Files\\Clementine\\clementine.exe"],
            "Strawberry": [r"C:\\Program Files\\Strawberry\\strawberry.exe"],
        }
        
        linux_players = { "VLC": "vlc", "MPV": "mpv", "Kodi": "kodi", "SMPlayer": "smplayer", "Totem": "totem", "PotPlayer": "potplayer", "KMPlayer": "kmplayer", "AIMP": "aimp", "QMPlay2": "qmplay2", "GOM Player": "gomplayer", "Audacious": "audacious", "Fauxdacious": "fauxdacious", "MPC-BE": "mpc-be", "Clementine": "clementine", "Strawberry": "strawberry", "Amarok": "amarok", "Rhythmbox": "rhythmbox", "Pragha": "pragha", "Lollypop": "lollypop", "Exaile": "exaile", "Quod Libet": "quodlibet", "Gmusicbrowser": "gmusicbrowser", "Xmms": "xmms", "Vocal": "vocal", "Haruna": "haruna", "Celluloid": "celluloid" }
        mac_paths = { "VLC": ["/Applications/VLC.app/Contents/MacOS/VLC"], "QuickTime": ["/Applications/QuickTime Player.app/Contents/MacOS/QuickTime Player"], "iTunes/Apple Music": ["/Applications/Music.app/Contents/MacOS/Music", "/Applications/iTunes.app/Contents/MacOS/iTunes"], "QMPlay2": ["/Applications/QMPlay2.app/Contents/MacOS/QMPlay2"], "Audacious": ["/Applications/Audacious.app/Contents/MacOS/Audacious"], "Fauxdacious": ["/Applications/Fauxdacious.app/Contents/MacOS/Fauxdacious"] }

        ok, err = False, ""

        if player_name == "Custom" and custom_path:
            exe = custom_path
            # Heuristically detect known players from custom path for better flags
            pname = os.path.basename(exe).lower()
            detected = player_name
            if "mpv" in pname:
                detected = "MPV"
            elif "vlc" in pname:
                detected = "VLC"
            elif "mpc-be" in pname or "mpcbe" in pname:
                detected = "MPC-BE"
            elif pname.startswith("mpc-hc") or pname.startswith("mpc"):
                detected = "MPC"
            argv = self._argv_for(detected, exe, platform.system() == "Windows", url)
            ok, err = self._spawn_windows(argv) if platform.system() == "Windows" else self._spawn_posix(argv)
        else:
            system = platform.system()
            if system == "Windows":
                choices = win_paths.get(player_name, [])
                for exe in choices:
                    if os.path.exists(exe):
                        argv = self._argv_for(player_name, exe, True, url)
                        ok, err = self._spawn_windows(argv)
                        if ok: break
                if not ok: err = err or f"Could not locate {player_name} executable."
            elif system == "Darwin":
                choices = mac_paths.get(player_name, [])
                for exe in choices:
                    if os.path.exists(exe):
                        argv = self._argv_for(player_name, exe, False, url)
                        ok, err = self._spawn_posix(argv)
                        if ok: break
                if not ok: err = err or f"Could not locate {player_name} app."
            else:
                cmd = linux_players.get(player_name)
                if cmd:
                    argv = self._argv_for(player_name, cmd, False, url)
                    ok, err = self._spawn_posix(argv)
                else: err = f"{player_name} is not configured for Linux."
        
        return ok, err

    def _argv_for(self, player_name: str, exe_or_cmd: str, is_windows: bool, url: str) -> list:
        # Build argv with best-effort single-instance/enqueue flags where supported.
        if player_name == "VLC":
            # Use single-instance flags, but do NOT enqueue so the new stream replaces playback.
            flags = ["--one-instance", "--one-instance-when-started-from-file"]
            return [exe_or_cmd, *flags, url]
        if player_name in ("MPC", "MPC-BE"):
            # Keep it simple and robust: let MPC's own single-instance setting
            # handle reuse; always pass the URL directly for predictable playback.
            return [exe_or_cmd, url]
        if player_name == "MPV":
            ipc = self._mpv_ipc_path()
            flags = [f"--input-ipc-server={ipc}", "--force-window=yes", "--idle=yes", "--no-terminal"]
            # On POSIX, if the IPC socket path exists but connect failed above, it's likely stale; try to remove it.
            if not is_windows and os.path.exists(ipc):
                try:
                    os.unlink(ipc)
                except Exception:
                    pass
            return [exe_or_cmd, *flags, url]
        # Default: just pass URL
        return [exe_or_cmd, url]

    def _spawn_posix(self, argv):
        try:
            subprocess.Popen(argv, close_fds=True)
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {argv[0]}"
        except Exception as e:
            return False, str(e)

    def _spawn_windows(self, argv):
        """Launch player on Windows with a normal, visible window."""
        try:
            subprocess.Popen(argv)
            return True, ""
        except FileNotFoundError:
            return False, f"Executable not found: {argv[0]}"
        except Exception as e:
            return False, str(e)

    def _mpv_ipc_path(self) -> str:
        if platform.system() == "Windows":
            return r"\\.\pipe\iptvclient-mpv"
        # POSIX: use a socket in temp
        return os.path.join(tempfile.gettempdir(), "iptvclient-mpv.sock")

    def _mpv_try_send(self, url: str) -> bool:
        """If an mpv instance with our IPC is running, send loadfile and return True."""
        ipc = self._mpv_ipc_path()
        payload = (json.dumps({"command": ["loadfile", url, "replace"]}) + "\n").encode("utf-8")
        if platform.system() == "Windows":
            try:
                # Minimal win32 named pipe client via ctypes
                from ctypes import wintypes
                import ctypes
                GENERIC_WRITE = 0x40000000
                OPEN_EXISTING = 3
                INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
                CreateFileW = ctypes.windll.kernel32.CreateFileW
                CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                CreateFileW.restype = wintypes.HANDLE
                h = CreateFileW(ipc, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
                if h == 0 or h == INVALID_HANDLE_VALUE:
                    return False
                try:
                    WriteFile = ctypes.windll.kernel32.WriteFile
                    WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
                    written = wintypes.DWORD(0)
                    ok = WriteFile(h, payload, len(payload), ctypes.byref(written), None)
                    return bool(ok and written.value == len(payload))
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                return False
        else:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.25)
                s.connect(ipc)
                s.sendall(payload)
                s.close()
                return True
            except Exception:
                return False

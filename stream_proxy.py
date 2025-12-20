
import http.server
import socketserver
import threading
import urllib.request
import urllib.parse
import socket
import logging
import json
import base64
import subprocess
import tempfile
import shutil
import os
import time
import hashlib
import queue

LOG = logging.getLogger(__name__)

class HLSConverter:
    def __init__(self, source_url, headers=None, transcode_profile: str = "auto"):
        self.source_url = source_url
        self.headers = headers or {}
        self.profile = transcode_profile
        self.user_agent = (
            self.headers.get("User-Agent")
            or self.headers.get("user-agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        self.temp_dir = tempfile.mkdtemp(prefix="iptv_remux_")
        self.process = None
        self.playlist_path = os.path.join(self.temp_dir, "stream.m3u8")
        self.last_access = time.time()
        self.start()

    def start(self):
        # Video HLS engine (piped)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-analyzeduration", "5000000", "-probesize", "5000000",
            "-fflags", "nobuffer+genpts+igndts",
            "-flags", "low_delay",
            "-i", "pipe:0",
            "-map", "0:v?", "-map", "0:a?",
            "-c:v", "copy",
            "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "320k", "-ac", "2", "-ar", "44100"
        ]

        hls_flags = "delete_segments+split_by_time+independent_segments+append_list+discont_start"
        cmd.extend([
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
            "-hls_flags", hls_flags, "-hls_segment_type", "mpegts",
            "-hls_version", "3", "-hls_init_time", "0", "-flush_packets", "1",
            "-start_number", "1", "-hls_segment_filename", os.path.join(self.temp_dir, "seg_%d.ts"),
            "-mpegts_flags", "pat_pmt_at_beginning",
            self.playlist_path
        ])

        LOG.info(f"Starting HLS engine for Video ({self.profile})")
        
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        try:
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=creation_flags)
            def _pump():
                try:
                    req = urllib.request.Request(self.source_url, headers=self.headers)
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        while self.process and self.process.poll() is None:
                            chunk = resp.read(32768)
                            if not chunk: break
                            try:
                                self.process.stdin.write(chunk)
                                self.process.stdin.flush()
                            except: break
                except Exception as e: LOG.error(f"HLS pump error: {e}")
                finally:
                    if self.process and self.process.stdin:
                        try: self.process.stdin.close()
                        except: pass
            threading.Thread(target=_pump, daemon=True).start()
        except Exception as e: LOG.error(f"FFmpeg HLS start failed: {e}")

    def stop(self):
        if self.process:
            try: self.process.terminate()
            except: pass
            self.process = None
        if os.path.exists(self.temp_dir):
            try: shutil.rmtree(self.temp_dir)
            except: pass

    def is_alive(self): return self.process and self.process.poll() is None
    def touch(self): self.last_access = time.time()

    def wait_for_playlist(self, timeout=10):
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(self.playlist_path) and os.path.getsize(self.playlist_path) > 100: return True
            if not self.is_alive(): return False
            time.sleep(0.2)
        return False


class StreamProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # 1. --- Route: /audio or /stream (High-Speed Direct Proxy) ---
        # Handles Radio (Direct/Transcode) and Video Gateway without 302 loops.
        if parsed.path in ('/audio', '/stream', '/proxy'):
            query = urllib.parse.parse_qs(parsed.query)
            target_url = query.get('url', [None])[0]
            if not target_url: return self.send_error(400)
            
            headers_json = query.get('headers', [None])[0]
            req_headers = {}
            if headers_json:
                try: req_headers = json.loads(base64.b64decode(headers_json).decode())
                except: pass
            
            # Default UA for radio compatibility
            if 'User-Agent' not in req_headers and 'user-agent' not in req_headers:
                req_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'

            # --- RADIO Path ---
            if "radio" in target_url.lower() or "streamon.fm" in target_url.lower() or parsed.path == '/audio':
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Icy-MetaData', '1')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                # Transcode to 320k MP3 if not already high-quality MP3
                # This handles Opus (RadioHD), AAC (CJSR), and others.
                is_mp3 = target_url.lower().endswith(".mp3") or "SerrebiRadio" in target_url
                if not is_mp3 or "RadioHD" in target_url or "CJSR" in target_url:
                    cmd = [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-user_agent", req_headers.get("User-Agent", "Mozilla/5.0"),
                        "-i", target_url, "-vn",
                        "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100",
                        "-f", "mp3", "pipe:1"
                    ]
                    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=creation_flags)
                    try:
                        while True:
                            chunk = proc.stdout.read(16384)
                            if not chunk: break
                            self.wfile.write(chunk)
                    except: pass
                    finally:
                        try: proc.terminate()
                        except: pass
                else:
                    # Standard MP3 (320k): Direct Byte Proxy
                    req = urllib.request.Request(target_url, headers=req_headers)
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            while True:
                                chunk = resp.read(32768)
                                if not chunk: break
                                self.wfile.write(chunk)
                    except: pass
                return

            # --- VIDEO Path (HLS Redirect) ---
            hls_url = get_proxy().get_transcoded_url(target_url, headers=req_headers, transcode_profile="auto")
            self.send_response(302)
            self.send_header('Location', hls_url)
            self.end_headers()
            return

        # 3. --- Route: /transcode/<session_id>/... ---
        if parsed.path.startswith('/transcode/'):
            parts = parsed.path.split('/')
            if len(parts) >= 4:
                session_id, filename = parts[2], parts[3]
                converter = get_proxy().get_converter(session_id)
                if not converter: return self.send_error(404)
                converter.touch()

                if filename == "stream.m3u8":
                    # Instant response with bootstrap if real segments aren't ready
                    if not converter.wait_for_playlist(timeout=3):
                        data = (
                            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
                            "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-DISCONTINUITY\n"
                            "#EXTINF:1.0,\n"
                            f"http://{get_proxy().host}:{get_proxy().port}/bootstrap.ts\n"
                        ).encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(data)
                        return

                    # Real playlist rewrite
                    try:
                        with open(converter.playlist_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        base = f"http://{get_proxy().host}:{get_proxy().port}/transcode/{session_id}/"
                        rewritten = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", "#EXT-X-DISCONTINUITY"]
                        for line in lines:
                            line = line.strip()
                            if not line or line.startswith("#EXTM3U") or line.startswith("#EXT-X-VERSION"): continue
                            if not line.startswith("#"): rewritten.append(base + line)
                            else: rewritten.append(line)
                        data = "\n".join(rewritten).encode("utf-8")
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(data)
                    except: self.send_error(500)
                    return
                
                # Serve segments
                file_path = os.path.join(converter.temp_dir, filename)
                if not os.path.exists(file_path): return self.send_error(404)
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp2t')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                try:
                    with open(file_path, 'rb') as f: shutil.copyfileobj(f, self.wfile)
                except: pass
                return

        # 4. --- Route: /bootstrap.ts (1s black segment) ---
        if parsed.path == '/bootstrap.ts':
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=10:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-b:v", "1M",
                "-c:a", "aac", "-b:a", "64k", "-f", "mpegts", "-muxrate", "2M", "pipe:1"
            ]
            creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=creation_flags)
            try:
                data = proc.stdout.read()
                if data: self.wfile.write(data)
            except: pass
            finally:
                try: proc.terminate()
                except: pass
            return

        self.send_error(404)


class StreamProxy:
    def __init__(self):
        self.server = None
        self.thread = None
        self.port = 0
        self.host = self._get_local_ip()
        self.converters = {}
        self.lock = threading.Lock()
        self._cleanup_thread = None
        self._running = False

    def _get_local_ip(self):
        """Robust primary IP detection for Chromecast compatibility."""
        try:
            # Method 1: Connected socket (fastest, most accurate for primary route)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith('127.'): return ip
        except: pass

        try:
            # Method 2: Hostname lookup
            ip = socket.gethostbyname(socket.gethostname())
            if ip and not ip.startswith('127.'): return ip
        except: pass

        try:
            # Method 3: Interface scan (last resort)
            # We don't assume netifaces is installed here, use standard socket
            for addr in socket.getaddrinfo(socket.gethostname(), None):
                ip = addr[4][0]
                if '.' in ip and not ip.startswith('127.'): return ip
        except: pass

        return '127.0.0.1'

    def start(self):
        if self.server: return
        self.server = socketserver.ThreadingTCPServer((self.host, 0), StreamProxyHandler)
        self.port = self.server.server_address[1]
        self._running = True
        self._ensure_firewall_rule()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        LOG.info(f"Proxy started at http://{self.host}:{self.port}")

    def stop(self):
        self._running = False
        if self.server: self.server.shutdown()
        with self.lock:
            for c in self.converters.values(): c.stop()
            self.converters.clear()

    def get_stream_url(self, target_url, headers=None, mode="auto"):
        params = {'url': target_url, 'mode': mode}
        if headers:
            if isinstance(headers, dict):
                clean = {k: str(v) for k, v in headers.items() if v is not None and k != '_extra'}
                params['headers'] = base64.b64encode(json.dumps(clean).encode()).decode()
            else: params['headers'] = headers
        return f"http://{self.host}:{self.port}/stream?{urllib.parse.urlencode(params)}"

    def get_audio_url(self, target_url, headers=None):
        return self.get_stream_url(target_url, headers, mode="audio")

    def get_transcoded_url(self, target_url, headers=None, transcode_profile="auto"):
        tag = transcode_profile
        session_id = hashlib.md5(f"{target_url}|{tag}".encode()).hexdigest()
        with self.lock:
            if session_id not in self.converters:
                self.converters[session_id] = HLSConverter(target_url, headers, transcode_profile)
            else: self.converters[session_id].touch()
        return f"http://{self.host}:{self.port}/transcode/{session_id}/stream.m3u8"

    def get_converter(self, session_id):
        with self.lock: return self.converters.get(session_id)

    def _cleanup_loop(self):
        while self._running:
            time.sleep(10)
            now = time.time()
            with self.lock:
                dead = [sid for sid, c in self.converters.items() if now - c.last_access > 60]
                for sid in dead:
                    self.converters[sid].stop()
                    del self.converters[sid]

    def _ensure_firewall_rule(self):
        if os.name != "nt" or not self.port: return
        rule_name = f"IPTV Proxy ({self.port})"
        try:
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"], capture_output=True, creationflags=flags)
            subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={rule_name}", "dir=in", "action=allow", "protocol=TCP", f"localport={self.port}", "profile=private,domain"], capture_output=True, creationflags=flags)
        except: pass

_PROXY = StreamProxy()
def get_proxy(): return _PROXY

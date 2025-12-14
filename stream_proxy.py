
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

LOG = logging.getLogger(__name__)

class HLSConverter:
    def __init__(self, source_url, headers=None):
        """Helper that remuxes a source URL to local HLS for Chromecast.

        Some IPTV providers require additional HTTP headers (cookies, referer,
        authorization, etc.) for the stream to remain valid. When we transcode
        MPEG-TS to HLS for Chromecast, ffmpeg must send those headers too or
        the remote server may drop the connection shortly after start.  To
        handle this, we keep the full headers dict and forward it via ffmpeg's
        -headers option in addition to an explicit -user_agent when present.
        """
        self.source_url = source_url
        self.headers = headers or {}
        # Normalise user-agent key and keep a dedicated copy for the ffmpeg
        # -user_agent option (some servers are picky about this).
        self.user_agent = (
            self.headers.get("User-Agent")
            or self.headers.get("user-agent")
        )
        self.temp_dir = tempfile.mkdtemp(prefix="iptv_remux_")
        self.process = None
        self.playlist_path = os.path.join(self.temp_dir, "stream.m3u8")
        self.last_access = time.time()
        self.start_ts = time.time()
        self.start()
    def start(self):
        # -re is NOT used because we want to fill buffer fast; the upstream
        # server or network will naturally limit the effective rate.
        # -c copy keeps CPU usage low by avoiding re-encoding.
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
        ]

        # Forward headers that some providers require (cookies, referer, auth, etc.)
        # in addition to a dedicated -user_agent option.
        if self.user_agent:
            cmd.extend(["-user_agent", self.user_agent])

        if self.headers:
            extra = []
            for k, v in self.headers.items():
                if v is None:
                    continue
                # user-agent is already handled via -user_agent above
                if k.lower() == "user-agent":
                    continue
                extra.append(f"{k}: {v}\r\n")
            if extra:
                header_blob = "".join(extra)
                cmd.extend(["-headers", header_blob])

        cmd.extend([
            "-i", self.source_url,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "3",
            "-hls_list_size", "20",
            "-hls_flags", "delete_segments+split_by_time",
            "-hls_segment_filename", os.path.join(self.temp_dir, "seg_%03d.ts"),
            self.playlist_path,
        ])

        LOG.info(f"Starting ffmpeg remux to {self.temp_dir}")

        if not shutil.which("ffmpeg"):
            LOG.error("ffmpeg not found in PATH. Transcoding impossible.")
            return

        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        try:
            self.process = subprocess.Popen(cmd, creationflags=creation_flags)
        except Exception as e:
            LOG.error(f"Failed to start ffmpeg: {e}")
    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        
        # Cleanup files
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                LOG.warning(f"Failed to cleanup temp dir {self.temp_dir}: {e}")

    def is_alive(self):
        return self.process and self.process.poll() is None

    def touch(self):
        self.last_access = time.time()

    def wait_for_playlist(self, timeout=15):
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(self.playlist_path) and os.path.getsize(self.playlist_path) > 0:
                return True
            if not self.is_alive():
                return False
            time.sleep(0.5)
        return False


class StreamProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # --- Route: /transcode/<session_id>/<filename> ---
        if parsed.path.startswith('/transcode/'):
            parts = parsed.path.split('/')
            if len(parts) >= 4:
                session_id = parts[2]
                filename = parts[3]

                converter = get_proxy().get_converter(session_id)
                if not converter:
                    self.send_error(404, "Session expired or not found")
                    return

                converter.touch()

                if filename == "stream.m3u8":
                    # Wait if not ready
                    if not converter.wait_for_playlist():
                        self.send_error(503, "Playlist generation failed or timed out")
                        return

                    file_path = converter.playlist_path
                    content_type = "application/x-mpegURL"
                else:
                    file_path = os.path.join(converter.temp_dir, filename)
                    content_type = "video/mp2t"

                if not os.path.exists(file_path):
                    self.send_error(404, "File not found")
                    return

                try:
                    with open(file_path, 'rb') as f:
                        content = f.read()
                        self.send_response(200)
                        self.send_header('Content-Type', content_type)
                        self.send_header('Content-Length', str(len(content)))
                        self.send_header('Access-Control-Allow-Origin', '*')
                        if filename.endswith('.m3u8'):
                            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                            self.send_header('Pragma', 'no-cache')
                            self.send_header('Expires', '0')
                        self.end_headers()
                        self.wfile.write(content)
                except Exception as e:
                    LOG.error(f"Error serving transcode file: {e}")
                    try:
                        self.send_error(500, "Internal server error")
                    except Exception:
                        pass
                return

            # Bad transcode path
            self.send_error(400, "Invalid transcode path")
            return

        # --- Route: /proxy (Standard pass-through) ---
        if parsed.path != '/proxy':
            self.send_error(404, "Not Found")
            return

        query = urllib.parse.parse_qs(parsed.query)
        target_url = query.get('url', [None])[0]

        if not target_url:
            self.send_error(400, "Missing url parameter")
            return

        # Reconstruct headers
        req_headers = {}
        headers_json = query.get('headers', [None])[0]
        if headers_json:
            try:
                decoded = base64.b64decode(headers_json).decode('utf-8')
                req_headers = json.loads(decoded)
            except Exception as e:
                LOG.warning("Failed to decode headers: %s", e)

        if 'User-Agent' not in req_headers and 'user-agent' not in req_headers:
            req_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

        try:
            LOG.info(f"Proxying: {target_url}")
            req = urllib.request.Request(target_url, headers=req_headers)

            with urllib.request.urlopen(req, timeout=10) as response:
                self.send_response(response.status)

                content_type_override = None
                path = urllib.parse.urlparse(target_url).path
                is_m3u8 = path.endswith('.m3u8')

                sent_content_type = False
                for k, v in response.getheaders():
                    lk = k.lower()
                    if lk == 'content-length':
                        continue
                    if lk == 'content-type':
                        if is_m3u8:
                            content_type_override = 'application/vnd.apple.mpegurl'
                            sent_content_type = True
                            continue
                        sent_content_type = True
                    self.send_header(k, v)

                if content_type_override and not sent_content_type:
                    self.send_header('Content-Type', content_type_override)

                if is_m3u8:
                    content = response.read()
                    try:
                        text = content.decode('utf-8', errors='ignore')
                        new_lines = []
                        base_url = response.geturl()
                        headers_param = f"&headers={query.get('headers', [''])[0]}" if query.get('headers') else ""

                        for line in text.splitlines():
                            s = line.strip()
                            if not s:
                                new_lines.append(s)
                                continue
                            if s.startswith('#'):
                                if s.startswith('#EXT-X-KEY:'):
                                    parts = s.split('URI="')
                                    if len(parts) > 1:
                                        pre = parts[0]
                                        rest = parts[1]
                                        if '"' in rest:
                                            uri, post = rest.split('"', 1)
                                            abs_uri = urllib.parse.urljoin(base_url, uri)
                                            enc_uri = urllib.parse.quote(abs_uri)
                                            proxied_uri = f"/proxy?url={enc_uri}{headers_param}"
                                            new_lines.append(f'{pre}URI="{proxied_uri}"{post}')
                                        else:
                                            new_lines.append(s)
                                    else:
                                        new_lines.append(s)
                                else:
                                    new_lines.append(s)
                            else:
                                abs_uri = urllib.parse.urljoin(base_url, s)
                                enc_uri = urllib.parse.quote(abs_uri)
                                proxied = f"/proxy?url={enc_uri}{headers_param}"
                                new_lines.append(proxied)

                        out = "\n".join(new_lines).encode('utf-8')
                        self.send_header('Content-Length', str(len(out)))
                        self.end_headers()
                        self.wfile.write(out)
                    except Exception as e:
                        LOG.error("Failed to rewrite m3u8: %s", e)
                        self.end_headers()
                        self.wfile.write(content)
                else:
                    self.end_headers()
                    try:
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                    except Exception as e:
                        LOG.error("Error writing to client: %s", e)
                        pass
        except Exception as e:
            LOG.error("Proxy error: %s", e)
            try:
                self.send_error(500, str(e))
            except Exception:
                pass
    # def log_message(self, format, *args):
    #     # Mute standard access logs to reduce noise
    #     pass

class StreamProxy:
    def __init__(self):
        self.server = None
        self.thread = None
        self.port = 0
        self.host = self._get_local_ip()
        self.converters = {} # session_id -> HLSConverter
        self.lock = threading.Lock()
        self._cleanup_thread = None
        self._running = False

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def start(self):
        if self.server:
            return
        
        self.server = socketserver.ThreadingTCPServer((self.host, 0), StreamProxyHandler)
        self.port = self.server.server_address[1]
        self._running = True
        
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        
        LOG.info(f"Stream proxy started at http://{self.host}:{self.port}/proxy")

    def stop(self):
        self._running = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread = None
        
        with self.lock:
            for c in self.converters.values():
                c.stop()
            self.converters.clear()

    def get_proxied_url(self, target_url, headers=None):
        if not self.server:
            self.start()
            
        params = {'url': target_url}
        if headers:
            clean_headers = {k: str(v) for k, v in headers.items() if v is not None and k != '_extra'}
            json_str = json.dumps(clean_headers)
            b64_str = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
            params['headers'] = b64_str
            
        query = urllib.parse.urlencode(params)
        return f"http://{self.host}:{self.port}/proxy?{query}"

    def get_transcoded_url(self, target_url, headers=None):
        if not self.server:
            self.start()
            
        # Generate session ID based on URL
        session_id = hashlib.md5(target_url.encode('utf-8')).hexdigest()
        
        with self.lock:
            if session_id not in self.converters:
                self.converters[session_id] = HLSConverter(target_url, headers)
            else:
                self.converters[session_id].touch()
                
        return f"http://{self.host}:{self.port}/transcode/{session_id}/stream.m3u8"

    def get_converter(self, session_id):
        with self.lock:
            return self.converters.get(session_id)

    def _cleanup_loop(self):
        while self._running:
            time.sleep(10)
            now = time.time()
            dead = []
            with self.lock:
                for sid, conv in self.converters.items():
                    if now - conv.last_access > 60: # 1 minute idle timeout
                        conv.stop()
                        dead.append(sid)
                for sid in dead:
                    del self.converters[sid]

# Global instance
_PROXY = StreamProxy()

def get_proxy():
    return _PROXY
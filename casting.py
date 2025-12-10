"""Unified casting module for Chromecast, DLNA/UPnP, and AirPlay.

Provides a simple interface to discover and cast to network media devices.
Each device is labeled with its type for easy identification.
"""

import asyncio
import logging
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


class CastProtocol(Enum):
    """Supported casting protocols."""
    CHROMECAST = "Chromecast"
    DLNA = "DLNA"
    UPNP = "UPnP"
    AIRPLAY = "AirPlay"


@dataclass
class CastDevice:
    """Represents a discovered casting device."""
    name: str
    protocol: CastProtocol
    identifier: str  # Unique ID for reconnection
    host: str
    port: int
    # Protocol-specific metadata
    metadata: Dict = field(default_factory=dict)
    
    @property
    def display_name(self) -> str:
        """User-friendly name with protocol label."""
        return f"{self.name} [{self.protocol.value}]"
    
    @property
    def unique_id(self) -> str:
        """Unique identifier for this device."""
        return f"{self.protocol.value}:{self.identifier}"


class CastError(Exception):
    """Base exception for casting errors."""
    pass


class DeviceNotFoundError(CastError):
    """Device could not be found on the network."""
    pass


class ConnectionError(CastError):
    """Failed to connect to device."""
    pass


class PlaybackError(CastError):
    """Failed to start or control playback."""
    pass


class BaseCaster(ABC):
    """Abstract base class for protocol-specific casters."""
    
    @abstractmethod
    async def discover(self, timeout: float = 5.0) -> List[CastDevice]:
        """Discover devices on the network."""
        pass
    
    @abstractmethod
    async def connect(self, device: CastDevice) -> None:
        """Connect to a specific device."""
        pass
    
    @abstractmethod
    async def play(self, url: str, title: str = "IPTV Stream", 
                   content_type: str = "video/mp2t", headers: Optional[Dict[str, str]] = None) -> None:
        """Start playing a stream URL."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop playback."""
        pass
    
    @abstractmethod
    async def pause(self) -> None:
        """Pause playback."""
        pass
    
    @abstractmethod
    async def resume(self) -> None:
        """Resume playback."""
        pass
    
    @abstractmethod
    async def set_volume(self, level: float) -> None:
        """Set volume (0.0 to 1.0)."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from device."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to a device."""
        pass


# ============================================================================
# Chromecast Implementation
# ============================================================================

try:
    import pychromecast
    from pychromecast.controllers.media import MediaController
    _HAS_CHROMECAST = True
except ImportError:
    _HAS_CHROMECAST = False
    pychromecast = None

from stream_proxy import get_proxy

class ChromecastCaster(BaseCaster):
    """Chromecast protocol implementation."""
    
    def __init__(self):
        if not _HAS_CHROMECAST:
            raise CastError("pychromecast is not installed. Run: pip install pychromecast")
        self._cast = None
        self._browser = None
        self._devices: Dict[str, any] = {}
        # Ensure proxy is ready
        get_proxy().start()
    
    async def discover(self, timeout: float = 5.0) -> List[CastDevice]:
        """Discover Chromecast devices."""
        devices = []
        
        # Run discovery in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        
        def do_discovery():
            try:
                # Use the standard discovery function
                # This returns a list of Playable/Cast objects and the browser, but we just want discovery info here.
                # get_chromecasts returns (casts, browser). blocking=True waits until timeout.
                casts, browser = pychromecast.get_chromecasts(timeout=timeout)
                # We need to stop the discovery/browser or it keeps running
                browser.stop_discovery()
                return casts
            except Exception as e:
                LOG.warning("Chromecast discovery error: %s", e)
                return []
        
        discovered_casts = await loop.run_in_executor(None, do_discovery)
        
        for cast in discovered_casts:
            try:
                # cast is a Chromecast object
                # Recent pychromecast versions store details in cast_info
                host = getattr(cast.cast_info, "host", None) or getattr(cast, "host", None)
                port = getattr(cast.cast_info, "port", None) or getattr(cast, "port", 8009)
                uuid = str(cast.uuid)
                
                device = CastDevice(
                    name=cast.name or cast.cast_info.friendly_name or f"Chromecast {uuid[:8]}",
                    protocol=CastProtocol.CHROMECAST,
                    identifier=uuid,
                    host=host,
                    port=port,
                    metadata={
                        "uuid": uuid,
                        "model_name": cast.model_name,
                        "manufacturer": "Google",
                        "cast_type": cast.cast_type,
                    }
                )
                devices.append(device)
                # Cache the cast object? No, better to get a fresh one on connect
                # or we can cache the connection info.
            except Exception as e:
                LOG.debug("Failed to process Chromecast device %s: %s", cast, e)
        
        return devices
    
    async def connect(self, device: CastDevice) -> None:
        """Connect to a Chromecast device."""
        loop = asyncio.get_event_loop()
        
        def do_connect():
            try:
                # Try by UUID first using get_listed_chromecasts
                # This performs discovery for the specific UUID
                chromecasts, browser = pychromecast.get_listed_chromecasts(
                    uuids=[device.identifier]
                )
                if not chromecasts:
                    # If strictly searching by UUID failed, try searching by known IP
                    # This is useful if the UUID changed or discovery is flaky but IP is known
                    # However, get_chromecasts(known_hosts=[...]) is better
                    chromecasts, browser = pychromecast.get_chromecasts(known_hosts=[device.host])
                    # Filter for the one we want if possible, or take the first one at that IP
                    if chromecasts:
                        # If we have a UUID, match it. If not, just take the first one (fallback)
                        for cc in chromecasts:
                            if str(cc.uuid) == device.identifier:
                                cast = cc
                                break
                        else:
                            cast = chromecasts[0]
                    else:
                        raise ConnectionError(f"Could not find Chromecast {device.name} at {device.host}")
                else:
                    cast = chromecasts[0]
                
                cast.wait()
                return cast, browser
            except Exception as e:
                raise ConnectionError(f"Failed to connect to {device.name}: {e}")
        
        self._cast, self._browser = await loop.run_in_executor(None, do_connect)
    
    async def play(self, url: str, title: str = "IPTV Stream",
                   content_type: str = "video/mp2t", headers: Optional[Dict[str, str]] = None) -> None:
        """Play a stream on Chromecast."""
        if not self._cast:
            raise ConnectionError("Not connected to a Chromecast device")
        
        loop = asyncio.get_event_loop()
        
        def do_play():
            mc = self._cast.media_controller
            
            content_type_actual = _detect_mime_type(url, content_type)
            
            # Determine stream type
            stream_type = "LIVE" if content_type_actual == "application/x-mpegURL" else "BUFFERED"
            
            # Use local proxy to handle headers and format compatibility
            if content_type_actual == "video/mp2t":
                # Transcode MPEG-TS to HLS for better Chromecast compatibility
                proxied_url = get_proxy().get_transcoded_url(url, headers)
                stream_type = "LIVE" # HLS is live
                content_type_actual = "application/x-mpegURL" # We are serving HLS now
                LOG.info(f"Remuxing MPEG-TS to HLS via proxy: {proxied_url}")
            else:
                # Just proxy headers
                proxied_url = get_proxy().get_proxied_url(url, headers)
                LOG.info(f"Casting to Chromecast via proxy: {proxied_url} -> {url}")
            
            # Note: Older/Standard versions of pychromecast's play_media do not support custom_data kwarg.
            # It was added in newer versions or specific forks. We'll omit it to be safe.
            # If headers are absolutely required, a custom receiver or proxy is needed.
            mc.play_media(
                proxied_url,
                content_type_actual,
                title=title,
                stream_type=stream_type
            )
            mc.block_until_active(timeout=10)
        
        await loop.run_in_executor(None, do_play)
    
    async def stop(self) -> None:
        if self._cast:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._cast.media_controller.stop)
    
    async def pause(self) -> None:
        if self._cast:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._cast.media_controller.pause)
    
    async def resume(self) -> None:
        if self._cast:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._cast.media_controller.play)
    
    async def set_volume(self, level: float) -> None:
        if self._cast:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._cast.set_volume, max(0.0, min(1.0, level)))
    
    async def disconnect(self) -> None:
        if self._cast:
            try:
                self._cast.disconnect()
            except Exception:
                pass
            self._cast = None
        if self._browser:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None
    
    def is_connected(self) -> bool:
        return self._cast is not None


# ============================================================================
# DLNA/UPnP Implementation
# ============================================================================

try:
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.aiohttp import AiohttpRequester
    from async_upnp_client.profiles.dlna import DmrDevice
    from async_upnp_client.search import async_search
    from async_upnp_client.ssdp import SSDP_TARGET_V1
    _HAS_UPNP = True
except ImportError:
    _HAS_UPNP = False


class DLNACaster(BaseCaster):
    """DLNA/UPnP protocol implementation."""
    
    # UPnP service types for media renderers
    MEDIA_RENDERER_TYPES = [
        "urn:schemas-upnp-org:device:MediaRenderer:1",
        "urn:schemas-upnp-org:device:MediaRenderer:2",
    ]
    
    def __init__(self):
        if not _HAS_UPNP:
            raise CastError("async-upnp-client is not installed. Run: pip install async-upnp-client")
        self._device: Optional[DmrDevice] = None
        self._factory: Optional[UpnpFactory] = None
        self._requester = None
    
    async def discover(self, timeout: float = 5.0) -> List[CastDevice]:
        """Discover DLNA/UPnP media renderers."""
        devices = []
        seen_usns = set()
        
        LOG.debug("Starting DLNA discovery (timeout=%.1fs)", timeout)
        
        async def process_response(response):
            try:
                usn = response.get("usn", "")
                st = response.get("st", "")
                location = response.get("location", "")
                
                # LOG.debug("DLNA Scan: Found USN=%s ST=%s Loc=%s", usn, st, location)

                if usn in seen_usns:
                    return
                seen_usns.add(usn)
                
                if not location:
                    return
                
                # Check if it is a media renderer
                is_renderer = any(rt in st for rt in self.MEDIA_RENDERER_TYPES)
                # Also accept ssdp:all responses that have MediaRenderer in USN
                if not is_renderer and "MediaRenderer" not in usn:
                    # LOG.debug("Ignoring non-renderer: %s", usn)
                    return
                
                LOG.info("Found DLNA Renderer: %s", usn)
                
                # Extract host/port from location
                from urllib.parse import urlparse
                parsed = urlparse(location)
                host = parsed.hostname or ""
                port = parsed.port or 80
                
                # Get friendly name from cache-control or USN
                name = usn.split("::")[0] if "::" in usn else usn
                if name.startswith("uuid:"):
                    name = f"DLNA Device {name[5:13]}"
                
                if "DLNA" in st.upper() or "dlna" in location.lower():
                    protocol = CastProtocol.DLNA
                else:
                    protocol = CastProtocol.UPNP
                
                device = CastDevice(
                    name=name,
                    protocol=protocol,
                    identifier=usn,
                    host=host,
                    port=port,
                    metadata={
                        "location": location,
                        "st": st,
                        "usn": usn,
                    }
                )
                devices.append(device)
                
            except Exception as e:
                LOG.debug("Failed to process DLNA device: %s", e)
        
        try:
            # Search for media renderers
            # We explicitly search for MediaRenderer:1 to reduce noise, and ssdp:all as backup
            targets = [self.MEDIA_RENDERER_TYPES[0], SSDP_TARGET_V1]
            for target in targets:
                await async_search(process_response, timeout=timeout/len(targets), service_type=target)
        except Exception as e:
            LOG.warning("DLNA discovery error: %s", e)
        
        LOG.debug("DLNA discovery finished. Found %d candidates. Fetching names...", len(devices))
        
        # Fetch friendly names for discovered devices
        await self._fetch_device_names(devices)
        
        return devices
    
    async def _fetch_device_names(self, devices: List[CastDevice]) -> None:
        """Fetch proper friendly names from device descriptions."""
        for device in devices:
            try:
                location = device.metadata.get("location", "")
                if not location:
                    continue
                
                requester = AiohttpRequester()
                factory = UpnpFactory(requester)
                upnp_device = await factory.async_create_device(location)
                
                if upnp_device.friendly_name:
                    device.name = upnp_device.friendly_name
                if upnp_device.manufacturer:
                    device.metadata["manufacturer"] = upnp_device.manufacturer
                if upnp_device.model_name:
                    device.metadata["model_name"] = upnp_device.model_name
                
                # Refine protocol based on device info
                model = (upnp_device.model_name or "").lower()
                manufacturer = (upnp_device.manufacturer or "").lower()
                if "dlna" in model or "dlna" in manufacturer:
                    device.protocol = CastProtocol.DLNA
                
                await requester.async_close()
                
            except Exception as e:
                LOG.debug("Failed to fetch device name for %s: %s", device.identifier, e)
    
    async def connect(self, device: CastDevice) -> None:
        """Connect to a DLNA/UPnP device."""
        try:
            location = device.metadata.get("location", "")
            if not location:
                raise ConnectionError(f"No location URL for device {device.name}")
            
            self._requester = AiohttpRequester()
            self._factory = UpnpFactory(self._requester)
            upnp_device = await self._factory.async_create_device(location)
            self._device = DmrDevice(upnp_device, None)
            
        except Exception as e:
            await self.disconnect()
            raise ConnectionError(f"Failed to connect to {device.name}: {e}")
    
    async def play(self, url: str, title: str = "IPTV Stream",
                   content_type: str = "video/mp2t", headers: Optional[Dict[str, str]] = None) -> None:
        """Play a stream on DLNA device."""
        if not self._device:
            raise ConnectionError("Not connected to a DLNA device")
        
        # Improved MIME type detection
        content_type = _detect_mime_type(url, content_type)
        
        try:
            # Build DIDL-Lite metadata
            # Add DLNA flags for better compatibility (Samsung, LG, Sony)
            # DLNA.ORG_OP=01 (Seek supported), DLNA.ORG_CI=0 (Transcoded=0)
            dlna_features = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
            res_attrs = f'protocolInfo="http-get:*:{content_type}:{dlna_features}"'
            
            if headers:
                # Common headers might be passed as a custom element or as part of protocolInfo
                # This is a best-effort attempt as standard doesn't define well.
                # User-Agent, Referer are most common.
                user_agent = headers.get("user-agent")
                referer = headers.get("referer")
                
                if user_agent:
                    res_attrs += f' http-user-agent="{user_agent}"'
                if referer:
                    res_attrs += f' http-referer="{referer}"'
                # Other headers are harder to embed portably.
            
            didl = f'''<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
                xmlns:dc="http://purl.org/dc/elements/1.1/"
                xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
                <item id="1" parentID="0" restricted="1">
                    <dc:title>{title}</dc:title>
                    <upnp:class>object.item.videoItem.videoBroadcast</upnp:class>
                    <res {res_attrs}>{url}</res>
                </item>
            </DIDL-Lite>'''
            
            await self._device.async_set_transport_uri(url, title, didl)
            await self._device.async_play()
            
        except Exception as e:
            raise PlaybackError(f"Failed to start playback: {e}")
    
    async def stop(self) -> None:
        if self._device:
            try:
                await self._device.async_stop()
            except Exception as e:
                LOG.debug("DLNA stop error: %s", e)
    
    async def pause(self) -> None:
        if self._device:
            try:
                await self._device.async_pause()
            except Exception as e:
                LOG.debug("DLNA pause error: %s", e)
    
    async def resume(self) -> None:
        if self._device:
            try:
                await self._device.async_play()
            except Exception as e:
                LOG.debug("DLNA resume error: %s", e)
    
    async def set_volume(self, level: float) -> None:
        if self._device:
            try:
                # DLNA volume is 0-100
                await self._device.async_set_volume_level(max(0.0, min(1.0, level)))
            except Exception as e:
                LOG.debug("DLNA set_volume error: %s", e)
    
    async def disconnect(self) -> None:
        self._device = None
        self._factory = None
        if self._requester:
            try:
                await self._requester.async_close()
            except Exception:
                pass
            self._requester = None
    
# ============================================================================
# AirPlay Implementation
# ============================================================================

try:
    import pyatv
    from pyatv import conf
    _HAS_AIRPLAY = True
except ImportError:
    _HAS_AIRPLAY = False


class AirPlayCaster(BaseCaster):
    """AirPlay protocol implementation using pyatv."""
    
    def __init__(self):
        if not _HAS_AIRPLAY:
            raise CastError("pyatv is not installed. Run: pip install pyatv")
        self._atv = None
        
    async def discover(self, timeout: float = 5.0) -> List[CastDevice]:
        """Discover AirPlay devices."""
        devices = []
        try:
            atvs = await pyatv.scan(loop=asyncio.get_event_loop(), timeout=int(timeout))
            
            for atv in atvs:
                # Prefer AirPlay protocol
                service = atv.get_service(conf.Protocol.AirPlay)
                if not service:
                    continue
                
                # atv is a BaseConfig which has address (IPv4/IPv6)
                # service is BaseService (or MutableService) which has port
                host = str(atv.address) if atv.address else ""
                
                device = CastDevice(
                    name=atv.name,
                    protocol=CastProtocol.AIRPLAY,
                    identifier=atv.identifier,
                    host=host,
                    port=service.port,
                    metadata={
                        "conf": atv
                    }
                )
                devices.append(device)
        except Exception as e:
            LOG.warning("AirPlay discovery error: %s", e)
            
        return devices
    
    async def connect(self, device: CastDevice, credentials: Optional[str] = None) -> None:
        """Connect to an AirPlay device."""
        config = device.metadata.get("conf")
        
        # Try to connect with cached config first
        try:
            if not config:
                raise ConnectionError("Missing AirPlay configuration")
            
            if credentials:
                config.set_credentials(conf.Protocol.AirPlay, credentials)

            self._atv = await pyatv.connect(config, loop=asyncio.get_event_loop())
            return
        except Exception:
            # Fallback: re-scan and retry once
            pass
            
        try:
            # Re-scan to get fresh config (handles IP changes, ephemeral ports, loop affinity)
            LOG.info(f"Re-scanning for {device.identifier}...")
            atvs = await pyatv.scan(identifier=device.identifier, loop=asyncio.get_event_loop(), timeout=3)
            if atvs:
                config = atvs[0]
                # Update metadata reference for future use
                device.metadata["conf"] = config
            
            if not config:
                 raise ConnectionError("Device not found during re-scan")

            if credentials:
                config.set_credentials(conf.Protocol.AirPlay, credentials)
            
            self._atv = await pyatv.connect(config, loop=asyncio.get_event_loop())
            
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {device.name}: {e}")

    async def start_pairing(self, device: CastDevice) -> object:
        """Start pairing process. Returns a pyatv PairingHandler."""
        # Always re-scan before pairing to ensure we have the latest state/loop context
        config = device.metadata.get("conf")
        try:
            atvs = await pyatv.scan(identifier=device.identifier, loop=asyncio.get_event_loop(), timeout=3)
            if atvs:
                config = atvs[0]
                device.metadata["conf"] = config
        except Exception as e:
            LOG.warning("Re-scan for pairing failed: %s", e)
            
        if not config:
            raise CastError("Missing configuration")
        
        # Ensure we start with a clean slate for credentials to avoid "not authenticated"
        # if stale garbage is present.
        config.set_credentials(conf.Protocol.AirPlay, None)
        
        return await pyatv.pair(config, conf.Protocol.AirPlay, loop=asyncio.get_event_loop())
    
    async def play(self, url: str, title: str = "IPTV Stream",
                   content_type: str = "video/mp2t", headers: Optional[Dict[str, str]] = None) -> None:
        """Play a stream on AirPlay."""
        if not self._atv:
            raise ConnectionError("Not connected to an AirPlay device")
        
        try:
            stream = self._atv.stream
            # pyatv's play_url doesn't directly support custom HTTP headers
            # However, some headers might be embedded in the URL itself if needed
            # For most AirPlay devices, this is not a common requirement as they
            # often fetch content directly without needing custom headers from the client.
            # If a stream requires custom headers, a proxy might be needed or
            # pyatv's capabilities extended.
            await stream.play_url(url, position=0)
        except Exception as e:
            # pyatv 0.14+ raises NotSupportedError if the connected protocol doesn't support streaming (e.g. RAOP only)
            if "NotSupportedError" in str(type(e).__name__):
                 raise PlaybackError("This AirPlay device does not support video streaming (play_url). It might be an audio-only device or connected via limited protocol.")
            raise PlaybackError(f"Failed to start playback: {e}")
    
    async def stop(self) -> None:
        if self._atv:
            try:
                # AirPlay doesn't strictly have 'stop', but we can close the connection
                # or try to stop media. 'remote_control.stop()' is for remote buttons.
                # play_url usually stops when connection closes or replaced.
                pass
            except Exception:
                pass
    
    async def pause(self) -> None:
        if self._atv:
            try:
                await self._atv.remote_control.pause()
            except Exception:
                pass
    
    async def resume(self) -> None:
        if self._atv:
            try:
                await self._atv.remote_control.play()
            except Exception:
                pass
    
    async def set_volume(self, level: float) -> None:
        if self._atv:
            try:
                # pyatv audio set_volume is 0-100
                await self._atv.audio.set_volume(level * 100)
            except Exception:
                pass
    
    async def disconnect(self) -> None:
        if self._atv:
            try:
                self._atv.close()
            except Exception:
                pass
            self._atv = None
    
    def is_connected(self) -> bool:
        return self._atv is not None


def _detect_mime_type(url: str, default: str = "video/mp2t") -> str:
    """Detect MIME type from URL with improved heuristics for IPTV."""
    u = url.lower()
    if ".m3u8" in u:
        return "application/x-mpegURL"
    elif ".ts" in u:
        return "video/mp2t"
    elif ".mp4" in u:
        return "video/mp4"
    elif ".mkv" in u:
        return "video/x-matroska"
    elif ".avi" in u:
        return "video/x-msvideo"
    return default


def _channel_http_headers(channel: Optional[Dict[str, str]]) -> Dict[str, object]:
    """Collect per-channel HTTP headers for casters."""
    headers: Dict[str, object] = {}
    if not channel:
        return headers
    def _copy(key: str, target: str) -> None:
        val = channel.get(key)
        if val:
            headers[target] = val
    _copy("http-user-agent", "user-agent")
    _copy("http-referrer", "referer")
    _copy("http-referer", "referer")
    _copy("http-origin", "origin")
    _copy("http-cookie", "cookie")
    _copy("http-authorization", "authorization")
    _copy("http-accept", "accept")
    extra = channel.get("http-headers")
    if isinstance(extra, list):
        headers["_extra"] = [str(h) for h in extra if h]
    return headers


# ============================================================================
# Unified Casting Manager
# ============================================================================

class CastingManager:
    """Manages multiple casting protocols and active sessions on a background loop."""
    
    def __init__(self):
        self.casters: Dict[CastProtocol, BaseCaster] = {}
        self.active_caster: Optional[BaseCaster] = None
        self.active_device: Optional[CastDevice] = None
        self._loop = None
        self._thread = None
        self._running = False
        
        # Initialize available casters
        if _HAS_CHROMECAST:
            self.casters[CastProtocol.CHROMECAST] = ChromecastCaster()
        if _HAS_UPNP:
            self.casters[CastProtocol.DLNA] = DLNACaster()
            # UPNP shares the same caster implementation
        if _HAS_AIRPLAY:
            self.casters[CastProtocol.AIRPLAY] = AirPlayCaster()

    def start(self):
        """Start the background asyncio loop."""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="CastingManagerLoop")
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def stop(self):
        """Stop the background loop."""
        if not self._running:
            return
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)

    def dispatch(self, coro):
        """Run a coroutine on the background loop and return the result synchronously."""
        if not self._running or not self._loop:
            raise RuntimeError("CastingManager is not running. Call start() first.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def discover_all(self, timeout: float = 5.0) -> List[CastDevice]:
        """Discover devices across all supported protocols."""
        return self.dispatch(self._discover_all_async(timeout))

    async def _discover_all_async(self, timeout: float) -> List[CastDevice]:
        unique_casters = set(self.casters.values())
        results = await asyncio.gather(
            *[c.discover(timeout) for c in unique_casters],
            return_exceptions=True
        )
        
        all_devices = []
        for res in results:
            if isinstance(res, list):
                all_devices.extend(res)
        
        return sorted(all_devices, key=lambda d: d.name)

    def connect(self, device: CastDevice, credentials: Optional[str] = None) -> None:
        """Connect to a selected device."""
        self.dispatch(self._connect_async(device, credentials))

    async def _connect_async(self, device: CastDevice, credentials: Optional[str] = None) -> None:
        if self.active_caster:
            await self.active_caster.disconnect()
            self.active_caster = None
            self.active_device = None
            
        caster = self.casters.get(device.protocol)
        if not caster and device.protocol in (CastProtocol.DLNA, CastProtocol.UPNP):
            caster = self.casters.get(CastProtocol.DLNA)
            
        if not caster:
            raise CastError(f"No caster implementation for {device.protocol}")
            
        if isinstance(caster, AirPlayCaster):
            await caster.connect(device, credentials)
        else:
            await caster.connect(device)
            
        self.active_caster = caster
        self.active_device = device

    def start_pairing(self, device: CastDevice) -> object:
        """Start pairing if supported by the protocol."""
        return self.dispatch(self._start_pairing_async(device))

    async def _start_pairing_async(self, device: CastDevice) -> object:
        caster = self.casters.get(device.protocol)
        if isinstance(caster, AirPlayCaster):
            return await caster.start_pairing(device)
        raise CastError("Pairing not supported for this device type")

    def play(self, url: str, title: str = "IPTV Stream", channel: Optional[Dict[str, str]] = None) -> None:
        self.dispatch(self._play_async(url, title, channel))

    async def _play_async(self, url: str, title: str, channel: Optional[Dict[str, str]]) -> None:
        if not self.active_caster:
            raise ConnectionError("No active cast device")
        
        headers = _channel_http_headers(channel)
        await self.active_caster.play(url, title, headers=headers)

    def stop_playback(self) -> None:
        if self.active_caster:
            self.dispatch(self.active_caster.stop())

    def disconnect(self) -> None:
        self.dispatch(self._disconnect_async())

    async def _disconnect_async(self) -> None:
        if self.active_caster:
            await self.active_caster.disconnect()
            self.active_caster = None
            self.active_device = None

    def is_connected(self) -> bool:
        # This can be checked safely without dispatch if we trust the flag state
        # but strictly the caster state might change. Ideally we dispatch, 
        # but for UI checks it's okay to read the local prop if updated correctly.
        # However, 'active_caster' is set on the loop.
        return self.active_caster is not None and self.active_caster.is_connected()



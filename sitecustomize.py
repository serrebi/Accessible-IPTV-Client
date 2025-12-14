"""Runtime shim to keep buffering logic canonical.

This module is auto-imported by Python if present. The internal player
behavior now lives solely in ``internal_player.py``; we deliberately avoid
maintaining a second, divergent implementation here.  Symbols are re-exported
for compatibility so other code importing ``sitecustomize`` still works.
"""

import logging

LOG = logging.getLogger(__name__)

try:  # Best-effort: some headless tools may not have full dependencies.
    import internal_player as _ip  # type: ignore
except Exception as exc:  # pragma: no cover - import guard
    LOG.debug("sitecustomize could not import internal_player: %s", exc)
    _ip = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

if _ip is not None:
    InternalPlayerFrame = _ip.InternalPlayerFrame  # type: ignore[attr-defined]
    InternalPlayerUnavailableError = _ip.InternalPlayerUnavailableError  # type: ignore[attr-defined]
    _prepare_vlc_runtime = getattr(_ip, "_prepare_vlc_runtime", None)
    _VLC_IMPORT_ERROR = getattr(_ip, "_VLC_IMPORT_ERROR", None)
    vlc = getattr(_ip, "vlc", None)
    exported = list(getattr(_ip, "__all__", []))
    if "InternalPlayerFrame" not in exported:
        exported.append("InternalPlayerFrame")
    __all__ = exported
else:
    class InternalPlayerUnavailableError(RuntimeError):
        """Fallback error used when the internal player cannot be loaded."""

    InternalPlayerFrame = None  # type: ignore[assignment]
    _prepare_vlc_runtime = None
    _VLC_IMPORT_ERROR = _IMPORT_ERROR
    vlc = None
    __all__ = [
        "InternalPlayerFrame",
        "InternalPlayerUnavailableError",
        "_VLC_IMPORT_ERROR",
        "vlc",
    ]

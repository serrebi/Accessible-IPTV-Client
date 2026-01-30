import datetime
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateManifest:
    version: str
    asset_filename: str
    download_url: str
    sha256: str
    published_date: str
    release_notes_summary: Optional[str] = None
    signing_thumbprints: Tuple[str, ...] = ()


def parse_version(value: str) -> Optional[Tuple[int, int, int]]:
    if not value:
        return None
    match = _VERSION_RE.match(value.strip())
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    return major, minor, patch


def normalize_version_tag(tag: str) -> Optional[str]:
    parsed = parse_version(tag)
    if not parsed:
        return None
    major, minor, patch = parsed
    return f"{major}.{minor}.{patch}"


def is_newer_version(current: str, latest: str) -> bool:
    cur = parse_version(current)
    new = parse_version(latest)
    if not cur or not new:
        return False
    return new > cur


def _build_request(url: str) -> urllib.request.Request:
    headers = {
        "User-Agent": "AccessibleIPTVClient-Updater",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"token {token}"
    return urllib.request.Request(url, headers=headers)


def _normalize_thumbprint(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace(" ", "").strip().upper()


def _normalize_thumbprints(values: Iterable[str]) -> Tuple[str, ...]:
    normalized = {_normalize_thumbprint(value) for value in values if value}
    normalized.discard("")
    return tuple(sorted(normalized))


def _env_thumbprints() -> Tuple[str, ...]:
    raw = os.environ.get("ACCESSIBLEIPTVCLIENT_TRUSTED_SIGNING_THUMBPRINTS", "")
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _extract_manifest_thumbprints(payload: dict) -> Tuple[str, ...]:
    raw = payload.get("signing_thumbprints") or payload.get("signing_thumbprint")
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if item)
    return ()


def fetch_latest_release(owner: str, repo: str) -> dict:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = _build_request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise UpdateError("GitHub API rate limit exceeded. Please try again later.") from exc
        raise UpdateError(f"Failed to fetch release info ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise UpdateError("Unable to reach GitHub. Please check your connection.") from exc


def fetch_update_manifest(release: dict, manifest_name: str) -> UpdateManifest:
    assets = release.get("assets") or []
    manifest_asset = None
    for asset in assets:
        name = asset.get("name") or ""
        if name.lower() == manifest_name.lower():
            manifest_asset = asset
            break
    if not manifest_asset:
        raise UpdateError("Update manifest was not found in the latest release.")

    url = manifest_asset.get("browser_download_url") or ""
    if not url:
        raise UpdateError("Update manifest download URL is missing.")

    data = download_json(url)
    thumbprints = _normalize_thumbprints(list(_env_thumbprints()) + list(_extract_manifest_thumbprints(data)))
    try:
        return UpdateManifest(
            version=str(data["version"]),
            asset_filename=str(data["asset_filename"]),
            download_url=str(data["download_url"]),
            sha256=str(data["sha256"]),
            published_date=str(data.get("published_date", "")),
            release_notes_summary=data.get("release_notes_summary"),
            signing_thumbprints=thumbprints,
        )
    except KeyError as exc:
        raise UpdateError("Update manifest is missing required fields.") from exc


def download_json(url: str) -> dict:
    req = _build_request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Failed to download manifest ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise UpdateError("Unable to download manifest. Please check your connection.") from exc


def download_file_with_sha256(url: str, dest_path: str) -> str:
    req = _build_request(url)
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, "wb") as handle:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Failed to download update ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise UpdateError("Unable to download update. Please check your connection.") from exc
    return digest.hexdigest()


def safe_extract_zip(zip_path: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    base_path = os.path.abspath(dest_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target_path = os.path.abspath(os.path.join(dest_dir, member.filename))
            if not target_path.startswith(base_path + os.sep) and target_path != base_path:
                raise UpdateError("Update package contains an unsafe file path.")
        zf.extractall(dest_dir)


def find_executable(root: str, exe_name: str) -> Optional[str]:
    exe_name_lower = exe_name.lower()
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower() == exe_name_lower:
                return os.path.join(dirpath, filename)
    return None


def verify_authenticode(exe_path: str, allowed_thumbprints: Iterable[str]) -> None:
    allowed = set(_normalize_thumbprints(allowed_thumbprints))
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            f"$sig = Get-AuthenticodeSignature -FilePath '{exe_path}'; "
            "$thumb = if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint } else { '' }; "
            "$out = @{Status=$sig.Status.ToString(); StatusMessage=$sig.StatusMessage; Thumbprint=$thumb}; "
            "$out | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise UpdateError(f"Authenticode verification failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        data = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise UpdateError("Authenticode verification returned invalid data.") from exc

    status = str(data.get("Status") or "").strip()
    status_msg = str(data.get("StatusMessage") or "").strip()
    thumbprint = _normalize_thumbprint(data.get("Thumbprint"))

    if status.lower() == "valid":
        return
    if thumbprint and allowed and thumbprint in allowed:
        return
    detail = f"Authenticode status was {status or 'Unknown'}."
    if status_msg:
        detail = f"{detail} {status_msg}"
    if thumbprint:
        detail = f"{detail} (thumbprint {thumbprint})."
    if allowed:
        detail = f"{detail} Expected thumbprints: {', '.join(sorted(allowed))}."
    raise UpdateError(detail)


def summarize_release_notes(notes: str, max_lines: int = 6, max_chars: int = 600) -> str:
    if not notes:
        return "No release notes provided."
    lines = [line.strip() for line in notes.splitlines() if line.strip()]
    summary = "\n".join(lines[:max_lines])
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."
    return summary


def build_manifest(
    version: str,
    asset_filename: str,
    download_url: str,
    sha256: str,
    release_notes_summary: Optional[str] = None,
    signing_thumbprint: Optional[str] = None,
) -> dict:
    published = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    manifest = {
        "version": version,
        "asset_filename": asset_filename,
        "download_url": download_url,
        "sha256": sha256,
        "published_date": published,
        "release_notes_summary": release_notes_summary,
    }
    if signing_thumbprint:
        manifest["signing_thumbprint"] = signing_thumbprint
    return manifest

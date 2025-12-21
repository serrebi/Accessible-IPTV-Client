# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

sys.setrecursionlimit(5000)

datas = [
    ('init.mp4', '.'),
    ('ffmpeg.exe', '.'),
]
binaries = []
hiddenimports = [
    'logging.handlers',
    'http.server',
    'urllib.request',
    'urllib.error',
    'urllib.parse',
    'json',
    'base64',
    'hashlib',
    'queue',
    'tempfile',
    'shutil',
    'uuid',
    'xml.etree.ElementTree',
]

# Comprehensive list of packages to collect
# Includes both direct and recursive dependencies that often fail in frozen builds
packages_to_bundle = [
    'wx',
    'vlc',
    'pychromecast',
    'pyatv',
    'zeroconf',
    'casttube',
    'async_upnp_client',
    'aiohttp',
    'requests',
    'cryptography',
    'chacha20poly1305_reuseable',
    'pydantic',
    'pydantic_core',
    'annotated_types',
    'typing_extensions',
    'srptools',
    'tabulate',
    'tinytag',
    'defusedxml',
    'didl_lite',
    'voluptuous',
    'certifi',
    'idna',
    'urllib3',
    'psutil',
    'netifaces',
    'ifaddr',
    'miniaudio',
    'charset_normalizer',
    'multidict',
    'yarl',
    'frozenlist',
    'aiosignal',
    'async_timeout',
]

for pkg in packages_to_bundle:
    try:
        # Collect data files, binaries, and hidden imports
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
        # Specifically ensure all submodules are captured
        hiddenimports += collect_submodules(pkg)
    except Exception as e:
        print(f"Warning: Could not fully collect package '{pkg}': {e}")

# Special handling for Google Protobuf (critical for Chromecast)
try:
    import google.protobuf
    tmp_ret = collect_all('google.protobuf')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    hiddenimports += collect_submodules('google.protobuf')
except ImportError:
    pass

# Deduplicate hidden imports
hiddenimports = list(set(filter(None, hiddenimports)))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

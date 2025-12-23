# -*- mode: python ; coding: utf-8 -*-

import os
import sys

block_cipher = None

# Hidden imports for networking and casting stacks
hidden_imports = [
    'pychromecast',
    'zeroconf',
    'aiohttp',
    'async_upnp_client',
    'pyatv',
    'miniaudio',
    'netifaces',
    'pydantic',
    'srptools',
    'tinytag',
    'tabulate',
    'defusedxml',
    'didl_lite',
    'voluptuous',
    'chacha20poly1305_reuseable',
    'requests',
    'vlc',
    'psutil',
    'cryptography',
]

# Explicitly add some submodules that PyInstaller might miss
hidden_imports += [
    'pychromecast.controllers',
    'pychromecast.controllers.media',
    'async_upnp_client.client_factory',
    'async_upnp_client.aiohttp',
    'async_upnp_client.profiles.dlna',
    'async_upnp_client.search',
    'async_upnp_client.ssdp',
    'pyatv.conf',
    'pyatv.const',
    'pyatv.convert',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('iptvclient.conf', '.'),
        ('init.mp4', '.'),
        ('ffmpeg.exe', '.'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='iptvclient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None, # Add icon if available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='iptvclient',
)
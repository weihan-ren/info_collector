# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — cross-platform (Windows / macOS / Linux)."""

import sys

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static')],
    hiddenimports=[
        'src', 'src.config_loader', 'src.collector', 'src.analyzer', 'src.server',
        'yaml', 'feedparser', 'openai', 'requests', 'bs4', 'pydantic', 'fastapi',
        'uvicorn',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ── macOS-specific: entitlements ──
is_mac = sys.platform == 'darwin'
entitlements_path = None

if is_mac:
    entitlements_txt = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.network.client</key>
    <true/>
</dict>
</plist>
"""
    entitlements_path = 'NewsCollector.entitlements'
    with open(entitlements_path, 'w') as f:
        f.write(entitlements_txt)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NewsCollector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=entitlements_path,
)

# ── macOS-only: application bundle ──
if is_mac:
    app = BUNDLE(
        exe,
        name='NewsCollector.app',
        icon=None,
        bundle_identifier='com.newscollector.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleName': 'NewsCollector',
            'LSBackgroundOnly': False,
        },
    )

# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['tray_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('forge-logo.png', '.'), ('forge-logo-small.png', '.'), ('index.html', '.'), ('suite.html', '.'), ('archive.html', '.'), ('forge_picker.js', '.'), ('channel_forge', 'channel_forge')],
    hiddenimports=['archive_forge', 'channel_forge.app'],
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
    [],
    exclude_binaries=True,
    name='NTV Forge',
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
    icon=['forge.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NTV Forge',
)

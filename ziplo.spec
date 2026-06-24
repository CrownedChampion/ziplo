# -*- mode: python ; coding: utf-8 -*-
# Ziplo PyInstaller spec — alternative to running build.py
# Usage: pyinstaller ziplo.spec

block_cipher = None

a = Analysis(
    ['src/ziplo.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter', 'tkinter.ttk', 'tkinter.scrolledtext',
        'tkinter.filedialog', 'tkinter.messagebox',
        'urllib', 'urllib.request', 'urllib.error', 'urllib.parse',
        'ssl', 'zipfile', 'tarfile', 'threading',
        'json', 'pathlib', 'tempfile', 'subprocess', 'shutil',
        'webbrowser', 'base64', 'time', 're',
    ],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ziplo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',   # comment out if no icon
    version_file=None,
)

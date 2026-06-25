"""
Ziplo build script — run this on Windows to produce ziplo.exe
Requires: pip install pyinstaller requests
"""

import subprocess
import sys
import os
from pathlib import Path

HERE = Path(__file__).parent
SRC  = HERE / "src" / "ziplo.py"
ICON = HERE / "assets" / "icon.ico"
DIST = HERE / "dist"

def main():
    print("=== Ziplo Builder ===\n")

    # Check PyInstaller
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("  Installing PyInstaller…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build args
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                   # single .exe
        "--windowed",                  # no console window
        "--name", "ziplo",
        "--distpath", str(DIST),
        "--workpath", str(HERE / "build_tmp"),
        "--specpath", str(HERE),
        "--clean",
        "--noconfirm",
    ]

    # Add icon if present
    if ICON.exists():
        args += ["--icon", str(ICON)]
    else:
        print("  (No icon.ico found in assets/ — skipping icon)")

    # Hidden imports for stdlib modules PyInstaller sometimes misses
    for mod in ["tkinter", "tkinter.ttk", "tkinter.scrolledtext",
                "tkinter.filedialog", "tkinter.messagebox",
                "urllib", "urllib.request", "urllib.error",
                "ssl", "zipfile", "tarfile", "threading",
                "json", "pathlib", "tempfile", "subprocess"]:
        args += ["--hidden-import", mod]

    args.append(str(SRC))

    print(f"\n  Running: pyinstaller {' '.join(args[2:])}\n")
    result = subprocess.run(args)

    if result.returncode == 0:
        exe = DIST / "ziplo.exe"
        if exe.exists():
            size_mb = exe.stat().st_size / 1_048_576
            print(f"\n✅  Build complete!")
            print(f"   → {exe}  ({size_mb:.1f} MB)")
        else:
            print("\n⚠  Build finished but ziplo.exe not found in dist/")
    else:
        print("\n✗  Build failed — check output above")
        sys.exit(1)

if __name__ == "__main__":
    main()

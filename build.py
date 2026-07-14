import subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
subprocess.check_call([sys.executable, "-m", "PyInstaller",
    "--onefile", "--windowed", "--name", "ziplo",
    "--distpath", str(HERE / "dist"),
    "--workpath", str(HERE / "build_tmp"),
    "--specpath", str(HERE), "--clean", "--noconfirm",
    str(HERE / "src" / "ziplo.py")])

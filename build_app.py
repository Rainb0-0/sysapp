# build_app.py
# -*- coding: utf-8 -*-
"""
One-click PyInstaller build script for the app.

Features:
- Dependency checks (PyQt5, PyQtWebEngine, psycopg2 or psycopg2-binary).
- Collects all required PyQt WebEngine resources.
- Packs project data folders and single data files (e.g., d3.min.js).
- Cross-platform add-data separator handling.
- Clean build options and useful defaults.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return platform.system().lower().startswith("win")


# ── Console encoding fix for Windows ───────────────────────────
# Windows consoles (cmd, PowerShell) default to cp1252, which
# cannot encode emoji like ✅, ❌, 📁, etc.  Force UTF-8 on stdout
# so the build script prints cleanly everywhere.
#
# Only called when the script runs as __main__; imports are safe.
def _fix_console_encoding():
    """Set stdout/stderr encoding to UTF-8 on Windows (Python 3.7+)."""
    if not is_windows():
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except OSError:
                pass  # e.g. pipe closed


def add_data_arg(src: Path, dst: str) -> str:
    """
    Build a --add-data argument for PyInstaller with platform-specific separator.
    On Windows: "src;dst"
    Else: "src:dst"
    """
    sep = ";" if is_windows() else ":"
    return f"--add-data={src}{sep}{dst}"


def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 9):
        print(f"❌ Python 3.9+ is recommended (detected {major}.{minor}).")
        return False
    return True


def check_dependencies() -> bool:
    """
    Check core runtime dependencies. Do not fail hard on optional ones,
    but guide the user to install missing packages.
    """
    ok = True

    required = [
        ("PyQt5", "pip install PyQt5"),
        ("PyQt5.QtWebEngineWidgets", "pip install PyQtWebEngine"),
    ]

    for mod, tip in required:
        try:
            __import__(mod)
            print(f"✅ {mod} found")
        except ImportError:
            print(f"❌ {mod} not found. Try: {tip}")
            ok = False

    # psycopg2 vs psycopg2-binary
    try:
        __import__("psycopg2")
        print("✅ psycopg2 found")
    except ImportError:
        try:
            __import__("psycopg2_binary")
            print("✅ psycopg2-binary found")
        except ImportError:
            print("❌ psycopg2 (or psycopg2-binary) not found!")
            print("   Try: pip install psycopg2-binary")
            ok = False

    # PyInstaller itself
    try:
        __import__("PyInstaller")
        print("✅ PyInstaller found")
    except ImportError:
        print("❌ PyInstaller not found! Try: pip install pyinstaller")
        ok = False

    return ok


def discover_project_data() -> list[str]:
    """
    Discover project data folders to include with --add-data.
    Adjust the list below to match your tree.
    """
    cwd = Path(__file__).parent.resolve()

    # Common content folders in the repo
    candidate_dirs = [
        "Architecture_View_tab",
        "Interface_Connectivity_tab",
        "Component_Tree_tab",
        "Schematic_View_tab",
        "styles",
        "themes",
        "images",
        "icons",
        "assets",
    ]

    add_data = []
    for d in candidate_dirs:
        p = cwd / d
        if p.exists() and p.is_dir():
            add_data.append(add_data_arg(p, d))
            print(f"📁 Will include dir: {p}")

    # Single data files at project root
    single_files = [
        "d3.min.js",
        "schematic_web.html",  # if you add the web schematic file
        "app_icon.ico",        # optional icon
    ]
    for f in single_files:
        p = cwd / f
        if p.exists() and p.is_file():
            # put root-level files into . (cwd inside bundle)
            add_data.append(add_data_arg(p, "."))
            print(f"📄 Will include file: {p}")

    return add_data


def make_build_cmd(
    entry_point: str,
    name: str,
    icon: str | None,
    onefile: bool = True,
    windowed: bool = True,
) -> list[str]:
    """
    Compose a robust PyInstaller command with common switches for this app.
    """
    cmd: list[str] = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--strip",
        "--optimize=2",
        f"--name={name}",
    ]

    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if icon and Path(icon).exists():
        cmd.append(f"--icon={icon}")

    # Hidden imports and resource collection for PyQt5 + WebEngine
    cmd += [
        "--hidden-import=PyQt5.QtCore",
        "--hidden-import=PyQt5.QtGui",
        "--hidden-import=PyQt5.QtWidgets",
        "--hidden-import=PyQt5.QtWebEngineWidgets",
        "--hidden-import=PyQt5.QtWebChannel",
        "--hidden-import=PyQt5.QtPrintSupport",
        "--hidden-import=psycopg2",
        "--hidden-import=psycopg2.pool",
        "--hidden-import=psycopg2.extras",
        "--collect-all=PyQt5.QtWebEngineWidgets",
        "--collect-all=PyQt5.QtWebEngineCore",
        "--collect-submodules=PyQt5",
        "--exclude-module=tkinter",
        "--exclude-module=IPython",
        "--exclude-module=jupyter",
        "--exclude-module=matplotlib",
        "--exclude-module=numpy",
        "--exclude-module=pandas",
        "--exclude-module=scipy",
    ]

    # Data files / folders
    cmd += discover_project_data()

    # Entry point
    cmd.append(entry_point)
    return cmd


def clean_old_builds(name: str):
    """
    Remove previous build artifacts to ensure a clean build.
    """
    for d in ("build",):
        if Path(d).exists():
            print(f"🧹 Removing {d}/ ...")
            shutil.rmtree(d, ignore_errors=True)

    spec_file = Path(f"{name}.spec")
    if spec_file.exists():
        print(f"🧹 Removing {spec_file} ...")
        spec_file.unlink()


def run_build(cmd: list[str]) -> int:
    """
    Execute PyInstaller build command.
    """
    print("\n🔧 Build command:")
    print(" ".join(map(str, cmd)))
    print()
    return subprocess.call(cmd)


def move_dist_artifacts(name: str):
    """
    Ensure the binary is in dist/, and show a friendly message.
    """
    dist = Path("dist")
    if not dist.exists():
        print("⚠️ No dist/ directory found.")
        return

    # On Windows: name.exe; else: name
    exe_name = f"{name}.exe" if is_windows() else name
    target = dist / exe_name
    if target.exists():
        print(f"✅ Build succeeded: {target.resolve()}")
    else:
        # Sometimes PyInstaller uses the entry script stem as name
        # (rare if --name is provided). Try to hint the user.
        print("⚠️ Build finished, but executable not found with expected name.")
        print(f"   Check dist/ for produced artifacts.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the app with PyInstaller.")
    p.add_argument("--entry", default="main.py", help="Entry-point script (default: main.py)")
    p.add_argument("--name", default="SystemArchitecture", help="Executable name")
    p.add_argument("--icon", default="app_icon.ico", help="Icon file (optional)")
    p.add_argument("--onedir", action="store_true", help="Build as onedir instead of onefile")
    p.add_argument("--console", action="store_true", help="Build with console window")
    return p.parse_args()


def main():
    _fix_console_encoding()

    args = parse_args()

    if not check_python_version():
        sys.exit(2)

    if not check_dependencies():
        sys.exit(3)

    if not Path(args.entry).exists():
        print(f"❌ Entry-point not found: {args.entry}")
        sys.exit(4)

    name = args.name
    icon = args.icon if args.icon and Path(args.icon).exists() else None
    onefile = not args.onedir
    windowed = not args.console

    clean_old_builds(name)
    cmd = make_build_cmd(entry_point=args.entry, name=name, icon=icon, onefile=onefile, windowed=windowed)
    rc = run_build(cmd)

    if rc == 0:
        move_dist_artifacts(name)
    else:
        print(f"❌ Build failed with exit code: {rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()

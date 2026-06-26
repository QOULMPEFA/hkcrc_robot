#!/usr/bin/env python3
"""Cython build script — compiles robot_control *.py → *.so on Orange Pi (ARM64).

Usage (run ON the Orange Pi):
    python3 build_cython.py              # full build
    python3 build_cython.py --clean      # clean build artifacts

Prerequisites:
    sudo apt install python3-dev gcc
    pip install cython

Output:
    robot_encrypted/robot_control/   — compiled .so files + assets
    robot_encrypted/run_server.py    — plain-text entry point
"""

import os
import sys
import shutil
import subprocess
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # code/
SRC_DIR = PROJECT_ROOT / "robot_control"
OUT_DIR = PROJECT_ROOT / "robot_encrypted" / "robot_control"

# .py files to compile (relative to SRC_DIR)
COMPILE_TARGETS = [
    "__init__.py",
    "module_base.py",
    "robot.py",
    "ugv.py",
    "lift.py",
    "arm.py",
    "hand.py",
    "camera.py",
    "motor.py",
    "web_gui/__init__.py",
    "web_gui/server.py",
    "web_gui/auth.py",
    "web_gui/i18n.py",
]

# Directories to copy verbatim (source → dest relative to OUT_DIR)
COPY_DIRS = [
    ("web_gui/templates", "web_gui/templates"),
    ("web_gui/static", "web_gui/static"),
]


def step(msg):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def check_deps():
    """Verify Cython and gcc are available."""
    try:
        import Cython
        print(f"[OK] Cython {Cython.__version__}")
    except ImportError:
        sys.exit("ERROR: Cython not installed. Run: pip install cython")

    r = subprocess.run(["gcc", "--version"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ERROR: gcc not found. Run: sudo apt install python3-dev gcc")
    print(f"[OK] gcc available")


def get_extension(path: str):
    """Return the expected .so filename suffix for this Python."""
    import sysconfig
    # e.g. .cpython-310-aarch64-linux-gnu.so
    return sysconfig.get_config_var("EXT_SUFFIX") or ".so"


def compile_file(target: str, ext_suffix: str):
    """Cythonize a single .py file → .so using setuptools."""
    src = SRC_DIR / target
    out = OUT_DIR / target.replace(".py", ext_suffix)

    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"  Compiling: {target} ...", end=" ", flush=True)

    # Use cython command-line approach (most reliable across Cython versions)
    import Cython.Build
    import Cython.Compiler.Options

    # Suppress Cython warnings
    Cython.Compiler.Options.annotate = False

    # Step 1: cython .py → .c
    c_file = out.parent / src.name.replace(".py", ".c")
    try:
        subprocess.run(
            [sys.executable, "-m", "cython", "-3", "-o", str(c_file), str(src)],
            check=True, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.CalledProcessError as e:
        print(f"FAILED (cython)")
        print(e.stderr)
        return False

    # Step 2: gcc .c → .so
    import sysconfig
    includes = sysconfig.get_config_var("INCLUDEPY") or sysconfig.get_path("include")
    cflags = sysconfig.get_config_var("CFLAGS") or ""
    ldshared = sysconfig.get_config_var("LDSHARED") or "gcc -shared"
    # Replace $(NO_STRICT_OVERFLOW) and other vars
    import re
    ldshared = re.sub(r'\$\([A-Z_]+\)', '', ldshared)

    cc_cmd = (
        f"gcc -shared -fPIC {cflags} "
        f"-I{includes} "
        f"-o {out} {c_file}"
    )
    try:
        result = subprocess.run(
            cc_cmd, shell=True,
            check=True, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )
        # Remove intermediate .c file
        c_file.unlink(missing_ok=True)
        print("OK")
        return True
    except subprocess.CalledProcessError as e:
        print("FAILED (gcc)")
        # Print first few lines of error
        lines = e.stderr.strip().split("\n")
        for line in lines[:5]:
            print(f"    {line}")
        return False


def copy_assets():
    """Copy templates, static files and plain-text __init__.py files."""
    # Copy __init__.py files as plain text (needed for Python package discovery)
    for target in COMPILE_TARGETS:
        if target.endswith("__init__.py"):
            src = SRC_DIR / target
            dst = OUT_DIR / target
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  [plain] {target}")

    # Copy template/static directories
    for src_rel, dst_rel in COPY_DIRS:
        src = SRC_DIR / src_rel
        dst = OUT_DIR / dst_rel
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
            print(f"  [copy]  {src_rel}/ -> {dst_rel}/")
        else:
            print(f"  [warn]  {src_rel}/ not found, skipping")


def create_package():
    """Create deployment tarball."""
    out_root = PROJECT_ROOT / "robot_encrypted"
    tarball = PROJECT_ROOT / "robot_encrypted.tar.gz"

    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(out_root, arcname="robot_encrypted")

    size_kb = tarball.stat().st_size / 1024
    print(f"\n[OK] Package: {tarball} ({size_kb:.0f} KB)")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--clean", action="store_true", help="Remove build artifacts")
    args = p.parse_args()

    if args.clean:
        out_root = PROJECT_ROOT / "robot_encrypted" / "robot_control"
        for f in out_root.rglob("*.so"):
            f.unlink()
        for f in out_root.rglob("*.c"):
            f.unlink()
        print("[OK] Build artifacts cleaned")
        return

    step("Checking dependencies")
    check_deps()

    step("Compiling Python → C → .so")
    ext_suffix = get_extension("")
    print(f"  Extension suffix: {ext_suffix}")

    ok = 0
    fail = 0
    for target in COMPILE_TARGETS:
        if target.endswith("__init__.py"):
            continue  # keep plain text
        if compile_file(target, ext_suffix):
            ok += 1
        else:
            fail += 1

    print(f"\n  Results: {ok} compiled, {fail} failed")

    step("Copying assets")
    copy_assets()

    step("Creating deployment package")
    create_package()

    if fail == 0:
        step("Build complete")
        print(f"  Output: {PROJECT_ROOT / 'robot_encrypted'}")
        print(f"  Package: {PROJECT_ROOT / 'robot_encrypted.tar.gz'}")
        print(f"\nDeploy to production robot:")
        print(f"  tar xzf robot_encrypted.tar.gz -C /home/orangepi/")
        print(f"  sudo cp robot_encrypted/systemd/robot_control_panel.service /etc/systemd/system/")
        print(f"  sudo systemctl daemon-reload")
        print(f"  sudo systemctl restart robot_control_panel")
    else:
        print(f"\nERROR: {fail} file(s) failed to compile")
        sys.exit(1)


if __name__ == "__main__":
    main()

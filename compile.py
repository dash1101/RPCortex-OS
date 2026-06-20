#!/usr/bin/env python3
"""RPCortex OS compiler — cross-platform replacement for compile.sh / compile.bat.

Compiles Core/ and Packages/ *.py -> .mpy with mpy-cross and writes a
deploy-ready image to dist/ (or a custom dir). main.py and Core/rpc_stub.py
stay as source (MicroPython boots main.py, and the stub is copied to /main.py
as text by reinstall). .lp / .cfg / .json and other assets are copied as-is.

Usage:
    python compile.py                    # armv6m (RP2040), -> ./dist
    python compile.py --arch armv7m      # RP2350 (Pico 2 / 2 W)
    python compile.py --arch xtensawin   # ESP32 / ESP32-S2 / ESP32-S3
    python compile.py --out /tmp/built   # custom output dir

Arch reference:  armv6m = RP2040 · armv7m = RP2350 · xtensawin = ESP32 family.
Install mpy-cross:  pip install mpy-cross
"""
import argparse
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
# .py files that must stay source even in a compiled image.
KEEP_SOURCE = {'main.py', os.path.join('Core', 'rpc_stub.py').replace('\\', '/')}
# Roots to walk (relative to the repo).
ROOTS = ['Core', 'Packages']


def _rel(path):
    return os.path.relpath(path, REPO).replace('\\', '/')


def _mpy_cross(src, dst, arch, srcname):
    """Compile one file. Returns (ok, err). -s embeds a clean relative source
    name so on-device tracebacks don't leak the build machine's absolute path.
    arch=None -> portable (architecture-neutral) .mpy that runs on any port."""
    cmd = [sys.executable, '-m', 'mpy_cross']
    if arch:
        cmd.append('-march=' + arch)
    cmd += ['-s', srcname, '-o', dst, src]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.returncode == 0, r.stderr.decode('utf-8', 'replace').strip()


def main():
    ap = argparse.ArgumentParser(description='Compile RPCortex to a .mpy image.')
    ap.add_argument('--arch', default=None,
                    help='mpy-cross arch for a device-specific build: armv6m (RP2040) / '
                         'armv7m (RP2350) / xtensawin (ESP32). Omit for a portable '
                         '(architecture-neutral) image that runs on any board.')
    ap.add_argument('--out', default=os.path.join(REPO, 'dist'), help='output directory')
    args = ap.parse_args()

    # prereq: mpy-cross importable
    try:
        subprocess.run([sys.executable, '-m', 'mpy_cross', '--version'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("[!] mpy-cross not available.  Install it with:  pip install mpy-cross")
        sys.exit(1)

    out = os.path.abspath(args.out)
    print("\n  RPCortex OS Compiler")
    print("  arch   : {}".format(args.arch or "neutral (portable, any board)"))
    print("  output : {}\n".format(out))

    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    compiled = copied = errors = 0
    src_bytes = out_bytes = 0

    # collect files: main.py at root + everything under ROOTS
    targets = [os.path.join(REPO, 'main.py')]
    for root in ROOTS:
        base = os.path.join(REPO, root)
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                targets.append(os.path.join(dp, fn))

    for src in targets:
        if not os.path.isfile(src):
            continue
        rel = _rel(src)
        if '__pycache__' in rel or rel.endswith('.pyc'):
            continue
        is_py = rel.endswith('.py')
        compile_it = is_py and rel not in KEEP_SOURCE
        if compile_it:
            dst = os.path.join(out, rel[:-3] + '.mpy')
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            ok, err = _mpy_cross(src, dst, args.arch, rel)
            if ok:
                sb = os.path.getsize(src); ob = os.path.getsize(dst)
                src_bytes += sb; out_bytes += ob; compiled += 1
                print("  [+] {:<48} {:>6} B -> {:>5} B".format(rel, sb, ob))
            else:
                errors += 1
                print("  [!] FAILED {}: {}".format(rel, err))
        else:
            dst = os.path.join(out, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    saved = (1 - out_bytes / src_bytes) * 100 if src_bytes else 0
    print("\n  compiled {}  ·  copied {} as source  ·  {} errors".format(
        compiled, copied, errors))
    if compiled:
        print("  size: {} B -> {} B  ({:.0f}% smaller)".format(src_bytes, out_bytes, saved))
    print("  image: {}".format(out))
    print("  deploy it:  python deploy.py --compiled" + ("" if args.out.endswith('dist') else " --out " + out))
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()

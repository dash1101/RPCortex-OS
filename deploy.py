#!/usr/bin/env python3
"""RPCortex deploy — cross-platform replacement for deploy.sh / deploy.bat.

Copies the OS to a connected MicroPython device with mpremote, in ONE raw-REPL
session (Core/, Packages/, main.py). /Vela/ and /Users/ on the device are never
touched, so accounts/settings/WiFi are safe.

Usage:
    python deploy.py                       # deploy source from the repo
    python deploy.py --compiled            # deploy the compiled dist/ image
    python deploy.py --compiled --out DIR  # deploy a custom compiled dir
    python deploy.py --port /dev/ttyACM0   # or COM3 — explicit serial port

Run  python compile.py  first if deploying --compiled.
Install mpremote:  pip install mpremote
"""
import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description='Deploy RPCortex to a device via mpremote.')
    ap.add_argument('--compiled', action='store_true', help='deploy the compiled dist/ image')
    ap.add_argument('--out', default=os.path.join(REPO, 'dist'), help='compiled image dir')
    ap.add_argument('--port', default=None, help='serial port (e.g. /dev/ttyACM0 or COM3)')
    args = ap.parse_args()

    # prereq: mpremote importable
    try:
        subprocess.run([sys.executable, '-m', 'mpremote', '--help'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("[!] mpremote not available.  Install it with:  pip install mpremote")
        sys.exit(1)

    src = os.path.abspath(args.out) if args.compiled else REPO
    if args.compiled and not os.path.isdir(src):
        print("[!] Compiled image not found: {}\n    Run  python compile.py  first.".format(src))
        sys.exit(1)

    print("\n  Deploying {} from: {}".format("COMPILED" if args.compiled else "SOURCE", src))
    print("  Port: {}\n".format(args.port or "auto-detect"))
    print("  -- copying Core/, Packages/, main.py  (user data under /Vela/ and /Users/ untouched) --")

    base = [sys.executable, '-m', 'mpremote']
    if args.port:
        base += ['connect', args.port]
    # one chained session: cp -r Core : + cp -r Packages : + cp main.py :
    cmd = base + ['cp', '-r', os.path.join(src, 'Core'), ':',
                  '+', 'cp', '-r', os.path.join(src, 'Packages'), ':',
                  '+', 'cp', os.path.join(src, 'main.py'), ':']
    r = subprocess.run(cmd)
    if r.returncode == 0:
        reset = base[2:] and ' '.join(['mpremote'] + base[2:] + ['reset']) or 'mpremote reset'
        print("\n  Deploy complete. Reboot to apply:  {}".format(reset))
    else:
        print("\n  [!] Deploy failed.")
        print("      - Board plugged in? Try:  python deploy.py --port /dev/ttyACM0")
        print("      - Close any other serial program (PuTTY/Thonny) using the port.")
        sys.exit(1)


if __name__ == '__main__':
    main()

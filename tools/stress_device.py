#!/usr/bin/env python3
"""On-device stress test for RPCortex + Nova D1. Host-side; not shipped.

Drives a real board over serial the way a person would, only harder and for
longer, recording free heap and largest allocatable block after every step. The
point is not that each command works once -- the host suites cover that -- but
that memory does not walk downwards and the shell stays responsive after
sustained use. That is what the unit tests structurally cannot tell you.

Two things it learned the hard way, both encoded here:
  * mpremote leaves the board at the BARE REPL, and `reboot` typed at a Python
    prompt is just a NameError -- the device never restarts and every reading
    after it describes the wrong state. at_repl() checks before assuming.
  * A previous session leaves every command module it touched in the cache, so a
    baseline taken without reclaiming first measures the last run, not the
    device.

Usage:  python3 tools/stress_device.py     (log: stress.log beside it)

Runs the device the way a person would, only harder and for longer, and records
free heap and largest allocatable block after every step. The point is not that
each command works once -- the host suite covers that -- but that memory does not
walk downwards and the shell does not become unresponsive after sustained use.

Everything is captured to a log; failures are counted, not fatal, so one bad
command does not end the run.
"""
import glob
import re
import sys
import time

import serial

ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
import os as _os
LOG = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         'stress.log'), 'w', buffering=1)
PROMPT = 'vela'
PASSWORD = _os.environ.get('RPC_ROOT_PW', 'rpcortex') if False else 'rpcortex'


def log(*a):
    line = ' '.join(str(x) for x in a)
    LOG.write(line + '\n')
    print(line, flush=True)


def port():
    for _ in range(30):
        for p in sorted(glob.glob('/dev/ttyACM*')):
            try:
                return serial.Serial(p, 115200, timeout=0.8)
            except Exception:
                pass
        time.sleep(2)
    raise SystemExit('no serial port')


class Dev:
    def __init__(self):
        self.s = port()
        self.fails = 0
        self.checks = 0

    def send(self, cmd, wait=4.0):
        """Send one command and read its reply.

        Drains first. A background service writing to serial leaves bytes in the
        buffer, and without the drain each read picks up the PREVIOUS command's
        tail — the replies walk one behind and the run looks hung when the device
        is perfectly responsive. That cost a whole test run to work out."""
        self.s.reset_input_buffer()
        self.s.write(cmd.encode() + b'\r')
        time.sleep(wait)
        out = ANSI.sub('', self.s.read(60000).decode('utf-8', 'replace'))
        # If the tail is not a prompt the device is still talking; give it more.
        for _ in range(6):
            if PROMPT in out[-40:] or '>' in out[-6:]:
                break
            time.sleep(1.5)
            out += ANSI.sub('', self.s.read(60000).decode('utf-8', 'replace'))
        return out

    def at_repl(self):
        """True if we are at the BARE MicroPython REPL rather than the shell.

        Worth checking explicitly: mpremote leaves the board there, and sending
        `reboot` to a Python prompt is just a NameError -- the device never
        restarts and every measurement after it is of the wrong thing."""
        self.s.write(b'\r')
        time.sleep(1.2)
        o = ANSI.sub('', self.s.read(20000).decode('utf-8', 'replace'))
        return '>>>' in o and 'vela' not in o

    def login(self):
        self.s.reset_input_buffer()
        if self.at_repl():
            log('   (at the bare REPL -- soft-resetting into RPCortex)')
            self.s.write(b'\x04')
            try:
                self.s.close()
            except Exception:
                pass
            time.sleep(45)
            self.s = port()
            time.sleep(4)
        self.s.reset_input_buffer()
        out = self.send('', 2.5)
        if 'Username' in out:
            self.send('root', 2.5)
            self.send(PASSWORD, 7.0)
        self.s.reset_input_buffer()

    def mem(self):
        """(free_kb, largest_kb) or (None, None)."""
        o = self.send('meminfo', 6.0)
        f = re.search(r'Free\s*:\s*(\d+) KB', o)
        b = re.search(r'Largest block\s*:\s*(\d+) KB', o)
        return (int(f.group(1)) if f else None, int(b.group(1)) if b else None)

    def check(self, cond, msg):
        self.checks += 1
        if not cond:
            self.fails += 1
            log('  FAIL:', msg)
        return cond

    def alive(self):
        o = self.send('', 2.0)
        return PROMPT in o or '>' in o


def main():
    d = Dev()
    # Reboot FIRST. A previous session leaves every command module it touched in
    # the cache, so measuring without this reports the last run's state, not the
    # device's.
    d.s.write(b'\x03\r'); time.sleep(1)
    d.login()                       # handles a bare REPL, then logs in
    # The GUI autostarts from services.cfg, so stop it first or the "before the
    # GUI" baseline is really "with the GUI", and every later delta is measured
    # against the wrong number.
    d.send('novagui stop', 6.0)
    time.sleep(3)
    d.send('freeup', 4.0)           # start from a known-clean cache
    log('=== 1. baseline, GUI stopped ===')
    f0, b0 = d.mem()
    log('   free %s KB   largest %s KB' % (f0, b0))
    d.check(f0 and f0 > 180, 'plenty of headroom before the GUI (%s KB)' % f0)

    log('\n=== 2. start the GUI as a background service ===')
    o = d.send('novagui --bg', 12.0)
    d.check('started' in o or 'already' in o, 'GUI service started (%r)' % o[-70:])
    time.sleep(6)
    f1, b1 = d.mem()
    log('   free %s KB   largest %s KB   (GUI costs %s KB)'
        % (f1, b1, (f0 - f1) if f0 and f1 else '?'))
    d.check(f1 and f1 > 60, 'usable headroom WITH the GUI running (%s KB)' % f1)
    # With the reserve armed the block is HELD, so the free-block probe can read
    # low and a handshake still succeed -- that is the whole point of it. Check the
    # reserve rather than the probe.
    o = d.send('meminfo', 6.0)
    d.check('held,' in o,
            'the TLS reserve is held with the GUI running (%r)' % o.strip()[-70:])

    log('   GUI service state: ' + ('running' if 'running' in d.send('novagui status', 5.0) else 'STOPPED'))

    log('\n=== 3. every shell command, twice, while the GUI runs ===')
    cmds = ['ver', 'sysinfo', 'uptime', 'date', 'df', 'meminfo', 'defrag',
            'radio status', 'd1 status', 'd1 pins', 'novagui status',
            'd1 radar', 'wifi status', 'pkg list', 'users', 'which ls',
            'echo hi', 'help', 'update channel', 'd1 incognito status',
            'd1 web', 'ls /Core', 'free', 'history']
    for rnd in (1, 2):
        for c in cmds:
            o = d.send(c, 3.2)
            bad = ('Traceback' in o or 'memory allocation failed' in o
                   or "isn't defined" in o or 'raised an error' in o)
            d.check(not bad, 'round %d: %r -> %s' % (rnd, c, o.strip()[-90:]))
        f, b = d.mem()
        st = 'running' if 'running' in d.send('novagui status', 4.0) else 'STOPPED'
        log('   after round %d: free %s KB  largest %s KB   GUI %s' % (rnd, f, b, st))
        d.check(st == 'running', 'round %d: the GUI survived the sweep' % rnd)
        d.send('freeup', 4.0)
        d.check(f and f > 40, 'round %d left headroom (%s KB)' % (rnd, f))

    log('\n=== 4. repeated GUI restarts (service churn) ===')
    for i in range(4):
        d.send('novagui stop', 5.0)
        o = d.send('novagui --bg', 9.0)
        d.check('started' in o, 'restart %d started the service' % (i + 1))
    f2, b2 = d.mem()
    log('   after 4 restarts: free %s KB  largest %s KB' % (f2, b2))
    d.check(f2 and f1 and f2 > f1 - 30,
            'restarts do not leak (%s -> %s KB)' % (f1, f2))

    log('\n=== 5. hammer the heaviest commands ===')
    for i in range(6):
        d.send('d1 status', 3.5)
        d.send('sysinfo', 3.0)
        d.send('meminfo', 3.5)
    f3, b3 = d.mem()
    log('   after 18 heavy commands: free %s KB  largest %s KB' % (f3, b3))
    d.check(f3 and f3 > 40, 'still has headroom (%s KB)' % f3)
    d.check(d.alive(), 'the shell is still responsive')

    log('\n=== 5b. HTTPS actually works after all that ===')
    # The check this whole session was about: after sustained use, can the device
    # still start a TLS handshake? Before the reserve was armed by default it
    # could not -- 52 KB free, largest block 10 KB, and every `update check`
    # failed on a handshake needing 16.9 KB unbroken.
    o = d.send('update check', 50.0)
    d.check('Latest version' in o or 'up to date' in o.lower(),
            'update check completes over HTTPS after heavy use (%r)'
            % o.strip()[-110:])
    d.check('contiguous' not in o.lower(),
            'and does not fail for want of a contiguous block')

    log('\n=== 6. defrag recovers ===')
    o = d.send('defrag', 8.0)
    f4, b4 = d.mem()
    log('   after defrag: free %s KB  largest %s KB' % (f4, b4))
    d.check(b4 and b4 >= 17, 'contiguous block back for HTTPS (%s KB)' % b4)

    log('\n=== 7. incognito hard stop ===')
    o = d.send('radio off', 6.0)
    d.check('LOCK' in o.upper(), 'radios locked (%r)' % o.strip()[-60:])
    o = d.send('wifi scan', 8.0)
    d.check('LOCK' in o.upper() or 'lock' in o,
            'a scan while locked is refused and SAYS so (%r)' % o.strip()[-80:])
    o = d.send('radio on', 6.0)
    d.check('release' in o.lower() or 'available' in o.lower(), 'radios released')

    log('\n=== 8. survive a reboot with the GUI autostarting ===')
    d.send('service clear', 5.0)
    d.send('service add novagui --bg', 5.0)
    d.s.write(b'reboot\r'); time.sleep(1.2); d.s.write(b'y\r')
    try:
        d.s.close()
    except Exception:
        pass
    time.sleep(50)
    d.s = port()
    time.sleep(5)
    d.login()
    o = d.send('novagui status', 6.0)
    d.check('running' in o, 'the GUI autostarted after reboot (%r)' % o.strip()[-60:])
    f5, b5 = d.mem()
    log('   after reboot with GUI: free %s KB  largest %s KB' % (f5, b5))
    d.check(f5 and f5 > 60, 'boots with headroom (%s KB)' % f5)
    o = d.send('meminfo', 6.0)
    d.check('held,' in o, 'and the TLS reserve is re-armed at boot')

    log('\n=== RESULT: %d/%d checks passed ===' % (d.checks - d.fails, d.checks))
    log('memory across the run: %s -> %s -> %s -> %s KB' % (f0, f1, f3, f5))
    d.s.close()
    return 1 if d.fails else 0


if __name__ == '__main__':
    sys.exit(main())

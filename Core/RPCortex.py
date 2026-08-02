# Desc: Core output utilities and session logging for RPCortex - Vela OS
# File: /Core/RPCortex.py
# Last Updated: 6/10/2026
# Lang: MicroPython, English
# Version: v1.0.0
# Author: dash1101

import os
import sys
import time

# Single source of truth for the running code's version and codename.
# initialization.start() syncs Settings.Version and System.Codename in the
# registry to these values on every boot, so the registry can never drift
# after an OS update.
OS_VERSION  = "v1.0.0"
OS_CODENAME = "RPCortex Vela"

# OS_BUILD is a date/time build id stamped by build.py into a generated
# Core/buildinfo.py at release-build time. A from-source/dev tree has no
# buildinfo, so it reports "source"/"dev". The build id lets the updater tell
# two builds of the SAME version apart (re-publishing v0.9.1 bumps the build).
# OS_STAGE is the release channel (Stable/Beta/Alpha/RC/Release) from build.cfg.
try:
    from buildinfo import BUILD as OS_BUILD
except Exception:
    OS_BUILD = "source"
try:
    from buildinfo import STAGE as OS_STAGE
except Exception:
    OS_STAGE = "dev"

post_check = True

# ---------------------------------------------------------------------------
# Output capture + command exit-status tracking  (pipes, && / ||, scripting)
#
# multi() is the data channel (stdout-like): when a capture buffer is active it
# is collected instead of printed, so the shell can feed it to the next stage
# of a pipeline.  The status helpers (ok/info/warn/error/fatal) always print —
# they are stderr-like and never become piped data.
#
# error()/fatal() additionally set _had_error.  The shell clears it before each
# command and reads it after, deriving a pass/fail exit status for && / || and
# script conditionals WITHOUT every command needing to return one.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Radio lock (airplane / incognito)
# ---------------------------------------------------------------------------
# A hard stop on every radio, enforced at the LOWEST level rather than by asking
# callers nicely. Turning an interface off does not hold: the next scan() or
# connect() simply turns it back on, which is why a "killed" radio could still be
# scanned and connected. The lock is a latch that net.py consults before handing
# out an interface at all, so there is no path that quietly re-enables it.
#
# Settings.Radio_Lock — 'on' locks. It persists, so a locked device stays locked
# across a reboot: a privacy switch that forgets itself when the battery dips is
# not a privacy switch.
_RADIO_LOCK_KEY = 'Settings.Radio_Lock'


def radio_locked():
    """True while every radio is being held down."""
    try:
        import regedit
        return str(regedit.read(_RADIO_LOCK_KEY) or 'off').lower() in ('on', 'true', '1')
    except Exception:
        return False


def lock_radios(on=True):
    """Engage or release the lock. Engaging also takes the interfaces down NOW;
    the latch is what stops them coming back up."""
    try:
        import regedit
        regedit.save(_RADIO_LOCK_KEY, 'on' if on else 'off')
    except Exception:
        return False
    if on:
        _radios_down()
    return True


def _radios_down():
    """Best-effort: deactivate everything we can reach. Each radio is isolated so
    a missing one cannot stop the others being silenced."""
    try:
        import network
        for attr in ('STA_IF', 'AP_IF'):
            try:
                w = network.WLAN(getattr(network, attr))
                try:
                    if w.isconnected():
                        w.disconnect()
                except Exception:
                    pass
                w.active(False)
            except Exception:
                pass
    except Exception:
        pass
    try:
        import bluetooth
        bluetooth.BLE().active(False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TLS heap reserve (ballast)
# ---------------------------------------------------------------------------
# A TLS handshake needs ONE unbroken ~16.7 KB block: MicroPython builds mbedTLS
# with MBEDTLS_SSL_IN_CONTENT_LEN 16384 and mbedtls_ssl_setup() takes the input
# buffer as a single m_tracked_calloc out of the GC heap. The GC never compacts
# (py/gc.c is non-moving), so once a long-running device has carved that heap up,
# no amount of collecting will produce such a run again — gc.mem_free() can say
# 90 KB while the largest hole is a fraction of that. This is why HTTPS works
# from a fresh boot and fails an hour later.
#
# The fix is to claim the block while the heap is still clean and hold it, then
# hand it over at the moment it is needed. From py/gc.c this works because:
#   - gc_alloc scans FIRST-FIT from area->gc_last_free_atb_index
#   - gc_free pulls that index back to the freed block if it is earlier
#   - gc_collect_end resets the index to 0
# so a block released low in the heap is the first thing the next scan finds, and
# the handshake's allocation lands in exactly the hole we just opened. Nothing
# must allocate in between, which is why release_reserve() is called immediately
# before the wrap and nowhere else.
#
# Gated by Settings.TLS_Reserve — 'off' (the DEFAULT), 'auto', or 'on'.
#
# It defaults OFF, and that default is the result of getting this wrong on
# hardware. Briefly defaulting it to 'auto' cost a Pico 2 W a third of its
# remaining free heap: free fell from ~53 KB to ~32 KB, and the OS then could not
# import a 2.7 KB command module. `wifi autoconnect` and `sreboot` both died
# allocating 1148 bytes and dropped the board to the REPL.
#
# The reason 'auto' did not save it is worth keeping written down: it measured
# headroom AT ARM TIME. This runs early in boot, when the heap is nearly empty,
# so `mem_free() >= size * 4` passed trivially — and then the GUI, its screens and
# the background services all loaded on top of a heap that was 17 KB smaller than
# they were built for. A guard that samples before the load it is guarding against
# is not a guard.
#
# 'auto' now also releases the block automatically when free memory falls below
# RELEASE_FLOOR, so even a device that arms it cannot be starved by it. But the
# honest summary is that 17 KB is a lot on this board, and holding it should be a
# deliberate choice made by someone watching `meminfo`.
TLS_RESERVE_BYTES = 16384 + 1024      # input buffer + record overhead + slack
_tls_reserve = None


def arm_reserve(size=None, force=False):
    """Claim the contiguous TLS block. Call once at boot, while the heap is clean.

    OFF by default. Turning it on was tried and MEASURED, and the measurement is
    worth keeping because the result is not the obvious one.

    From a fresh boot it works: a Pico 2 W that had been failing `update check`
    completed it with the block claimed. But a soak told a different story. After
    a long session the same board sat at 34 KB free WITH the reserve held — the
    17 KB comes straight out of the working set — and `update check` still failed,
    now with MBEDTLS_ERR_MPI_ALLOC_FAILED. The handshake had got past the input
    buffer the reserve guarantees and died on the RSA/MPI allocations behind it.
    Ordinary commands began failing at 'allocating 2071 bytes' in the same state.

    So the reserve does not fix a degraded heap; it moves the failure later and
    costs 17 KB of the memory the rest of the system needs. From a fresh boot,
    HTTPS tends to work anyway — which is where the earlier "verified" result came
    from, and why it did not generalise.

    It remains available (`Settings.TLS_Reserve = on`) for a device that mostly
    idles and needs one reliable download, and relieve_reserve() is now actually
    called so it is no longer a permanent tax. NOTE: the release was not observed
    firing on-device at 34 KB free with RELEASE_FLOOR at 40 KB — DEVICE-UNCONFIRMED,
    and the reason to leave the default off until it is understood.

    Returns True if the reserve is held. Never raises: failing to arm just means
    the device behaves exactly as it did before."""
    global _tls_reserve
    if _tls_reserve is not None:
        return True
    mode = 'off'
    try:
        import regedit
        mode = str(regedit.read('Settings.TLS_Reserve') or 'off').strip().lower()
    except Exception:
        pass
    if not force and mode not in ('on', 'auto', 'true', '1'):
        return False
    size = size or TLS_RESERVE_BYTES
    try:
        import gc
        gc.collect()
        if not force:
            # Don't take the last of a tight heap — that would make the very
            # problem this exists to prevent strictly worse. Applied to every
            # mode, not just 'auto': there is no board on which claiming the
            # block is worth doing when there is barely room for it.
            #
            # Its own try: a port without gc.mem_free() should still get the
            # reserve, not silently lose it to a failed headroom check.
            try:
                if gc.mem_free() < size * 4:
                    return False
            except Exception:
                pass
        _tls_reserve = bytearray(size)
        return True
    except Exception:
        _tls_reserve = None
        return False


def release_reserve():
    """Hand the block over, immediately before a TLS handshake. True if we had one.

    The collect matters as much as the free: it resets the allocator's scan index
    to the start of the heap, so the very next allocation finds this hole first."""
    global _tls_reserve
    if _tls_reserve is None:
        return False
    _tls_reserve = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    return True


RELEASE_FLOOR = 40960      # below this much free, the reserve gives itself back


def relieve_reserve():
    """Give the block back if the heap has got tight since it was claimed.

    Called from the shell's idle path. The reserve is only ever worth holding
    while there is room to spare; once free memory is down near the floor, 17 KB
    set aside for a hypothetical download is 17 KB the running system needs more.
    Returns True if it released."""
    if _tls_reserve is None:
        return False
    try:
        import gc
        if gc.mem_free() >= RELEASE_FLOOR:
            return False
    except Exception:
        return False
    return release_reserve()


def reserve_state():
    """(held, size) — for `meminfo` and the Nova D1 troubleshoot screen."""
    return (_tls_reserve is not None,
            len(_tls_reserve) if _tls_reserve is not None else 0)


_capture   = None     # list buffer while capturing multi() output, else None
_had_error = False    # set by error()/fatal(); cleared per command by the shell


def begin_capture():
    """Start buffering multi() output. Returns the previous buffer (nesting-safe)."""
    global _capture
    prev = _capture
    _capture = []
    return prev


def end_capture(prev=None):
    """Stop buffering; return captured text and restore the previous buffer."""
    global _capture
    text = ''.join(_capture) if _capture is not None else ''
    _capture = prev
    return text


def is_capturing():
    """True while multi() output is being captured (i.e. piped onward)."""
    return _capture is not None


def clear_error():
    """Reset the per-command error flag (call before dispatching a command)."""
    global _had_error
    _had_error = False


def had_error():
    """True if error()/fatal() was called since the last clear_error()."""
    return _had_error

# ---------------------------------------------------------------------------
# ANSI color constants
# ---------------------------------------------------------------------------

HEADER    = '\033[95m'
OKBLUE    = '\033[94m'
OKCYAN    = '\033[96m'
WARNING   = '\033[93m'
GRAY      = '\033[90m'
GREEN     = '\033[32m'
WHITE     = '\033[0m'   # NB: this is ANSI reset/default, not white — used to reset color.
FAIL      = '\033[91m'  #     Bright white is WHITE_AT ('\033[97m').
BOLD      = '\033[1m'
UNDERLINE = '\033[4m'
WHITE_AT  = '\033[97m'

# ---------------------------------------------------------------------------
# Session log
# ---------------------------------------------------------------------------

LOG_DIR    = '/Vela/Logs'
LATEST_LOG = LOG_DIR + '/latest.log'
MAX_LOGS   = 10

_log_file    = None   # open file handle during a session; None otherwise
_log_pending = 0      # lines written since last flush (batched to cut flash latency)


def init_session_log():
    """Open a new session log file. Call once after successful login."""
    global _log_file, _log_pending
    _log_pending = 0
    try:
        try:
            os.mkdir(LOG_DIR)
        except OSError:
            pass   # already exists
        rename_logs()
        _log_file = open(LATEST_LOG, 'w')
        t = time.localtime()
        _log_file.write(
            "=== RPCortex Vela - Session Log ===\n"
            "Started : {}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}\n"
            "=====================================\n\n".format(
                t[0], t[1], t[2], t[3], t[4], t[5]
            )
        )
        _log_file.flush()
    except Exception:
        _log_file = None   # logging unavailable; non-fatal


def close_session_log():
    """Flush and close the session log. Call on logout or shutdown."""
    global _log_file
    if _log_file:
        try:
            t = time.localtime()
            _log_file.write(
                "\n=== Session ended {}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} ===\n".format(
                    t[0], t[1], t[2], t[3], t[4], t[5]
                )
            )
            _log_file.flush()
            _log_file.close()
        except Exception:
            pass
        _log_file = None


def _log_write(level, msg):
    """Internal: append one line to the open session log.

    Flushes are batched — flushing flash on every line caused visible lag
    between rapid output calls. Errors and warnings flush immediately so
    the crash log stays useful; routine lines flush every 8 writes.
    """
    global _log_pending
    if not _log_file:
        return
    try:
        t = time.localtime()
        _log_file.write(
            "{}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} [{:<5}] {}\n".format(
                t[0], t[1], t[2], t[3], t[4], t[5], level, msg
            )
        )
        _log_pending += 1
        if _log_pending >= 8 or level in ('ERROR', 'FATAL', 'WARN'):
            _log_file.flush()
            _log_pending = 0
    except Exception:
        pass


def rename_logs():
    """Rotate logs: latest.log -> log_1, log_1 -> log_2, ..., up to MAX_LOGS."""
    for i in range(MAX_LOGS - 1, 0, -1):
        src = LOG_DIR + '/log_{}.log'.format(i)
        dst = LOG_DIR + '/log_{}.log'.format(i + 1)
        try:
            os.rename(src, dst)
        except OSError:
            pass
    try:
        os.rename(LATEST_LOG, LOG_DIR + '/log_1.log')
    except OSError:
        pass


def log(msg):
    """Write a raw message directly to the session log."""
    _log_write('LOG', msg)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def file_exists(filepath):
    try:
        import uos
        uos.stat(filepath)
        return True
    except OSError:
        return False


def str_to_bool(value):
    v = value.lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError("Cannot convert '{}' to bool".format(value))


# ---------------------------------------------------------------------------
# Storage guard — shared by pkg install / downloads / logging apps.
# Onboard flash must never fill completely: an OS update needs headroom to
# unpack. So writes warn hard at 95% and stop at 98%, leaving ~2% free.
# ---------------------------------------------------------------------------
STORAGE_WARN = 95      # % used: warn, but allow
STORAGE_BLOCK = 98     # % used: refuse (keep ~2% for updates)


def disk_usage_pct(path="/"):
    """Onboard flash usage as an integer percent (0-100), or None if unknown."""
    try:
        import uos
        st = uos.statvfs(path)
        total = st[2]                       # f_blocks
        free = st[3]                        # f_bfree
        if total <= 0:
            return None
        return int((total - free) * 100 // total)
    except Exception:
        return None


def storage_state(path="/"):
    """(pct, level) where level is 'ok' / 'warn' (>=95%) / 'block' (>=98%).
    level is 'ok' when usage can't be read, so a probe failure never blocks."""
    pct = disk_usage_pct(path)
    if pct is None:
        return None, "ok"
    if pct >= STORAGE_BLOCK:
        return pct, "block"
    if pct >= STORAGE_WARN:
        return pct, "warn"
    return pct, "ok"


# ---------------------------------------------------------------------------
# Output functions
# All print to the terminal AND write to the session log if one is active.
# ---------------------------------------------------------------------------

def _fmt(color, symbol, msg, p, nL):
    """Build and print a tagged output line."""
    out = "{}[{}{}{}]".format(color, WHITE_AT, symbol, color)
    if p is not None:
        out += " {}[{}{}{}]".format(color, WHITE_AT, p, color)
    out += " {}{}".format(WHITE, msg)
    if nL:
        out += '\n'
    if _capture is not None:
        # Capture info/ok/warn/error too, not just multi(). Previously only multi()
        # was buffered, so a command whose whole result came from ok() (freeup,
        # wifi status, ...) captured NOTHING and the GUI could only show '(done)'.
        _capture.append(out)
        return
    sys.stdout.write(out)   # faster than print() on MicroPython (no arg/sep/end work)


def error(msg, nL=True, p=None):
    global _had_error
    _had_error = True   # failure signal for && / || and script conditionals
    if post_check:
        _fmt(FAIL, '!', msg, p, nL)
        _log_write('ERROR', ('[{}] '.format(p) if p else '') + str(msg))


def fatal(msg, nL=True, p=None):
    global _had_error
    _had_error = True
    if post_check:
        _fmt(FAIL, '!!!', msg, p, nL)
        _log_write('FATAL', ('[{}] '.format(p) if p else '') + str(msg))


def info(msg, nL=True, p=None):
    if post_check:
        _fmt(HEADER, ':', msg, p, nL)
        _log_write('INFO', ('[{}] '.format(p) if p else '') + str(msg))


def warn(msg, nL=True, p=None):
    if post_check:
        _fmt(WARNING, '?', msg, p, nL)
        _log_write('WARN', ('[{}] '.format(p) if p else '') + str(msg))


def ok(msg, nL=True, p=None):
    if post_check:
        _fmt(OKCYAN, '@', msg, p, nL)
        _log_write('OK', ('[{}] '.format(p) if p else '') + str(msg))


def multi(msg, nL=True, p=None):
    # The high-volume display/data channel (cat/ls/grep/TUI output). Uses
    # sys.stdout.write (faster than print) and is NOT logged per line — logging
    # every display line to flash was the main drag on text-heavy output. The
    # diagnostic log still captures events (info/ok/warn/error/fatal).
    if post_check:
        out = msg + ('\n' if nL else '')
        if _capture is not None:
            _capture.append(out)   # piped onward instead of printed
        else:
            sys.stdout.write(out)


# ---------------------------------------------------------------------------
# In-place spinner — for any operation that makes the user wait (WiFi connect,
# downloads, scans). Renders "<label> \ (3s)" on one line, updating in place.
# ---------------------------------------------------------------------------
_SPIN_FRAMES = '-\\|/'

def spin(label, i, start_ms):
    """Render the spinner once: '<label> <frame> (<elapsed>s)'.
    Call repeatedly with an incrementing i and the start tick from utime.ticks_ms()."""
    try:
        import utime
        secs = utime.ticks_diff(utime.ticks_ms(), start_ms) // 1000
    except Exception:
        secs = 0
    ch = _SPIN_FRAMES[i % len(_SPIN_FRAMES)]
    sys.stdout.write('\r\x1b[K{} {} ({}s)'.format(label, ch, secs))

def spin_done(msg=None):
    """Clear the spinner line; print a final message on its own line if given."""
    sys.stdout.write('\r\x1b[K')
    if msg is not None:
        sys.stdout.write(msg + '\n')


def inpt(msg):
    # Always return a string — callers do inpt(...).strip(). Returning None when
    # post_check is off (as an earlier version did) was a latent AttributeError.
    if not post_check:
        return ''
    return input("{}{} {}••>  {}".format(WHITE, msg, OKCYAN, WHITE))


def masked_inpt(msg):
    """Like inpt() but echoes a bullet (•) for each character.
    Falls back to regular inpt() on platforms where raw stdin isn't available."""
    if not post_check:
        return ''
    prompt_str = "{}{} {}••>  {}".format(WHITE, msg, OKCYAN, WHITE)
    sys.stdout.write(prompt_str)
    buf = []
    skip_lf = False
    try:
        while True:
            ch = sys.stdin.read(1)
            if skip_lf:
                skip_lf = False
                if ch == '\n':
                    continue
            if ch in ('\r', '\n'):
                if ch == '\r':
                    skip_lf = True
                sys.stdout.write('\r\n')
                return ''.join(buf)
            elif ch in ('\x7f', '\x08'):   # backspace / DEL
                if buf:
                    buf.pop()
                    sys.stdout.write('\x08 \x08')
            elif ch == '\x03':             # Ctrl+C — treat as empty input
                sys.stdout.write('^C\r\n')
                return ''
            elif ord(ch) >= 32:
                buf.append(ch)
                sys.stdout.write('\u2022')  # bullet point
    except Exception:
        # stdin read failed (e.g. non-interactive context) — fall back
        sys.stdout.write('\r\n')
        return ''.join(buf)

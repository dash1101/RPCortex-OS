# Desc: appkit - cooperative app framework for the RPCortex async shell
# File: /Core/appkit.py
# Lang: MicroPython, English
# Version: v1.0.0 "Vela"
#
# The substrate that lets a foreground TUI app run on the SAME asyncio event loop
# as background services, so e.g. `sysmon` refreshes WHILE `httpd --bg` serves.
# Two rules make sharing one terminal + one loop safe:
#
#   1. SCREEN OWNERSHIP — only the current owner may write visibly to the
#      terminal. Background components (the task scheduler, future notifications)
#      check shell_owns_screen() and stay quiet while a full-screen app is up, so
#      a stray `[task]` line can never corrupt an app's display.
#   2. NEVER BLOCK THE LOOP — apps read input via `await read_key()` and pace
#      redraws with `await asyncio.sleep_ms(...)`, yielding between, so services
#      keep ticking. (A blocking `while`/`input()` would freeze everything.)
#
# This is the Tier-1 cooperative model: apps/services interleave at refresh /
# command / accept() boundaries. Async I/O (smooth-under-load) is Tier 2 / later.
#
# MicroPython-safe: no f-strings, positional str.split(), .format() only.

import sys

# ---------------------------------------------------------------------------
# Screen ownership
# ---------------------------------------------------------------------------
# The component currently allowed to draw to the terminal. 'shell' = the prompt
# owns it; an app name (e.g. 'sysmon') while a full-screen app is foreground.
_owner = 'shell'


def claim_screen(name):
    """A foreground app takes the terminal. Returns the previous owner so it can
    be restored (run_foreground does this automatically)."""
    global _owner
    prev = _owner
    _owner = name
    return prev


def release_screen(prev='shell'):
    global _owner
    _owner = prev


def current_owner():
    return _owner


def shell_owns_screen():
    """True when the interactive prompt owns the screen (no full-screen app up).
    Background printers gate their visible output on this."""
    return _owner == 'shell'


# ---------------------------------------------------------------------------
# Async input
# ---------------------------------------------------------------------------
async def read_key(timeout_ms=None):
    """Await a single key from stdin WITHOUT blocking the event loop. Returns the
    1-char string, or '' on timeout (when timeout_ms is given). Polls with a
    zero-timeout select and yields via asyncio.sleep_ms between polls, so
    background coroutines keep running while we wait for a keypress."""
    import asyncio
    import select as _sel
    import utime as _ut
    deadline = None
    if timeout_ms is not None:
        deadline = _ut.ticks_add(_ut.ticks_ms(), timeout_ms)
    while True:
        try:
            r = _sel.select([sys.stdin], [], [], 0)[0]
        except Exception:
            r = None
        if r:
            try:
                return sys.stdin.read(1)
            except Exception:
                return ''
        if deadline is not None and _ut.ticks_diff(_ut.ticks_ms(), deadline) >= 0:
            return ''
        await asyncio.sleep_ms(15)


async def read_escape():
    """Called right after a `\\x1b` (ESC) was read: drain the rest of a CSI/SS3
    escape sequence and return the FULL sequence (e.g. '\\x1b[A', '\\x1b[1;5C').
    Bounded and non-blocking; if nothing follows quickly it returns just '\\x1b'
    (a bare ESC press)."""
    import asyncio
    import select as _sel
    seq = '\x1b'
    for i in range(10):
        got = False
        for _ in range(3):                     # brief grace for the next byte
            try:
                if _sel.select([sys.stdin], [], [], 0)[0]:
                    got = True
                    break
            except Exception:
                pass
            await asyncio.sleep_ms(3)
        if not got:
            break
        ch = sys.stdin.read(1)
        seq += ch
        if len(seq) == 2 and ch not in ('[', 'O'):
            break                              # not a CSI/SS3 intro — stop
        if len(seq) > 2 and '@' <= ch <= '~':  # CSI/SS3 final byte
            break
    return seq


# ---------------------------------------------------------------------------
# Screen helpers (thin ANSI wrappers)
# ---------------------------------------------------------------------------
def w(s):
    sys.stdout.write(s)


def clear():
    sys.stdout.write('\x1b[2J\x1b[H')


def home():
    sys.stdout.write('\x1b[H')


def move(row, col):
    sys.stdout.write('\x1b[{};{}H'.format(row, col))


def erase_eol():
    sys.stdout.write('\x1b[K')


def hide_cursor():
    sys.stdout.write('\x1b[?25l')


def show_cursor():
    sys.stdout.write('\x1b[?25h')


# ---------------------------------------------------------------------------
# Foreground app runner
# ---------------------------------------------------------------------------
async def run_foreground(coro, name):
    """Run an app coroutine as the screen-owning foreground. Claims the screen,
    awaits the app, and ALWAYS restores the previous owner + shows the cursor on
    exit (even on error/cancel) — so a crashing app can't leave the terminal
    'stuck owned' or the cursor hidden."""
    prev = claim_screen(name)
    try:
        await coro
    finally:
        release_screen(prev)
        try:
            show_cursor()
        except Exception:
            pass

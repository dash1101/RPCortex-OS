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
# A persistent poll object registered with stdin ONCE. Rebuilding
# select([sys.stdin],[],[],0) on every poll allocates four lists per call — on the
# hot keystroke path that is steady GC pressure (and a GC pause shows up as a
# typing hiccup). poll().poll(0) is the readiness primitive MicroPython recommends
# and allocates less. NOTE: poll and select share the same underlying stream ioctl,
# so this is a GC/throughput win, not a cure for COARSE readiness (if the port only
# refreshes the ready flag on a tick, both are equally stale — that case needs the
# loop's interrupt-driven reader; `inputstat` below tells us which it is).
_poll = None
_poll_ok = None        # None = untried, True = poll usable, False = fall back to select

# Input instrumentation, read by the `inputstat` command. The key signal is
# max_drain: how many bytes drain_printable pulled in ONE event-loop turn. ~1 while
# typing = readiness is prompt (smooth). A large value = bytes bunched between
# ready signals = COARSE select -> chunky typing (the reported symptom).
input_stats = {'reads': 0, 'empty_polls': 0, 'max_run': 0,
               'drains': 0, 'max_drain': 0, 'total_drained': 0}
_empty_run = 0


def _stdin_ready():
    """True if a byte is waiting on stdin. Persistent poll object, select fallback.
    Centralizes the readiness check for read_key / drain_printable / read_escape."""
    global _poll, _poll_ok
    if _poll_ok is None:
        try:
            import select as _sel
            _poll = _sel.poll()
            _poll.register(sys.stdin, _sel.POLLIN)
            _poll_ok = True
        except Exception:
            _poll_ok = False
    if _poll_ok:
        try:
            return bool(_poll.poll(0))
        except Exception:
            _poll_ok = False
    try:
        import select as _sel
        return bool(_sel.select([sys.stdin], [], [], 0)[0])
    except Exception:
        return False


# --- optional interrupt-driven reader (Settings.Stream_Input, default OFF) -----
# When the port's select() reports readiness only COARSELY (bytes bunch -> chunky
# typing; confirm with `inputstat`), polling can't fix it. This path instead waits
# on an asyncio.StreamReader over stdin, so the EVENT LOOP's poller (interrupt
# driven, like the REPL) wakes us the instant a byte lands — no poll latency.
# Gated + lazy + self-disabling: if the port can't wrap stdin as a stream it falls
# back to polling permanently, and a hard crash trips the async-boot sentinel which
# drops to the sync shell next boot. read(1) consumes exactly one byte, so the rest
# stay in the FIFO and the unchanged drain_printable/read_escape see them.
_stream_mode = None        # None = undecided, True = stream reader, False = poll
_stream_reader = None


def _resolve_stream_mode():
    global _stream_mode, _stream_reader
    if _stream_mode is not None:
        return _stream_mode
    on = False
    try:
        import regedit
        on = (regedit.read('Settings.Stream_Input') or 'false').strip().lower() == 'true'
    except Exception:
        on = False
    if on:
        try:
            import asyncio
            _stream_reader = asyncio.StreamReader(sys.stdin)
            _stream_mode = True
        except Exception:
            _stream_mode = False
    else:
        _stream_mode = False
    return _stream_mode


async def _stream_read_key(timeout_ms):
    import asyncio
    global _empty_run, _stream_mode
    try:
        if timeout_ms is None:
            b = await _stream_reader.read(1)
        else:
            b = await asyncio.wait_for(_stream_reader.read(1), timeout_ms / 1000)
    except asyncio.TimeoutError:
        input_stats['empty_polls'] += 1
        _empty_run += 1
        return ''
    except Exception:
        _stream_mode = False              # misbehaved -> permanent fallback to polling
        return ''
    if not b:
        return ''
    ch = b.decode() if isinstance(b, (bytes, bytearray)) else b
    input_stats['reads'] += 1
    if _empty_run > input_stats['max_run']:
        input_stats['max_run'] = _empty_run
    _empty_run = 0
    return ch


async def read_key(timeout_ms=None, poll_ms=10):
    """Await a single key from stdin WITHOUT blocking the event loop. Returns the
    1-char string, or '' on timeout (when timeout_ms is given). Checks readiness
    with a non-blocking poll and yields via asyncio.sleep_ms between checks, so
    background coroutines keep running while we wait for a keypress.

    `poll_ms` is the idle poll interval — the worst-case latency between a *fresh*
    keypress (after a pause) and us seeing it. Queued bytes return with no sleep at
    all, so CONTINUOUS typing is unpaced. Callers wanting a coarse refresh tick
    (e.g. an app's 1 s redraw wait) can pass a larger poll_ms."""
    import asyncio
    import utime as _ut
    global _empty_run
    if _resolve_stream_mode():
        return await _stream_read_key(timeout_ms)
    deadline = None
    if timeout_ms is not None:
        deadline = _ut.ticks_add(_ut.ticks_ms(), timeout_ms)
    while True:
        if _stdin_ready():
            try:
                ch = sys.stdin.read(1)
            except Exception:
                ch = ''
            input_stats['reads'] += 1
            if _empty_run > input_stats['max_run']:
                input_stats['max_run'] = _empty_run
            _empty_run = 0
            return ch
        if deadline is not None and _ut.ticks_diff(_ut.ticks_ms(), deadline) >= 0:
            return ''
        input_stats['empty_polls'] += 1
        _empty_run += 1
        await asyncio.sleep_ms(poll_ms)


def drain_printable(maxn=256):
    """SYNCHRONOUSLY pull already-buffered EDITING chars (printables + backspace,
    no await/yield) so a paste OR a held key/backspace goes in one event-loop turn
    instead of one char per turn. Returns (chars, leftover) where leftover is the
    first non-editing char read (already consumed — caller: '\\r'/'\\n' = submit,
    else drop), or None. Reads nothing for a lone keypress (select not ready)."""
    out = ''
    leftover = None
    for _ in range(maxn):
        try:
            if not _stdin_ready():
                break
            ch = sys.stdin.read(1)
        except Exception:
            break
        if not ch:
            break
        if (0x20 <= ord(ch) < 0x7f) or ch in ('\x7f', '\x08'):
            out += ch
        else:
            leftover = ch
            break
    # instrumentation: bytes coalesced in this one turn (the chunkiness signal)
    n = len(out) + (1 if leftover is not None else 0)
    if n:
        input_stats['drains'] += 1
        input_stats['total_drained'] += n
        if n > input_stats['max_drain']:
            input_stats['max_drain'] = n
    return out, leftover


# ---------------------------------------------------------------------------
# Async text-line prompt (shared by converted TUIs for rename/filter/etc.)
# ---------------------------------------------------------------------------
async def read_line(label='', echo=True):
    """Cooperative replacement for a blocking input() inside a TUI: read a line of
    text via read_key (so background services keep running while the user types a
    filename/search). Returns the string (without the trailing newline), or None
    if cancelled with ESC. Handles backspace; ignores other control/escape keys."""
    buf = []
    if label:
        w(label)
    while True:
        ch = await read_key()
        if ch in ('\r', '\n'):
            if echo:
                w('\r\n')
            return ''.join(buf)
        if ch == '\x1b':                       # ESC cancels (drain any CSI tail)
            await read_escape()
            return None
        if ch in ('\x08', '\x7f'):             # backspace
            if buf:
                buf.pop()
                if echo:
                    w('\x08 \x08')
            continue
        if ch and ord(ch) >= 32:
            buf.append(ch)
            if echo:
                w(ch)


async def read_escape():
    """Called right after a `\\x1b` (ESC) was read: drain the rest of a CSI/SS3
    escape sequence and return the FULL sequence (e.g. '\\x1b[A', '\\x1b[1;5C').
    Bounded and non-blocking; if nothing follows quickly it returns just '\\x1b'
    (a bare ESC press)."""
    import asyncio
    seq = '\x1b'
    for i in range(10):
        got = False
        for _ in range(3):                     # brief grace for the next byte
            if _stdin_ready():
                got = True
                break
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

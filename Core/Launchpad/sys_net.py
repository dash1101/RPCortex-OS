# Desc: Network shell commands (wget, curl, runurl, ping, nslookup) - RPCortex Pulsar OS
# File: /Core/Launchpad/sys_net.py
# Last Updated: 6/10/2026
# Lang: MicroPython, English
# Version: v0.9.1

import sys
import uos

if '/Core' not in sys.path:
    sys.path.append('/Core')

from RPCortex import warn, error, info, ok, multi, inpt


def _tokenize(s):
    """Split a string on whitespace, respecting single/double quotes.

    Quote characters are removed; an empty quoted token ('') is preserved.
    Lets curl flags like  -H 'Auth: x'  and  -d '{"k":1}'  parse as one token.
    """
    out, cur = [], []
    in_q, q, started = False, None, False
    for ch in s:
        if ch in ('"', "'"):
            if not in_q:
                in_q, q, started = True, ch, True
            elif ch == q:
                in_q, q = False, None
            else:
                cur.append(ch); started = True
        elif ch in (' ', '\t') and not in_q:
            if started:
                out.append(''.join(cur)); cur = []; started = False
        else:
            cur.append(ch); started = True
    if started:
        out.append(''.join(cur))
    return out


def _parse_wget_args(args):
    """Shared arg parse for wget / wget_async. Returns (url, dest) with dest
    resolved to an absolute path, or None when it printed usage."""
    if not args:
        warn("Usage: wget <url> [destination]")
        return None
    parts = args.strip().split(None, 1)
    url  = parts[0]
    dest = parts[1].strip() if len(parts) > 1 else None
    if dest and not dest.startswith('/'):
        dest = uos.getcwd().rstrip('/') + '/' + dest
    elif not dest:
        fname = url.rstrip('/').split('/')[-1] or 'download'
        dest = uos.getcwd().rstrip('/') + '/' + fname
    return (url, dest)


def _wget_free_heap():
    """Clear the command cache + GC so the TLS handshake has contiguous heap."""
    import gc
    try:
        _cmd_cache.clear()
    except NameError:
        pass
    gc.collect()


def _wget_report(status_code, written, dest):
    if status_code == 200:
        ok("Saved {} bytes to '{}'".format(written, dest))
    else:
        error("HTTP {} — file may be incomplete.".format(status_code))


def wget(args=None):
    parsed = _parse_wget_args(args)
    if not parsed:
        return
    url, dest = parsed
    if not _net_ready():
        return
    _wget_free_heap()
    from net import wget as _wget
    try:
        status_code, written = _wget(url, dest=dest, verbose=True)
        _wget_report(status_code, written, dest)
    except MemoryError as e:
        error("Not enough RAM: {}".format(e))
        info("Tip: run 'freeup' to reclaim memory, then retry.")
    except Exception as e:
        error("Download failed: {}".format(e))


async def wget_async(args=None):
    """Async 'wget' for the multitasking shell — same output as wget(), but it
    yields to the event loop on every socket wait, so background services (e.g.
    httpd --bg) keep serving WHILE the download runs, and Ctrl+C / 'q' aborts
    between chunks. The async shell routes here via _resolve_async_app.

    Async is the DEFAULT shell, so this is the default `wget`. If the async path
    raises (e.g. firmware without async-TLS support), it falls back to the proven
    synchronous net.wget(), so downloads never break just because async sockets
    misbehave on a board. A MemoryError is NOT retried on the sync path (it would
    OOM the same way) — it just reports the freeup tip."""
    parsed = _parse_wget_args(args)
    if not parsed:
        return
    url, dest = parsed
    if not _net_ready():
        return
    _wget_free_heap()
    import net
    try:
        status_code, written = await net.awget(url, dest=dest, verbose=True)
        _wget_report(status_code, written, dest)
    except MemoryError as e:
        error("Not enough RAM: {}".format(e))
        info("Tip: run 'freeup' to reclaim memory, then retry.")
    except Exception as e:
        # Device-only async-socket/TLS failure: fall back to the hardware-proven
        # synchronous download path so `wget` never breaks in the default shell.
        warn("async wget unavailable ({}); using sync wget".format(e))
        _wget_free_heap()
        try:
            from net import wget as _wget
            status_code, written = _wget(url, dest=dest, verbose=True)
            _wget_report(status_code, written, dest)
        except Exception as e2:
            error("Download failed: {}".format(e2))


def runurl(args=None):
    if not args:
        warn("Usage: runurl <url> [--keep] [-y]")
        return
    parts = args.strip().split()
    url  = parts[0]
    keep = '--keep' in parts
    yes  = '-y' in parts or '--yes' in parts

    from net import run_url, is_available, online
    if not is_available():
        error("WiFi not available on this board.")
        return
    if not online():
        error("Not connected to WiFi. Run: wifi connect")
        return

    # Code-execution guard: runurl downloads a .py and runs it with FULL device
    # access. Make that an explicit, acknowledged act so a stray startup task or
    # .rps line can't silently pull and run remote code. '-y' bypasses for
    # trusted automation.
    if not yes:
        warn("This downloads and RUNS code from:")
        multi("  " + url)
        warn("Only continue if you trust this source — it gets full device access.")
        if inpt("Run it? (yes/no)").strip().lower() not in ('y', 'yes'):
            info("Cancelled.")
            return
    try:
        run_url(url, keep=keep)
    except Exception as e:
        error("runurl failed: {}".format(e))


def _parse_curl_args(tokens):
    """Parse curl flags into (url, kwargs). Returns (None, None) on error.

    Supports: -v  -s/--silent  -I/--head  -X <method>  -d <data>
              -H <header>  -o <file>  --timeout <secs>
    """
    url     = None
    kwargs  = {'verbose': False, 'silent': False, 'head_only': False,
               'method': 'GET', 'data': None, 'headers': None,
               'output': None, 'timeout': 15}
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t == '-v':
            kwargs['verbose'] = True
        elif t in ('-s', '--silent'):
            kwargs['silent'] = True
        elif t in ('-I', '--head'):
            kwargs['head_only'] = True
        elif t == '-X' and i + 1 < n:
            kwargs['method'] = tokens[i + 1].upper(); i += 1
        elif t == '-d' and i + 1 < n:
            kwargs['data'] = tokens[i + 1]
            if kwargs['method'] == 'GET':
                kwargs['method'] = 'POST'
            i += 1
        elif t == '-H' and i + 1 < n:
            hdr = tokens[i + 1]
            if ':' in hdr:
                k, v = hdr.split(':', 1)
                if kwargs['headers'] is None:
                    kwargs['headers'] = {}
                kwargs['headers'][k.strip()] = v.strip()
            i += 1
        elif t == '-o' and i + 1 < n:
            kwargs['output'] = tokens[i + 1]; i += 1
        elif t == '--timeout' and i + 1 < n:
            try:
                kwargs['timeout'] = int(tokens[i + 1])
            except ValueError:
                pass
            i += 1
        elif t.startswith('-'):
            error("Unknown curl flag: {}".format(t))
            return None, None
        elif url is None:
            url = t
        i += 1
    return url, kwargs


def _prep_curl(args):
    """Shared parse+resolve for curl / curl_async. Returns (url, kwargs), or
    (None, None) when it printed usage/error."""
    if not args:
        warn("Usage: curl <url> [-v] [-s] [-I] [-X M] [-d data] [-H 'K: V'] [-o file] [--timeout n]")
        return None, None
    # Tokenize, honouring single/double quotes so -H 'Auth: x' and -d '{...}' work.
    tokens = _tokenize(args.strip())
    url, kwargs = _parse_curl_args(tokens)
    if url is None:
        if kwargs is not None:
            error("No URL given.")
        return None, None
    # Resolve a relative -o path against the current directory.
    if kwargs.get('output') and not kwargs['output'].startswith('/'):
        kwargs['output'] = uos.getcwd().rstrip('/') + '/' + kwargs['output']
    return url, kwargs


def curl(args=None):
    url, kwargs = _prep_curl(args)
    if url is None:
        return
    if not _net_ready():
        return
    _wget_free_heap()
    import net
    try:
        net.curl(url, **kwargs)
    except MemoryError as e:
        error("Not enough RAM: {}".format(e))
        info("Tip: run 'freeup' to reclaim memory, then retry.")
    except Exception as e:
        error("curl failed: {}".format(e))


async def curl_async(args=None):
    """Async 'curl' for the multitasking shell — same flags/output as curl(), but it
    yields to the event loop on every socket wait, so background services keep
    serving WHILE it runs and Ctrl+C/'q' aborts between chunks. The async shell
    routes a bare `curl <url>` here via _resolve_async_app (a piped `curl | ...`
    takes the sync path). Falls back to the proven synchronous net.curl() if the
    async path raises, so curl never breaks in the default shell. A MemoryError is
    not retried on the sync path (it would OOM the same way)."""
    url, kwargs = _prep_curl(args)
    if url is None:
        return
    if not _net_ready():
        return
    _wget_free_heap()
    import net
    try:
        await net.acurl(url, **kwargs)
    except MemoryError as e:
        error("Not enough RAM: {}".format(e))
        info("Tip: run 'freeup' to reclaim memory, then retry.")
    except Exception as e:
        warn("async curl unavailable ({}); using sync curl".format(e))
        _wget_free_heap()
        try:
            net.curl(url, **kwargs)
        except Exception as e2:
            error("curl failed: {}".format(e2))


def _parse_ping_args(args):
    """Shared arg parse for ping / ping_async. Returns (host, count, port), or
    None when it printed help/usage (caller just returns)."""
    if args and args.strip().lower() in ('help', '-h', '--help', '?'):
        multi("ping — TCP reachability test (measures round-trip time).")
        multi("Usage: ping <host> [count] [-p <port>]")
        multi("  <host>      hostname or IP (e.g. google.com, 8.8.8.8)")
        multi("  [count]     number of probes (default 4)")
        multi("  -p <port>   probe a specific TCP port (default: auto 443/80/53)")
        multi("Press Ctrl+C or 'q' to stop early.")
        return None
    if not args:
        warn("Usage: ping <host> [count] [-p <port>]")
        return None
    host  = None
    count = 4
    port  = None
    toks = args.split()
    j = 0
    while j < len(toks):
        t = toks[j]
        if t in ('-p', '--port') and j + 1 < len(toks):
            try:
                port = int(toks[j + 1])
            except ValueError:
                warn("Invalid port — ignoring.")
            j += 2
            continue
        if host is None:
            host = t
        else:
            try:
                count = int(t)
            except ValueError:
                pass
        j += 1
    if not host:
        warn("Usage: ping <host> [count] [-p <port>]")
        return None
    return (host, count, port)


def _net_ready():
    """True if WiFi is present and connected; prints why not otherwise."""
    from net import is_available, online
    if not is_available():
        error("WiFi not available on this board.")
        return False
    if not online():
        error("Not connected to WiFi. Run: wifi connect")
        return False
    return True


def ping(args=None):
    parsed = _parse_ping_args(args)
    if not parsed:
        return
    host, count, port = parsed
    if not _net_ready():
        return
    from net import ping as _ping
    _ping(host, count=count, port=port)


async def ping_async(args=None):
    """Async 'ping' for the multitasking shell — same output as ping() (tagged
    [async]), but it yields to the event loop between probes (via asyncio +
    appkit.read_key), so background services (e.g. httpd --bg) keep running WHILE
    you ping, and Ctrl+C / 'q' stops it promptly. The async shell routes here
    automatically through _resolve_async_app.

    Async is the DEFAULT shell, so this is the default `ping`. If the async path
    raises — e.g. an older firmware whose asyncio lacks open_connection/wait_for —
    it falls back to the proven synchronous net.ping(), so `ping` never breaks just
    because async sockets misbehave on a given board. (The sync ping() above is also
    what the classic `asyncmode off` shell uses.)"""
    parsed = _parse_ping_args(args)
    if not parsed:
        return
    host, count, port = parsed
    if not _net_ready():
        return
    import net
    try:
        await net.aping(host, count=count, port=port)
    except Exception as e:
        # Device-only async-socket failure: don't leave `ping` broken in the
        # default shell — fall back to the synchronous, hardware-proven path.
        warn("async ping unavailable ({}); using sync ping".format(e))
        net.ping(host, count=count, port=port)


def nslookup(args=None):
    if not args:
        warn("Usage: nslookup <host>")
        return
    from net import nslookup as _nslookup, is_available, online
    if not is_available():
        error("WiFi not available on this board.")
        return
    if not online():
        error("Not connected to WiFi. Run: wifi connect")
        return
    _nslookup(args.strip())

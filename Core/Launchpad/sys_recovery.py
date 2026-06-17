# Desc: Recovery & diagnostic shell commands - RPCortex Pulsar OS
# File: /Core/Launchpad/sys_recovery.py
# Last Updated: 6/10/2026
# Lang: MicroPython, English
# Version: v0.9.1
#
# Diagnostics and repair tools, usable from the normal shell or recovery mode.
# Registered via its own recovery.lp so it still loads if system.lp is damaged.
#
#   fscheck            verify core OS files are present and non-empty
#   diag               quick health snapshot (RAM, flash, registry, version)
#   logdump [n]        print the current session log (last n lines if given)
#   regreset           delete registry.cfg so POST rebuilds it (keeps users/wifi)
#   pkgdisable <name>  disable a package without removing it
#   pkgenable  <name>  re-enable a disabled package

import sys
import uos

if '/Core' not in sys.path:
    sys.path.append('/Core')

from RPCortex import warn, error, info, ok, multi, inpt

# Core files expected on a healthy install.  Missing/empty entries are flagged.
_MANIFEST = (
    '/main.py',
    '/Core/RPCortex.py',
    '/Core/regedit.py',
    '/Core/initialization.py',
    '/Core/post.py',
    '/Core/launchpad.py',
    '/Core/usrmgmt.py',
    '/Core/net.py',
    '/Core/pulse.py',
    '/Core/pkgmgr.py',
    '/Core/rpc_install.py',
    '/Core/rpc_stub.py',
    '/Core/appkit.py',
    '/Core/lineedit.py',
    '/Core/hwinfo.py',
    '/Core/Launchpad/system.lp',
    '/Core/Launchpad/sys_fs.py',
    '/Core/Launchpad/sys_sys.py',
    '/Core/Launchpad/sys_net.py',
    '/Core/Launchpad/sys_user.py',
    '/Core/Launchpad/sys_text.py',
    '/Pulsar/Registry/registry.cfg',
    '/Pulsar/Registry/user.cfg',
)

_REGISTRY = '/Pulsar/Registry/registry.cfg'
_LOG      = '/Pulsar/Logs/latest.log'


def _stat_any(path):
    """stat the path, or its .mpy counterpart — a compiled build ships e.g.
    launchpad.mpy instead of launchpad.py. Returns (stat, real_path) or None."""
    try:
        return uos.stat(path), path
    except OSError:
        pass
    if path.endswith('.py'):
        alt = path[:-3] + '.mpy'
        try:
            return uos.stat(alt), alt
        except OSError:
            pass
    return None


def fscheck(args=None):
    """Verify core OS files exist and are non-empty (source OR compiled)."""
    info("Filesystem check — {} core files".format(len(_MANIFEST)))
    missing = 0
    empty = 0
    for path in _MANIFEST:
        res = _stat_any(path)
        if res is None:
            multi("  \033[91mMISSING\033[0m {}".format(path))
            missing += 1
            continue
        st, real = res
        if st[6] == 0:
            multi("  \033[93mEMPTY  \033[0m {}".format(real))
            empty += 1
        else:
            note = '  (.mpy)' if real.endswith('.mpy') else ''
            multi("  \033[92mOK     \033[0m {}{}".format(path, note))
    multi("")
    if missing == 0 and empty == 0:
        ok("All core files present.")
    else:
        error("{} missing, {} empty. Re-imaging is recommended (update reinstall).".format(missing, empty))


def diag(args=None):
    """Quick health snapshot."""
    import gc
    gc.collect()
    info("=== Diagnostics ===")
    free = gc.mem_free()
    alloc = gc.mem_alloc()
    multi("  Free RAM    : {} KB / {} KB".format(free // 1024, (free + alloc) // 1024))
    try:
        sv = uos.statvfs('/')
        ftot = sv[0] * sv[2]
        ffree = sv[0] * sv[3]
        multi("  Free flash  : {} KB / {} KB".format(ffree // 1024, ftot // 1024))
    except OSError:
        multi("  Free flash  : unavailable")
    try:
        import regedit
        ver = regedit.read('Settings.Version') or '?'
        multi("  OS version  : {}".format(ver))
        multi("  Registry    : \033[92mreadable\033[0m")
    except Exception as e:
        multi("  Registry    : \033[91mERROR\033[0m ({})".format(e))
    multi("  Platform    : {}".format(sys.platform))


def _cstat(label, status, detail=''):
    """Print one compatibility-check row. status in OK/WARN/FAIL/NA."""
    col = {'OK': '92', 'WARN': '93', 'FAIL': '91', 'NA': '90'}.get(status, '90')
    pad = status + ' ' * (4 - len(status))
    multi("  \033[{}m{}\033[0m {:<13}{}".format(col, pad, label, detail))


def compat(args=None):
    """Platform compatibility self-test — PROBES the runtime features the OS
    depends on (not just 'can I import it') and reports OK/WARN/FAIL/NA per
    subsystem. Run it on a new board (e.g. ESP32-S3) to verify the multitasking
    OS will actually work there. Every probe is isolated, so it never crashes.
    `compat -q` skips the interactive keypress test."""
    import gc
    info("=== RPCortex compatibility self-test ===")
    multi("  Platform : {}   MicroPython {}".format(sys.platform, sys.version))
    multi("")

    # CPU clock (read)
    try:
        import machine
        f = machine.freq()
        if isinstance(f, tuple):
            f = f[0]
        _cstat('cpu clock', 'OK', '{} MHz'.format(f // 1_000_000))
    except Exception as e:
        _cstat('cpu clock', 'FAIL', str(e))

    # CPU clock (set-to-current — proves machine.freq(set) works, no speed change)
    try:
        import machine
        cur = machine.freq()
        if isinstance(cur, tuple):
            cur = cur[0]
        machine.freq(cur)
        _cstat('freq set', 'OK', 'settable')
    except Exception as e:
        _cstat('freq set', 'WARN', 'not settable ({})'.format(e))

    # hwinfo: temp sensor + platform clock range
    try:
        import hwinfo
        lo, hi = hwinfo.clock_range_mhz()
        _cstat('clock range', 'OK', '{}-{} MHz'.format(lo, hi))
        t = hwinfo.cpu_temp_c()
        if t is None:
            _cstat('temp sensor', 'NA', 'no readable sensor on this platform')
        else:
            _cstat('temp sensor', 'OK', '{:.1f} C'.format(t))
    except Exception as e:
        _cstat('hwinfo', 'FAIL', str(e))

    # RAM
    try:
        gc.collect()
        _cstat('free RAM', 'OK', '{} KB'.format(gc.mem_free() // 1024))
    except Exception as e:
        _cstat('free RAM', 'FAIL', str(e))

    # Filesystem write + read-back
    try:
        tp = '/Pulsar/.compat_test'
        with open(tp, 'w') as f:
            f.write('ok')
        with open(tp) as f:
            good = (f.read() == 'ok')
        try:
            uos.remove(tp)
        except Exception:
            pass
        _cstat('fs write', 'OK' if good else 'WARN',
               'write+read ok' if good else 'read-back mismatch')
    except Exception as e:
        _cstat('fs write', 'FAIL', str(e))

    # RTC
    try:
        import machine
        dt = machine.RTC().datetime()
        _cstat('rtc', 'OK', '{}-{:02d}-{:02d}'.format(dt[0], dt[1], dt[2]))
    except Exception as e:
        _cstat('rtc', 'WARN', str(e))

    # WiFi hardware
    try:
        import network
        _cstat('wifi hw', 'OK' if hasattr(network, 'WLAN') else 'NA',
               'network.WLAN present' if hasattr(network, 'WLAN') else 'no WLAN')
    except Exception as e:
        _cstat('wifi hw', 'NA', str(e))

    # TLS (HTTPS / async wget)
    try:
        import ssl
        _cstat('ssl/tls', 'OK', 'ssl')
    except Exception:
        try:
            import ussl
            _cstat('ssl/tls', 'OK', 'ussl')
        except Exception as e:
            _cstat('ssl/tls', 'WARN', 'no ssl — HTTPS unavailable ({})'.format(e))

    # asyncio — the multitasking core. If the async shell is ALREADY running, that
    # proves it works (and a nested asyncio.run() would error), so just report it.
    try:
        import asyncio
        lp = sys.modules.get('Core.launchpad')
        if lp is not None and getattr(lp, '_async_active', False):
            _cstat('asyncio', 'OK', 'multitasking shell active (running on it now)')
        else:
            async def _tick():
                await asyncio.sleep_ms(0)
                return True
            _cstat('asyncio', 'OK' if asyncio.run(_tick()) else 'WARN', 'event loop runs')
    except Exception as e:
        _cstat('asyncio', 'FAIL', str(e))

    # select on stdin — the async reader's keystroke poll (the spottiest thing
    # across ports; an ESP32-S3 native-USB difference would surface here).
    try:
        import select
        select.select([sys.stdin], [], [], 0)
        _cstat('select stdin', 'OK', 'pollable (non-blocking)')
    except Exception as e:
        _cstat('select stdin', 'FAIL', 'async shell will fall back to sync ({})'.format(e))

    # kbd_intr — Ctrl+C-as-byte so the async loop survives Ctrl+C
    try:
        import micropython
        _cstat('kbd_intr', 'OK' if hasattr(micropython, 'kbd_intr') else 'WARN',
               'present' if hasattr(micropython, 'kbd_intr') else 'absent — Ctrl+C may tear the loop')
    except Exception as e:
        _cstat('kbd_intr', 'WARN', str(e))

    # Interactive: actually deliver a keystroke through select (the real test).
    interactive = not (args and ('-q' in args or 'quick' in args))
    try:
        import RPCortex as _rpc
        if _rpc.is_capturing():
            interactive = False
    except Exception:
        pass
    if interactive:
        multi("")
        info("Press any key within 3s to test stdin delivery (or wait to skip)...")
        try:
            import select
            import utime
            t0 = utime.ticks_ms()
            got = False
            while utime.ticks_diff(utime.ticks_ms(), t0) < 3000:
                if select.select([sys.stdin], [], [], 0)[0]:
                    sys.stdin.read(1)
                    got = True
                    break
                utime.sleep_ms(20)
            _cstat('key delivery', 'OK' if got else 'WARN',
                   'key received via select' if got else 'no key seen (skipped, or stdin not delivering)')
        except Exception as e:
            _cstat('key delivery', 'WARN', str(e))

    multi("")
    ok("Compatibility self-test complete.")
    multi("  \033[90mFAIL on 'select stdin' or 'asyncio' => the async multitasking shell")
    multi("  can't run here; 'asyncmode off' falls back to the proven sync shell.\033[0m")


def logdump(args=None):
    """Print the current session log (optionally only the last n lines)."""
    n = None
    if args and args.strip():
        try:
            n = int(args.strip())
        except ValueError:
            warn("Usage: logdump [n]")
            return
    try:
        with open(_LOG, 'r') as f:
            lines = f.readlines()
    except OSError as e:
        error("Cannot read log '{}': {}".format(_LOG, e))
        return
    if n is not None:
        lines = lines[-n:]
    for line in lines:
        multi(line.rstrip('\n'))
    ok("{} line(s) from {}.".format(len(lines), _LOG))


def regreset(args=None):
    """Delete registry.cfg so POST rebuilds it from template on next boot.

    User accounts (user.cfg) and saved WiFi (networks.cfg) are NOT touched.
    """
    warn("This deletes the registry. POST rebuilds defaults on next boot.")
    warn("User accounts and saved WiFi are preserved.")
    if inpt("Type CONFIRM to reset the registry").strip() != 'CONFIRM':
        info("Cancelled.")
        return
    try:
        uos.remove(_REGISTRY)
        ok("Registry deleted. Reboot to rebuild defaults: reboot")
    except OSError as e:
        error("Could not delete registry: {}".format(e))


def _find_pkg_dir(name, suffix=''):
    base = '/Packages'
    try:
        for entry in uos.listdir(base):
            if entry.lower() == (name + suffix).lower():
                return base + '/' + entry
    except OSError:
        pass
    return None


def _pkg_cmd_names(pkg_dir):
    """Read the command names a package registers (from pkg.cmd in package.cfg)."""
    names = []
    try:
        with open(pkg_dir + '/package.cfg', 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('pkg.cmd') and ':' in line:
                    val = line.split(':', 1)[1]
                    for entry in val.split(';'):
                        entry = entry.strip()
                        if entry:
                            names.append(entry.split(':', 1)[0].strip())
    except OSError:
        pass
    return names


def pkgdisable(args=None):
    """Disable a package by renaming its directory to <name>.disabled."""
    if not args or not args.strip():
        warn("Usage: pkgdisable <name>")
        return
    name = args.strip()
    target = _find_pkg_dir(name)
    if target is None:
        error("Package '{}' not found in /Packages.".format(name))
        return
    cmds = _pkg_cmd_names(target)   # read before the dir is renamed away
    try:
        uos.rename(target, target + '.disabled')
    except OSError as e:
        error("Could not disable '{}': {}".format(name, e))
        return
    # Drop it from the LIVE command table + cache so it stops working now,
    # not just after a reboot.
    live = globals().get('_commands')
    cache = globals().get('_cmd_cache')
    if live is not None:
        for c in cmds:
            if c in live:
                del live[c]
    if cache is not None:
        cache.clear()
    ok("Disabled '{}'. Its command(s) stop working immediately.".format(name))
    info("Re-enable with: pkgenable {}".format(name))


def pkgenable(args=None):
    """Re-enable a package previously disabled with pkgdisable."""
    if not args or not args.strip():
        warn("Usage: pkgenable <name>")
        return
    name = args.strip()
    target = _find_pkg_dir(name, '.disabled')
    if target is None:
        error("No disabled package '{}' found.".format(name))
        return
    restored = target[:-len('.disabled')]
    try:
        uos.rename(target, restored)
    except OSError as e:
        error("Could not re-enable '{}': {}".format(name, e))
        return
    # Re-register the command(s) live: clear cache + reload the command table.
    cache = globals().get('_cmd_cache')
    if cache is not None:
        cache.clear()
    reload = globals().get('_load_commands')
    if reload:
        try:
            reload()
        except Exception:
            pass
    ok("Re-enabled '{}'.".format(name))

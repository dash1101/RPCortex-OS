# Desc: `sd` shell command — mount/unmount/inspect a MicroSD card at /sd.
# File: /Core/Launchpad/sd.py
#
# Thin front-end over Core/sdmgr.py. Pins are config-driven (Hardware.SD_*); a
# card mounts at /sd and the normal fs commands (ls /sd, cp, read ...) work on it.
# Enable boot auto-mount with: reg set Features.SD_Support true
# MicroPython-safe: no f-strings, positional split, .format() only.


def sd(args=None):
    from RPCortex import info, ok, warn, error, multi
    import sdmgr
    parts = (args or '').strip().split(None, 1)
    cmd = parts[0].lower() if parts else 'status'
    rest = parts[1].strip() if len(parts) > 1 else ''

    if cmd in ('help', '-h', '--help', '?'):
        info("sd — MicroSD card mounting", p="sd")
        multi("  sd status          show mount state")
        multi("  sd mount           mount the card at /sd")
        multi("  sd unmount         unmount /sd")
        multi("  sd ls [path]       list /sd (or a subpath)")
        multi("")
        multi("  Pins: Hardware.SD_SCK/_MOSI/_MISO/_CS/_Slot")
        multi("  Auto-mount at boot: reg set Features.SD_Support true")
        return

    if cmd == 'status':
        multi("  " + sdmgr.status())
    elif cmd in ('mount', 'm'):
        okk, msg = sdmgr.mount('force' in rest or '-f' in rest)
        (ok if okk else warn)(msg, p="sd")
    elif cmd in ('unmount', 'umount', 'u'):
        okk, msg = sdmgr.unmount()
        (ok if okk else warn)(msg, p="sd")
    elif cmd in ('ls', 'list'):
        import uos
        path = rest or '/sd'
        if not path.startswith('/sd'):
            path = '/sd/' + path
        if not sdmgr.is_mounted():
            warn("Not mounted. Run: sd mount")
            return
        try:
            entries = uos.listdir(path)
            if not entries:
                multi("  (empty)")
            for e in entries:
                multi("  " + e)
        except Exception as e:
            error("ls failed: {}".format(e))
    else:
        warn("Unknown subcommand '{}'. Try: sd help".format(cmd))

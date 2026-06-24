# Desc: SD card mount manager — mount/unmount a MicroSD at /sd (config-driven).
# File: /Core/sdmgr.py
#
# One small, portable place for SD mounting so the shell (`sd` command), boot
# auto-mount, and apps (e.g. the Nova D1) all share it. Pins come from the
# registry (Hardware.SD_*) so any board wires freely.
#
# ESP32 uses the native machine.SDCard (SPI host). RP2040/RP2350 have NO
# machine.SDCard — they need the pure-Python `sdcard.py` driver (not bundled
# yet), so mount() reports that clearly rather than failing cryptically.
#
# Shared-SPI note: SD shares SCK/MOSI/MISO with the CC1101/SX1276 on the Nova D1
# wiring — a mounted card HOLDS the SPI host, so probing those radios while SD is
# mounted will conflict. Mount on demand (or unmount before RF). MicroPython-safe.

import uos

_MOUNT = '/sd'


def _reg(key, default):
    try:
        import regedit
        v = regedit.read(key)
        return v if v not in (None, '') else default
    except Exception:
        return default


def _pin(key, d):
    try:
        return int(_reg(key, d))
    except (TypeError, ValueError):
        return d


def is_mounted():
    try:
        uos.stat(_MOUNT)
        return True
    except OSError:
        return False


def mount(force=False):
    """Mount the card at /sd. Returns (ok_bool, message)."""
    if is_mounted():
        if not force:
            return True, 'already mounted at /sd'
        unmount()
    import machine
    sck = _pin('Hardware.SD_SCK', 12)
    mosi = _pin('Hardware.SD_MOSI', 11)
    miso = _pin('Hardware.SD_MISO', 13)
    cs = _pin('Hardware.SD_CS', 15)
    slot = _pin('Hardware.SD_Slot', 2)
    sd = None
    if hasattr(machine, 'SDCard'):
        try:
            sd = machine.SDCard(slot=slot, sck=machine.Pin(sck), mosi=machine.Pin(mosi),
                                miso=machine.Pin(miso), cs=machine.Pin(cs))
        except Exception as e:
            return False, 'SDCard init failed: {}'.format(e)
    else:
        try:
            import sdcard          # the pure-Python SPI driver (RP2 etc.)
        except ImportError:
            return False, 'no SDCard on this port (needs sdcard.py driver)'
        try:
            spi = machine.SPI(1, baudrate=1000000, sck=machine.Pin(sck),
                              mosi=machine.Pin(mosi), miso=machine.Pin(miso))
            sd = sdcard.SDCard(spi, machine.Pin(cs, machine.Pin.OUT))
        except Exception as e:
            return False, 'SD init failed: {}'.format(e)
    try:
        uos.mount(sd, _MOUNT)
        return True, 'mounted at /sd'
    except Exception as e:
        return False, 'mount failed: {}'.format(e)


def unmount():
    """Unmount /sd. Returns (ok_bool, message)."""
    try:
        uos.umount(_MOUNT)
        return True, 'unmounted /sd'
    except Exception as e:
        return False, 'umount failed: {}'.format(e)


def status():
    if is_mounted():
        try:
            return 'mounted at /sd ({} entries)'.format(len(uos.listdir(_MOUNT)))
        except Exception:
            return 'mounted at /sd'
    return 'not mounted'

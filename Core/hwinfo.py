# Desc: Hardware/platform abstraction - the one place for bits that differ across
#       RP2040/RP2350 and ESP32-S3 (clock range, die-temperature sensor, freq), so
#       the rest of the OS stays portable. Import this instead of hard-coding ADC(4)
#       or RP2-only clock limits. - RPCortex Vela (v1.0)
# File: /Core/hwinfo.py
# Lang: MicroPython, English

import sys

try:
    import machine
except ImportError:          # CPython / host test
    machine = None


def platform():
    """Short platform id ('rp2', 'esp32', ...) == sys.platform."""
    return sys.platform


def is_rp2():
    return sys.platform == 'rp2'


def is_esp32():
    return sys.platform == 'esp32'


def cpu_temp_c():
    """CPU/die temperature in degrees C (float), or None when this board has no
    readable sensor. RP2040/RP2350 read the internal sensor on ADC channel 4;
    ESP32(-S3) uses the esp32 module (mcu_temperature -> C, else raw_temperature
    -> F). Never raises."""
    p = sys.platform
    try:
        if p == 'rp2':
            v = machine.ADC(4).read_u16() * 3.3 / 65535
            return 27.0 - (v - 0.706) / 0.001721
        if p == 'esp32':
            import esp32
            if hasattr(esp32, 'mcu_temperature'):
                return float(esp32.mcu_temperature())
            if hasattr(esp32, 'raw_temperature'):
                return (esp32.raw_temperature() - 32) / 1.8
    except Exception:
        return None
    return None


def cpu_temp_str(default='n/a', unit='C'):
    """Formatted temperature, e.g. '42.3C' / '108.1F', or `default` if unavailable."""
    t = cpu_temp_c()
    if t is None:
        return default
    if unit == 'F':
        return '{:.1f}F'.format(t * 1.8 + 32.0)
    return '{:.1f}C'.format(t)


def clock_range_mhz():
    """Safe (min_mhz, max_mhz) clock range for this platform:
      RP2040/RP2350 : 80-220  (conservative + on-device proven; <80 freezes flash timing)
      ESP32(-S3)    : 80-240  (240 is the S3 ceiling; valid steps are 80/160/240)
      other         : 80-160  (cautious default)"""
    p = sys.platform
    if p == 'rp2':
        return (80, 220)
    if p == 'esp32':
        return (80, 240)
    return (80, 160)


def dyn_floor_mhz():
    """Lowest clock the dynamic-clock idle state may drop to. RP2 is stable down to
    ~60 MHz (a hard freeze-safe floor below the configured minimum); other ports
    must not go below their platform minimum (e.g. ESP32 steps don't include 60)."""
    if sys.platform == 'rp2':
        return 60
    return clock_range_mhz()[0]


def cpu_freq_mhz():
    """Current CPU frequency in MHz. Handles ports where machine.freq() returns a
    tuple. Returns 0 if unavailable."""
    try:
        f = machine.freq()
        if isinstance(f, tuple):
            f = f[0]
        return f // 1_000_000
    except Exception:
        return 0

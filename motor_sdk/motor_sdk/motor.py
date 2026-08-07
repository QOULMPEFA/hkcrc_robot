"""Single DC motor via H-bridge (2 GPIO pins).

Uses the Orange Pi wiringOP ``gpio`` CLI for reliable GPIO control.
Falls back to mock mode on non-OPi platforms.
"""

import logging
import subprocess

log = logging.getLogger(__name__)

# Physical-pin → wPi-number mapping for Orange Pi Zero 2W (wiringOP 2.46).
# The ``-g`` (BCM GPIO) mode of the gpio CLI does NOT work reliably on
# this board for ``mode`` or ``write`` — pins stay OFF.  wPi numbering
# (no -g flag) works correctly for both mode and write.
# Source: ``gpio readall`` output.
_PHYS_TO_WPI = {
    3:  0,  5:  1,  7:  2,
    8:  3,  10: 4,  11: 5,  12: 6,
    13: 7,  15: 8,  16: 9,  18: 10,
    19: 11, 21: 12, 22: 13, 23: 14,
    24: 15, 26: 16, 27: 17, 28: 18,
    29: 19, 31: 20, 32: 21, 33: 22,
    35: 23, 36: 24, 37: 25, 38: 26,
    40: 27,
}


def _wpi(pin: int) -> int:
    """Convert physical pin number to wPi number."""
    return _PHYS_TO_WPI.get(pin, pin)


def _checked_run(cmd: list[str], use_sudo: bool = False) -> bool:
    """Run a gpio CLI command, return True on success.

    gpio always exits 1 on Orange Pi Zero 2W (board detection bug), but
    the mode/write operations succeed.  We consider it a success as long
    as the binary is found and runs without a crash.
    """
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return True
    except (FileNotFoundError, OSError):
        if use_sudo:
            try:
                subprocess.run(["sudo", "-S"] + cmd,
                             input="orangepi\n", capture_output=True, text=True, timeout=5)
                return True
            except (FileNotFoundError, OSError):
                pass
        return False


def _gpio_available() -> bool:
    """Check whether the gpio CLI tool works (wiringOP installed)."""
    try:
        r = subprocess.run(["gpio", "-v"], capture_output=True, text=True, timeout=3)
        return "gpio version" in (r.stdout or "")
    except (FileNotFoundError, OSError):
        return False


_USE_GPIO_CLI = _gpio_available()


class DCMotor:
    """Control one DC motor through an H-bridge using two GPIO pins.

    Physical pin numbers are converted to GPIO numbers automatically
    for the Orange Pi Zero 2W.  Pins not in the mapping table are used
    as-is (treated as raw GPIO numbers).

    Pin states::

        forward:  IN1=HIGH, IN2=LOW
        backward: IN1=LOW,  IN2=HIGH
        stop:     IN1=LOW,  IN2=LOW
    """

    def __init__(self, in1: int, in2: int, name: str = "motor"):
        self.in1 = in1
        self.in2 = in2
        self.name = name
        self._running = False
        self._direction = "stop"

        self._w1 = _wpi(in1)
        self._w2 = _wpi(in2)
        self._ok = False

        if _USE_GPIO_CLI:
            a = _checked_run(["gpio", "mode", str(self._w1), "out"], use_sudo=True)
            b = _checked_run(["gpio", "mode", str(self._w2), "out"], use_sudo=True)
            if a and b:
                self._ok = True
                self.stop()
                log.info("[Motor] %s init — phys %d/%d → wPi %d/%d", name, in1, in2, self._w1, self._w2)
            else:
                log.error("[Motor] %s — gpio mode failed (need sudo?): phys %d/%d", name, in1, in2)
        else:
            log.warning("[Motor] gpio CLI not found — %s running in mock mode", name)

    def forward(self):
        self._direction = "forward"
        self._running = True
        if self._ok:
            _checked_run(["gpio", "write", str(self._w1), "1"], use_sudo=True)
            _checked_run(["gpio", "write", str(self._w2), "0"], use_sudo=True)
        else:
            log.info("[MockMotor] %s forward", self.name)

    def backward(self):
        self._direction = "backward"
        self._running = True
        if self._ok:
            _checked_run(["gpio", "write", str(self._w1), "0"], use_sudo=True)
            _checked_run(["gpio", "write", str(self._w2), "1"], use_sudo=True)
        else:
            log.info("[MockMotor] %s backward", self.name)

    def stop(self):
        self._direction = "stop"
        self._running = False
        if self._ok:
            _checked_run(["gpio", "write", str(self._w1), "0"], use_sudo=True)
            _checked_run(["gpio", "write", str(self._w2), "0"], use_sudo=True)
        else:
            log.info("[MockMotor] %s stop", self.name)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def direction(self) -> str:
        return self._direction

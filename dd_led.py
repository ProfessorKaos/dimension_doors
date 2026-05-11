import asyncio
import math
import threading
import time

from lib.pca9685 import PCA9685


import board
from smbus2 import SMBus
import RPi.GPIO as GPIO
import neopixel_spi


def ticks_ms():
    return int(time.monotonic() * 1000)


def ticks_diff(now_ms, then_ms):
    return now_ms - then_ms


class _NeoPixelAdapter:
    def __init__(self, pin, count, brightness=255):
        self.count = int(count)
        self._strip = neopixel_spi.NeoPixel_SPI(
            board.SPI(),
            self.count,
            brightness=max(0.0, min(1.0, brightness / 255.0)),
            auto_write=False,
            pixel_order=neopixel_spi.GRB,
        )

    @staticmethod
    def _clamp_rgb(rgb):
        return (
            max(0, min(255, int(rgb[0]))),
            max(0, min(255, int(rgb[1]))),
            max(0, min(255, int(rgb[2]))),
        )

    def __setitem__(self, idx, rgb):
        idx = int(idx)
        if idx < 0 or idx >= self.count:
            return
        self._strip[idx] = self._clamp_rgb(rgb)

    def fill(self, rgb):
        self._strip.fill(self._clamp_rgb(rgb))

    def write(self):
        self._strip.show()


class _WallPCAAdapter:
    def __init__(self, i2c_bus=1, address=0x40, oe_pin=4, pwm_freq=1000):
        self._i2c_bus_num = int(i2c_bus)
        self._address = int(address)
        self._oe_pin = int(oe_pin)
        self._pwm_freq = int(pwm_freq)
        self._bus = None
        self._pca = None
        self._gpio_ready = False
        self._wall_pwm_map = {
            0: (0, 3, 2),
            1: (4, 7, 6),
            2: (8, 10, 11),
            3: (12, 14, 15),
        }
        self._setup_gpio()
        self._setup_i2c()
        self.clear()
        self.allOff()

    def _setup_gpio(self):
        if GPIO is None:
            print("RPi.GPIO is not available, OE control is disabled.")
            return
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._oe_pin, GPIO.OUT, initial=GPIO.HIGH)
            self._gpio_ready = True
        except Exception as exc:
            print(f"Failed to initialize OE GPIO pin {self._oe_pin}: {exc}")

    def _setup_i2c(self):
        if SMBus is None:
            print("smbus2 is not available, PCA9685 wall output is disabled.")
            return
        try:
            self._bus = SMBus(self._i2c_bus_num)
            self._pca = PCA9685(self._bus, address=self._address)
            self._pca.freq(self._pwm_freq)
        except Exception as exc:
            print(f"Failed to initialize PCA9685 on I2C bus {self._i2c_bus_num}: {exc}")
            self._bus = None
            self._pca = None

    @staticmethod
    def _clamp_rgb(rgb):
        return (
            max(0, min(255, int(rgb[0]))),
            max(0, min(255, int(rgb[1]))),
            max(0, min(255, int(rgb[2]))),
        )

    @staticmethod
    def _to_duty(value):
        return int(max(0, min(255, int(value))) * 4095 / 255)

    def _set_oe(self, enabled):
        if not self._gpio_ready:
            return
        try:
            GPIO.output(self._oe_pin, GPIO.LOW if enabled else GPIO.HIGH)
        except Exception as exc:
            print(f"Failed to drive OE pin {self._oe_pin}: {exc}")

    def setLED(self, index, rgb):
        if self._pca is None:
            return
        index = int(index)
        if index not in self._wall_pwm_map:
            return
        ch_r, ch_g, ch_b = self._wall_pwm_map[index]
        r, g, b = self._clamp_rgb(rgb)
        self._pca.duty(ch_r, self._to_duty(r))
        self._pca.duty(ch_g, self._to_duty(g))
        self._pca.duty(ch_b, self._to_duty(b))

    def write(self):
        return

    def clear(self):
        for wall in self._wall_pwm_map:
            self.setLED(wall, (0, 0, 0))

    def allOn(self):
        self._set_oe(True)

    def allOff(self):
        self._set_oe(False)

    def close(self):
        self.allOff()
        if self._bus and hasattr(self._bus, "close"):
            try:
                self._bus.close()
            except Exception:
                pass


def limit_map(x, in_min, in_max, out_min, out_max):
    return int(((max(in_min, min(in_max, x))) - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


class LED:
    def __init__(
        self,
        np_pin=10,
        num_np=500,
        i2c_bus=1,
        pca_address=0x40,
        oe_pin=4,
        pca_pwm_freq=1000,
    ):
        self.D = None
        self.walls = _WallPCAAdapter(
            i2c_bus=i2c_bus,
            address=pca_address,
            oe_pin=oe_pin,
            pwm_freq=pca_pwm_freq,
        )

        self.num_np = int(num_np)
        self.np_pin = int(np_pin)
        self.np = _NeoPixelAdapter(pin=self.np_pin, count=self.num_np)
        self.led((0, 0, 0))

        self.freq = 10.0
        self.min_freq = 3.0
        self.max_freq = 40.0
        self.bright = 1.0
        self.hue = 0
        self.sat = 1.0
        self.duty = 0.4
        self.duration = 600000
        self.remaining_duration = 0
        self.period = 1.0 / self.freq
        self.on_time = self.period * self.duty
        self.off_time = self.period * (1 - self.duty)

        self.rgb = (255, 0, 0)
        self.white = (255, 255, 255)
        self.off_tup = (0, 0, 0)

        self.led_on = 0

        self.active_mode = None
        self.variant = None
        self.mode = None
        self.xp_on = False

        self.wall = {
            "door": 0,
            "left": 3,
            "right": 2,
            "back": 1,
        }

        self.np_axis = {
            "start": 1,
            "x": 400,
            "y": 600,
            "end": self.num_np,
        }

        self._flick_thread = None
        self._flick_stop = threading.Event()
        self._lock = threading.Lock()
        self._warned_multi_spec = False

    def setWalls(self, walls):
        walls_int = []
        if isinstance(walls, int):
            walls_int.append(walls)
        elif isinstance(walls, str) and walls in self.wall:
            walls_int.append(self.wall[walls])
        else:
            for wall in walls:
                if isinstance(wall, int):
                    walls_int.append(wall)
                elif isinstance(wall, str) and wall in self.wall:
                    walls_int.append(self.wall[wall])
        return tuple(walls_int)

    def _flick_loop(self):
        self.update_flick()
        while not self._flick_stop.is_set():
            self.walls.allOn()
            if self._flick_stop.wait(timeout=self.on_time):
                break
            self.walls.allOff()
            if self._flick_stop.wait(timeout=self.off_time):
                break


    async def flick_transition(self, dur=10, s_freq=5.0, e_freq=15.0):
        start_time = ticks_ms()
        dur_ms = int(dur * 1000)
        end_time = start_time + dur_ms
        while ticks_ms() < end_time:
            elapsed = ticks_diff(ticks_ms(), start_time)
            freq = s_freq + (e_freq - s_freq) * (elapsed / dur_ms)
            self.update_flick(freq=freq)
            await asyncio.sleep(0.1)


    def start_flick(self, freq=None):
        if freq is not None:
            self.update_flick(freq=freq)
        if self._flick_thread and self._flick_thread.is_alive():
            return
        self.walls.allOff()
        self._flick_stop.clear()
        self._flick_thread = threading.Thread(target=self._flick_loop, daemon=True)
        self._flick_thread.start()

    def update_flick(self, freq=None, duty=None):
        with self._lock:
            if freq is not None:
                self.freq = max(0.01, float(freq))
            if duty is not None:
                self.duty = max(0.01, min(0.99, float(duty)))
            self.period = 1.0 / self.freq
            self.on_time = self.period * self.duty
            self.off_time = self.period * (1 - self.duty)

    def stop_flick(self):
        self._flick_stop.set()
        if self._flick_thread:
            self._flick_thread.join(timeout=0.5)
        self._flick_thread = None
        for wall in range(4):
            self.walls.setLED(wall, self.off_tup)
        self.walls.write()
        self.walls.allOff()

    def stop(self):
        print("Stopping All")
        if self.mode:
            self.mode.cancel()
            self.mode = None
            self.active_mode = None
        self.stop_flick()

    def hsv(self, h=0, s=1.0, v=1.0):
        hf = (h % 360) / 360.0
        i = math.floor(hf * 6)
        f = hf * 6 - i
        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)

        r, g, b = [
            (v, t, p),
            (q, v, p),
            (p, v, t),
            (p, q, v),
            (t, p, v),
            (v, p, q),
        ][int(i % 6)]
        r = int(255 * r)
        g = int(255 * g)
        b = int(255 * b)
        return (r, g, b)

    async def pulse_walls(self, walls=None, dur=3, hue=0, sat=1.0):
        walls = self.setWalls(walls) if walls else tuple(range(4))
        rgb = self.hsv(hue, sat)
        start_time = ticks_ms()
        dur_ms = int(dur * 1000)
        end_time = start_time + dur_ms
        self.walls.allOn()

        while ticks_ms() < end_time:
            elapsed = ticks_diff(ticks_ms(), start_time)
            phase = (elapsed % dur_ms) / dur_ms * 2 * math.pi
            v = 0.4 * (1 - math.cos(phase)) / 2
            for wall in walls:
                self.walls.setLED(wall, (int(rgb[0] * v), int(rgb[1] * v), int(rgb[2] * v)))
            self.walls.write()
            await asyncio.sleep(0.05)

    async def keep_pulsing(self, walls=None, length=None, dur=3, hue=1, sat=1.0):
        length = dur if length is None else length
        start = ticks_ms()
        while ticks_diff(ticks_ms(), start) < int(length * 1000):
            await self.pulse_walls(walls, dur, hue, sat)

    async def wheel(self, walls=None, dur=3, hue=0, sat=1.0):
        walls = self.setWalls(walls) if walls else tuple(range(4))
        start_time = ticks_ms()
        dur_ms = int(dur * 1000)
        end_time = start_time + dur_ms
        self.walls.allOn()
        while ticks_ms() < end_time:
            elapsed = ticks_diff(ticks_ms(), start_time)
            hue_frac = (elapsed % dur_ms) / dur_ms
            hue_deg = hue_frac * 360
            rgb = self.hsv(hue_deg, sat)
            for wall in walls:
                self.walls.setLED(wall, rgb)
            self.walls.write()
            await asyncio.sleep(0.05)

    async def pulse_wheel(self, walls=None, length=None, p_dur=3, w_dur=3, start_hue=0, sat=1.0):
        walls = self.setWalls(walls) if walls else tuple(range(4))

        length = p_dur if length is None else length
        p_dur_ms = int(p_dur * 1000)
        w_dur_ms = int(w_dur * 1000)
        start_time = ticks_ms()
        end_time = start_time + int(length * 1000)
        self.walls.allOn()
        while ticks_ms() < end_time:
            elapsed = ticks_diff(ticks_ms(), start_time)
            hue_frac = (elapsed % w_dur_ms) / w_dur_ms
            phase = (elapsed % p_dur_ms) / p_dur_ms * 2 * math.pi
            v = 0.4 * (1 - math.cos(phase)) / 2
            hue_deg = (start_hue + hue_frac * 360) % 360
            rgb_base = self.hsv(hue_deg, sat)
            rgb = (int(rgb_base[0] * v), int(rgb_base[1] * v), int(rgb_base[2] * v))
            for wall in walls:
                self.walls.setLED(wall, rgb)
            self.walls.write()
            await asyncio.sleep(0.05)

    def setColour(self, rgb, walls=None, s=1.0, v=1.0):
        walls = self.setWalls(walls) if walls else tuple(range(4))
        if isinstance(rgb, int):
            rgb = self.hsv(h=rgb, s=s, v=v)
        self.walls.allOn()
        for wall in walls:
            self.walls.setLED(wall, rgb)
        self.walls.write()

    def led(self, rgb, idx=None, s=1.0, v=1.0):
        if isinstance(rgb, int):
            rgb = self.hsv(h=rgb, s=s, v=v)
        if idx is None:
            self.np.fill(rgb)
        elif isinstance(idx, (tuple, list)):
            for i in idx:
                self.np[i] = rgb
        else:
            if isinstance(idx, str):
                idx = self.np_axis.get(idx, 0)
            self.np[idx] = rgb
        self.np.write()

    async def string(self, rgb, start=0, end=None, dur=5, s=1.0, v=1.0):
        if isinstance(start, str):
            start = self.np_axis.get(start, 0)
        if isinstance(end, str):
            end = self.np_axis.get(end, self.num_np)
        end = end or self.num_np
        print(f"End is {end}")
        total = max(1, (end - start))
        interval = int(dur * 1000 / total)
        if isinstance(rgb, int):
            rgb = self.hsv(h=rgb, s=s, v=v)
        for i in range(start, end):
            self.np[i] = rgb
            self.np.write()
            await asyncio.sleep(max(0.001, interval / 1000.0))


if __name__ == "__main__":
    # lights = LED(num_np=100)
    # print("leds initialized")
    # lights.led((0, 0, 0))

    async def main():
        for i in range(100,800,50):
            print(f"Testing with {i} LEDs")
            lights = LED(num_np=i)
            lights.led(1)
            time.sleep(1)
            lights.led((0, 0, 0))

        # await lights.string(1)

    asyncio.run(main())

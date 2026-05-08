import asyncio
import os
import sys
import time
from pathlib import Path

from dd_led import LED
from lib.audio_player import AudioPlayer
from lib.LCD1602 import LCD
from lib.keypad import Keypad


BASE_DIR = Path(__file__).resolve().parent


def ticks_ms():
    return int(time.monotonic() * 1000)

def ticks_us():
    return int(time.monotonic())

def ticks_diff(now_ms, then_ms):
    return now_ms - then_ms


def _get_int_env(name, default):
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _get_int_tuple_env(name, default):
    raw = os.getenv(name)
    if not raw:
        return tuple(default)
    try:
        values = tuple(int(chunk.strip()) for chunk in raw.split(",") if chunk.strip())
        return values if values else tuple(default)
    except ValueError:
        return tuple(default)


def _get_optional_hex_int(name, default=None):
    raw = os.getenv(name)
    if not raw:
        return default
    raw = raw.strip().lower()
    try:
        if raw.startswith("0x"):
            return int(raw, 16)
        return int(raw)
    except ValueError:
        return default


class Link:
    def __init__(self):
        self.lights = LED(
            np_pin=_get_int_env("DD_NEOPIXEL_PIN", 10),
            num_np=_get_int_env("DD_NEOPIXEL_COUNT", 150),
        )
        self.lcd = LCD(
            i2c_bus=_get_int_env("DD_LCD_I2C_BUS", 2),
            address=_get_optional_hex_int("DD_LCD_I2C_ADDR", None),
        )
        self.player = AudioPlayer(
            audio_dir=os.getenv("DD_AUDIO_DIR", str(BASE_DIR / "audio")),
            volume=100,
        )
        self.keypad = Keypad(
            row_pin_nums=_get_int_tuple_env("DD_KEYPAD_ROWS", (5, 6, 13, 19)),
            column_pins_nums=_get_int_tuple_env("DD_KEYPAD_COLS", (26,20,26,12)),
            keys=[
                ['1', '2', '3', 'A'],
                ['4', '5', '6', 'B'],
                ['7', '8', '9', 'C'],
                ['*', '0', '#', 'D']
            ],
        )

        self.apps = (self.lcd, self.lights, self.player, self.keypad)
        for app in self.apps:
            if app:
                app.D = self


D = Link()
lcd, lights, player, keypad = D.lcd, D.lights, D.player, D.keypad


def c_task(coro):
    return asyncio.create_task(coro)


def start_mode(mode):
    if lights.mode:
        lights.mode.cancel()
    lights.xp_on = True
    lights.mode = c_task(mode)
    return lights.mode


def d(text, cl=True, y=0, x=0):
    if lcd:
        if cl:
            lcd.clear()
        return lcd.puts(text, y=y, x=x)


async def default_screen(timer=1):
    if lcd:
        print(f"Displaying default screen in {timer} seconds")
        await asyncio.sleep(timer)
        lcd.clear()
        d("The Dimension Doors", cl=False, y=0, x=0)
        d("Experience", cl=False, y=1, x=5)
        d("9 mins long", cl=False, y=2, x=4)
        d("Press A Then get in", cl=False, y=3, x=0)


async def cl(timer):
    await asyncio.sleep(timer)
    if lcd:
        lcd.clear()


def wc(rgb, idx=None):
    return lights.setColour(rgb, walls=idx)


def np(rgb=None, idx=None):
    rgb = rgb if rgb is not None else (0, 0, 0)
    return lights.led(rgb, idx)


def pulse(walls=None, length=10, dur=3, h=1, sat=1.0):
    return start_mode(lights.keep_pulsing(walls=walls, length=length, dur=dur, hue=h, sat=sat))


_experience_task = None
_key_sequence_task = None


def _ts_to_ms(ts):
    mm, ss, mss = ts.strip().split(":")
    return (int(mm) * 60000) + (int(ss) * 1000) + int(mss)


def _parse_header(line):
    track_raw, duration_raw = line.split(",", 1)
    return int(track_raw.strip()), _ts_to_ms(duration_raw)


def _ms_to_ts(ms):
    mm = ms // 60000
    ss = (ms % 60000) // 1000
    return f"{mm:02d}:{ss:02d}"


async def countdown(duration):
    if lcd:
        start_time = ticks_ms()
        lcd.clear()
        d("Experience Underway", cl=False, y=0, x=0)
        d("Please wait", cl=False, y=1, x=0)
        d("Time left:", cl=False, y=2, x=0)
        while True:
            elapsed = ticks_diff(ticks_ms(), start_time)
            remaining = max(0, duration - elapsed)
            ts = _ms_to_ts(int(remaining))
            d(f"{ts}", cl=False, y=3, x=0)

            if remaining <= 0:
                d("Experience Over", y=0, x=0)
                c_task(default_screen(20))
                break
            await asyncio.sleep(1)


async def _handle_key_sequence_after_timeout(timeout_seconds=1.0):
    await asyncio.sleep(timeout_seconds)
    typed = keypad.typed_keys[:]
    keypad.typed_keys.clear()

    global _experience_task

    if typed == ["C", "A", "C", "A"]:
        print("Restarting controller process")
        d("Controller Restarting", y=0, x=0)
        await asyncio.sleep(2)
        if lcd:
            lcd.clear()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    if typed == ["B", "A", "B", "A"]:
        print("Cancelling Current Experience")
        d("Cancelling Experience", y=0, x=0)
        if _experience_task:
            _experience_task.cancel()
            _experience_task = None
        c_task(cl(2))
        return

    if typed == ["A"]:
        print("Key A was pressed")
        if _experience_task and not _experience_task.done():
            d("Experience active", y=0, x=0)
        else:
            d("Doors open. GET IN!", y=0, x=0)
            play_exp("dimension.csv")
            c_task(cl(2))
    elif typed == ["B"]:
        print("Key B was pressed")
        d("Key B was pressed!", y=0, x=0)
        c_task(default_screen(5))
    elif typed == ["C"]:
        print("Key C was pressed")
        d("Key C was pressed!", y=0, x=0)
        c_task(default_screen(5))
    else:
        print(f"Typed keys: {typed} did not match any special sequence.")


async def wait_for_keypress():
    global _key_sequence_task
    while True:
        try:
            key = await keypad.wait_for_keypress()
            keypad.typed_keys.append(key)
            if _key_sequence_task:
                _key_sequence_task.cancel()
            _key_sequence_task = c_task(_handle_key_sequence_after_timeout(1.0))
            d("You typed: " + "".join(keypad.typed_keys), cl=False, y=3, x=0)
        except Exception as e:
            print("Error in wait_for_keypress:", e)
            await asyncio.sleep(0.2)


def _build_exec_ns():
    return {
        "asyncio": asyncio,
        "time": time,
        "lcd": lcd,
        "lights": lights,
        "player": player,
        "keypad": keypad,
        "c_task": c_task,
        "start_mode": start_mode,
        "default_screen": default_screen,
        "cl": cl,
        "wc": wc,
        "np": np,
        "pulse": pulse,
        "d": d,
    }


async def run_exp(csv_file, start_at=0):
    countdown_task = None
    lights.xp_on = True
    print("Starting experience with CSV:", csv_file)
    try:
        csv_path = Path(csv_file)
        if not csv_path.is_absolute():
            csv_path = BASE_DIR / csv_path

        with open(csv_path, "r", encoding="utf-8") as f:
            print("Opened CSV file:", str(csv_path))
            raw = f.read()

        lines = raw.split("\n")
        header = None
        for raw_line in lines:
            line = raw_line.strip()
            if line and not line.startswith("#"):
                header = line
                break

        if header is None:
            print("Experience CSV has no header:", str(csv_path))
            return

        track_id, duration_ms = _parse_header(header)
        print(f"Parsed header - Track ID: {track_id}, Duration (ms): {duration_ms}")
        await asyncio.sleep(5)

        if isinstance(start_at, str):
            start_at = _ts_to_ms(start_at)
        elif isinstance(start_at, (int, float)):
            start_at = int(start_at)
        print(f"Starting experience at {_ms_to_ts(start_at)}")

        lights.duration = duration_ms
        player.play_track(track_id, start_ms=int(start_at))
        countdown_task = c_task(countdown(lights.duration - int(start_at)))

        exec_ns = _build_exec_ns()

        start_ms = ticks_ms() - int(start_at)
        for raw_line in lines[1:]:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                ts, call = line.split(",", 1)
            except ValueError:
                print("Skipping malformed CSV line:", line)
                continue

            event_ms = _ts_to_ms(ts)
            while True:
                elapsed_ms = ticks_diff(ticks_ms(), start_ms)
                wait_ms = event_ms - elapsed_ms
                if wait_ms <= 5:
                    break
                await asyncio.sleep(max(0.001, min(wait_ms / 1000.0, 0.05)))

            try:
                start = ticks_us()
                exec(call.strip(), exec_ns, exec_ns)
                end = ticks_us()
                print(f"Executed:{ts} , {call.strip()} in {ticks_diff(end, start)} us")
            except Exception as e:
                print("Exec failed:", e, "Command:", call)
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        print("Experience task was cancelled")
    except Exception as e:
        print("Error in run_exp:", e)
    finally:
        if countdown_task:
            countdown_task.cancel()
        if lights.mode:
            lights.mode.cancel()
            lights.mode = None
        lights.stop_flick()
        lights.active_mode = None
        lights.xp_on = False
        player.stop()
        print("Experience ended, cleaning up.")


def play_exp(csv_file, start_at=0):
    global _experience_task
    if _experience_task and not _experience_task.done():
        _experience_task.cancel()
    _experience_task = c_task(run_exp(csv_file, start_at))
    return _experience_task


async def main():
    c_task(wait_for_keypress())

    # if os.getenv("DD_AUTOSTART", "0") == "1":
    #     play_exp(os.getenv("DD_EXPERIENCE_FILE", "dimension.csv"))
    # else:
    #     c_task(default_screen())
    start = _ts_to_ms("01:20:00")
    play_exp(os.getenv("DD_EXPERIENCE_FILE", "dimension.csv"), start_at=start)
    while True:
        await asyncio.sleep(20)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user")
    finally:
        try:
            player.stop()
        except Exception:
            pass
        try:
            keypad.cleanup()
        except Exception:
            pass
        try:
            lcd.clear()
            lcd.close()
        except Exception:
            pass

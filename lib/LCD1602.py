from smbus2 import SMBus

from RPLCD.i2c import CharLCD


class _NullLCD:
    def __init__(self, cols=20, rows=4):
        self.cols = cols
        self.rows = rows
        self.backlight_enabled = True

    def clear(self):
        return None

    def close(self):
        return None

    def write_string(self, text):
        return None

    def create_char(self, location, bitmap):
        return None

    @property
    def cursor_pos(self):
        return (0, 0)

    @cursor_pos.setter
    def cursor_pos(self, value):
        return None


class LCD():
    def __init__(self, scl_pin=23, sda_pin=24, freq=100000, i2c_bus=1, address=None, cols=20, rows=4):
        self.D = None
        self.cols = cols
        self.rows = rows
        self.i2c_bus = i2c_bus
        self.LCD_I2C_ADDR = address or self._detect_address()
        self.lcd = self._create_lcd()

    def _detect_address(self):
        bus = SMBus(self.i2c_bus)
        try:
            for addr in (0x27, 0x3F, 0x20, 0x26):
                try:
                    bus.write_byte(addr, 0x00)
                    return addr
                except OSError:
                    continue
        finally:
            try:
                bus.close()
            except Exception:
                pass

        print("Cannot locate I2C LCD backpack (tried 0x27, 0x3F, 0x20, 0x26); using dummy LCD bus")
        return None

    def _create_lcd(self):
        if self.LCD_I2C_ADDR is None:
            return _NullLCD(cols=self.cols, rows=self.rows)

        try:
            return CharLCD(
                "PCF8574",
                address=self.LCD_I2C_ADDR,
                port=self.i2c_bus,
                cols=self.cols,
                rows=self.rows,
                charmap="A02",
                auto_linebreaks=False,
                backlight_enabled=True,
            )
        except Exception as exc:
            print(f"Failed to initialize RPLCD LCD driver: {exc}")
            return _NullLCD(cols=self.cols, rows=self.rows)

    def _clamp_position(self, x, y):
        if x < 0:
            x = 0
        if y < 0:
            y = 0
        if x >= self.cols:
            x = self.cols - 1
        if y >= self.rows:
            y = self.rows - 1
        return x, y

    def _wrap_text(self, s, x=0):
        max_cols = self.cols
        first_width = max_cols - x
        paragraphs = s.split("\n")
        lines = []
        first_line = True

        for para in paragraphs:
            if para == "":
                lines.append("")
                first_line = False
                continue

            words = para.split()
            cur = ""
            cur_width = first_width if first_line else max_cols

            for w in words:
                if len(w) > cur_width:
                    if cur:
                        lines.append(cur)
                        cur = ""
                    while len(w) > cur_width:
                        lines.append(w[:cur_width])
                        w = w[cur_width:]
                        cur_width = max_cols
                    cur = w
                else:
                    if not cur:
                        cur = w
                    elif len(cur) + 1 + len(w) <= cur_width:
                        cur = cur + " " + w
                    else:
                        lines.append(cur)
                        cur = w
                        cur_width = max_cols

            if cur:
                lines.append(cur)
            first_line = False

        return lines

    def clear(self):
        self.lcd.clear()

    def backlight(self, on):
        self.lcd.backlight_enabled = bool(on)

    def on(self):
        self.backlight(True)

    def off(self):
        self.backlight(False)

    def shl(self):
        return None

    def shr(self):
        return None

    def char(self, ch, x=-1, y=0):
        if isinstance(ch, int):
            ch = chr(ch)
        if x >= 0:
            x, y = self._clamp_position(x, y)
            self.lcd.cursor_pos = (y, x)
        self.lcd.write_string(ch)

    def puts(self, s, y=0, x=0):
        """
        Write string `s` starting at (x,y), wrapping at word boundaries.
        """
        if not s:
            return

        x, y = self._clamp_position(x, y)
        lines = self._wrap_text(s, x=x)

        for idx, line in enumerate(lines):
            row = y + idx
            if row >= self.rows:
                break

            col = x if idx == 0 else 0
            width = self.cols - x if idx == 0 else self.cols
            self.lcd.cursor_pos = (row, col)
            self.lcd.write_string((line[:width]).ljust(width))

    def create_charactor(self, ram_position, char):
        assert len(char) == 8
        self.lcd.create_char(ram_position & 0x7, char)

    def close(self):
        try:
            self.lcd.close()
        except Exception:
            pass
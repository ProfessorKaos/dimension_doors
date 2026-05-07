import time

from smbus2 import SMBus, i2c_msg


class LCD():
    def __init__(self, scl_pin=23, sda_pin=24, freq=100000, i2c_bus=1, address=None):
        self.D = None

        # board definition
        # P0: RS
        # P1: R/W
        # P2: E
        # P3: --
        
        # P4-P7: DB4-DB7

        self.bus = SMBus(i2c_bus)
        self.LCD_I2C_ADDR = address or self._detect_address()
        self.bufs = []  # a list of bytes, created as writing things all in one go with i2c.writeto is more efficient than writing each byte
        self.BK = 0x08
        self.RS = 0x00
        self.E = 0x04

        self.queue(0x30)  # 0011
        self.execute()
        time.sleep(0.005)
        self.queue(0x30)  # 0011
        self.execute()
        time.sleep(0.005)
        self.queue(0x20)  # 0010
        self.execute()
        time.sleep(0.005)
        self.add_command(0x28,run=True)  # 0010   1000
        self.on()
        self.add_command(0x06)  # 0000   0110
        self.add_command(0x01)  # 0000   0001
        self.execute()

    def _detect_address(self):
        for addr in (0x27, 0x3F, 0x20, 0x26):
            try:
                self.bus.write_byte(addr, 0x00)
                return addr
            except OSError:
                continue
        print("Cannot locate I2C LCD backpack (tried 0x27, 0x3F, 0x20, 0x26); using dummy LCD bus")

        def _noop_write_byte(*args, **kwargs):
            return None

        def _noop_i2c_rdwr(*args, **kwargs):
            return None

        def _noop_close(*args, **kwargs):
            return None

        self.bus.write_byte = _noop_write_byte
        self.bus.i2c_rdwr = _noop_i2c_rdwr
        self.bus.close = _noop_close
        return 0x27

    def queue(self, dat):
        '''
        Add data to queue, wait execution
        :param dat: 8-bit data, first 4 bit is D7-D4, Last 4 bit is NC, E, RW, RS
        :return:
        '''
        
        dat = dat & 0xF0
        dat |= self.BK
        dat |= self.RS
        
        self.bufs.append(dat | 0x04) # enable high
        self.bufs.append(dat) # enable low
            
    def execute(self):
        try:
            payload = bytes(self.bufs)
            msg = i2c_msg.write(self.LCD_I2C_ADDR, payload)
            self.bus.i2c_rdwr(msg)
            self.bufs=[]
            time.sleep(0.00005)
        except Exception as e:
            print(e)

    def add_command(self, cmd, run=False):
        self.RS = 0
        # I2C chip only has 8 bit, so only 4 bit can be used for data, thus the data needs to be send in two parts
        self.queue(cmd)
        self.queue(cmd << 4)
        if run:
            self.execute()

    def add_data(self, dat):
        self.RS = 1
        # I2C chip only has 8 bit, so only 4 bit can be used for data, thus the data needs to be send in two parts
        self.queue(dat)
        self.queue(dat << 4)

    def clear(self):
        self.add_command(1,run=True)

    def backlight(self, on):
        if on:
            self.BK = 0x08
        else:
            self.BK = 0
        self.add_command(0,run=True)

    def on(self):
        self.add_command(0x0C,run=True)  # 0000 1100 Turn on screen,  turn cursor off, turn blink off

    def off(self):
        self.add_command(0x08,run=True)  # 0000 1000 Turn off screen, turn cursor off, turn blink off

    def shl(self):
        self.add_command(0x18,run=True)

    def shr(self):
        self.add_command(0x1C,run=True)

    def char(self, ch, x=-1, y=0):
        if x >= 0:
            if y == 0:
                a = 0x80
            if y == 1:
                a = 0xC0
            if y == 2:
                a = 0x80 + 20
            if y == 3:
                a = 0xC0 + 20
            a += x
            self.add_command(a)
        self.add_data(ch)

    def puts(self, s, y=0, x=0):
        """
        Write string `s` starting at (x,y). Wrap by words to subsequent lines;
        if a word exceeds the available width it will be split. Supports '\n'.
        """
        if not s:
            return
        # start_time = time.ticks_ms()
        max_cols = 20
        max_rows = 4

        try:
            if 20 > len(s) > 0:
                self.char(ord(s[0]), x, y)
                for i in range(1, len(s)):
                    self.char(ord(s[i]))

                self.execute()
                # end_time = time.ticks_ms()
                # elapsed = time.ticks_diff(end_time, start_time)
                # print(f"Displayed SHORT STRING in {elapsed} ms")
                return
        except Exception as e:
            print(f"Error displaying string: {e}")

        # clamp starting column
        if x < 0:
            x = 0
        if x >= max_cols:
            x = max_cols - 1
        first_width = max_cols - x

        paragraphs = s.split('\n')
        lines = []
        first_line = True

        for para in paragraphs:
            if para == '':
                lines.append('')
                first_line = False
                continue

            words = para.split()
            cur = ''
            cur_width = first_width if first_line else max_cols

            for w in words:
                if len(w) > cur_width:
                    if cur:
                        lines.append(cur)
                        cur = ''
                    # split long word into chunks
                    while len(w) > cur_width:
                        lines.append(w[:cur_width])
                        w = w[cur_width:]
                        cur_width = max_cols
                    cur = w
                else:
                    if not cur:
                        cur = w
                    elif len(cur) + 1 + len(w) <= cur_width:
                        cur = cur + ' ' + w
                    else:
                        lines.append(cur)
                        cur = w
                        cur_width = max_cols

            if cur:
                lines.append(cur)
            first_line = False

        try:
            for idx, line in enumerate(lines):
                row = y + idx
                if row >= max_rows:
                    break

                if line:
                    self.char(ord(line[0]), x if idx == 0 else 0, row)
                    for ch in line[1:]:
                        self.char(ord(ch))
                else:
                    # empty line: set cursor and leave blank
                    self.char(ord(' '), x if idx == 0 else 0, row)

                # pad remainder of line with spaces to clear previous content
                pad = (max_cols - x if idx == 0 else max_cols) - len(line)
                for _ in range(pad):
                    self.char(ord(' '))
        except Exception as e:
            print(f"Error displaying string: {e}")

        self.execute()
        # end_time = time.ticks_ms()
        # elapsed = time.ticks_diff(end_time, start_time)
        # print(f"Displayed LONG STRING in {elapsed} ms")

    def create_charactor(self, ram_position, char):
        '''

        :param position: int, 0-7
        :param char: 8 bytes
        :return:
        '''

        assert len(char)==8
        ram_position &= 0x7;
        set_CGRAM_address = 0x40
        self.add_command(set_CGRAM_address | (ram_position << 3))
        for i in range(8):
            self.add_data(char[i])
        self.execute()

    def close(self):
        try:
            self.bus.close()
        except Exception:
            pass
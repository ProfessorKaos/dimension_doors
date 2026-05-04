import struct
import time


class PCA9685:
    def __init__(self, i2c, address=0x40):
        self.i2c = i2c
        self.address = address
        self.reset()

    def _sleep_us(self, usec):
        if hasattr(time, "sleep_us"):
            time.sleep_us(usec)
            return
        time.sleep(float(usec) / 1_000_000.0)

    def _write_mem(self, register, data):
        if hasattr(self.i2c, "writeto_mem"):
            self.i2c.writeto_mem(self.address, register, bytes(data))
            return
        if hasattr(self.i2c, "write_i2c_block_data"):
            self.i2c.write_i2c_block_data(self.address, register, list(data))
            return
        if hasattr(self.i2c, "write_byte_data") and len(data) == 1:
            self.i2c.write_byte_data(self.address, register, int(data[0]))
            return
        raise TypeError("Unsupported I2C object for PCA9685 writes")

    def _read_mem(self, register, length):
        if hasattr(self.i2c, "readfrom_mem"):
            return bytes(self.i2c.readfrom_mem(self.address, register, length))
        if hasattr(self.i2c, "read_i2c_block_data"):
            return bytes(self.i2c.read_i2c_block_data(self.address, register, length))
        if hasattr(self.i2c, "read_byte_data") and length == 1:
            return bytes([self.i2c.read_byte_data(self.address, register)])
        raise TypeError("Unsupported I2C object for PCA9685 reads")

    def _write(self, address, value):
        self._write_mem(address, bytearray([value]))

    def _read(self, address):
        return self._read_mem(address, 1)[0]

    def reset(self):
        self._write(0x00, 0x00) # Mode1

    def freq(self, freq=None):
        if freq is None:
            return int(25000000.0 / 4096 / (self._read(0xfe) - 0.5))
        prescale = int(25000000.0 / 4096.0 / freq + 0.5)
        old_mode = self._read(0x00) # Mode 1
        self._write(0x00, (old_mode & 0x7F) | 0x10) # Mode 1, sleep
        self._write(0xfe, prescale) # Prescale
        self._write(0x00, old_mode) # Mode 1
        self._sleep_us(5)
        self._write(0x00, old_mode | 0xa1) # Mode 1, autoincrement on

    def pwm(self, index, on=None, off=None):
        if on is None or off is None:
            data = self._read_mem(0x06 + 4 * index, 4)
            return struct.unpack('<HH', data)
        data = struct.pack('<HH', on, off)
        self._write_mem(0x06 + 4 * index, data)

    def duty(self, index, value=None, invert=False):
        if value is None:
            pwm = self.pwm(index)
            if pwm == (0, 4096):
                value = 0
            elif pwm == (4096, 0):
                value = 4095
            else:
                value = pwm[1]
            if invert:
                value = 4095 - value
            return value
        if not 0 <= value <= 4095:
            raise ValueError("Out of range")
        if invert:
            value = 4095 - value
        if value == 0:
            self.pwm(index, 0, 4096)
        elif value == 4095:
            self.pwm(index, 4096, 0)
        else:
            self.pwm(index, 0, value)

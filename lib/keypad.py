import asyncio
import time

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover - allows local development without GPIO
    GPIO = None

__version__ = '2.0.0'
__author__ = 'Teeraphat Kullanankanjana'


class KeypadException(Exception):
    """Exception class for keypad-related errors."""
    pass


class Keypad:
    DEBOUNCE_MS = 50

    def __init__(self, row_pin_nums, column_pins_nums, keys):
        """
        Initialize the keypad with IRQ-driven row detection.

        Columns are driven HIGH. Rows are pulled LOW by internal pull-downs and
        rise to HIGH when a keypress connects them to a HIGH column, triggering
        a rising-edge IRQ on that row pin.

        Args:
            row_pins (list): List of Pin objects for rows (will be reconfigured as
                             input with pull-down and rising-edge IRQ).
            column_pins (list): List of Pin objects for columns (will be driven HIGH).
            keys (list): 2D list of key labels matching [row][column] layout.

        Raises:
            KeypadException: If pins or keys are not properly defined.
        """
        if not all(isinstance(num, int) for num in row_pin_nums):
            raise KeypadException("Row pin numbers must be integers.")
        if not all(isinstance(num, int) for num in column_pins_nums):
            raise KeypadException("Column pin numbers must be integers.")
        if not isinstance(keys, list) or not all(isinstance(row, list) for row in keys):
            raise KeypadException("Keys must be a 2D list.")
        if len(row_pin_nums) != len(keys) or len(column_pins_nums) != len(keys[0]):
            raise KeypadException("Number of row/column pins does not match the key layout size.")

        self.row_pins = list(row_pin_nums)
        self.column_pins = list(column_pins_nums)
        self.keys = keys
        self._last_read_ms = 0
        self.typed_keys = []

        if GPIO:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            for pin in self.column_pins:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            for pin in self.row_pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        else:
            print("RPi.GPIO not available, keypad input disabled.")

    def _scan_column(self, row_idx):
        """
        Identify the pressed column for a known row by strobing each column HIGH
        while the rest are held LOW.
        """
        if not GPIO:
            return None

        row_pin = self.row_pins[row_idx]

        # Pull all columns LOW to remove the ambient HIGH
        for col in self.column_pins:
            GPIO.output(col, GPIO.LOW)

        result = None
        for col_idx in range(len(self.column_pins)):
            col = self.column_pins[col_idx]
            GPIO.output(col, GPIO.HIGH)
            # Brief busy-wait for signal to settle (~few µs)
            time.sleep(0.0001)
            if GPIO.input(row_pin):
                result = self.keys[row_idx][col_idx]
                GPIO.output(col, GPIO.LOW)
                break
            GPIO.output(col, GPIO.LOW)

        # Restore all columns HIGH so the next keypress can trigger the IRQ
        for col in self.column_pins:
            GPIO.output(col, GPIO.HIGH)

        return result

    def read_keypad(self):
        """
        Return the key that was pressed since the last call, or None.

        This should be polled from the main loop. The column scan happens here
        (deferred from the IRQ) while the key is likely still held.

        Returns:
            str or None: The pressed key label, or None if no event is pending.
        """
        if not GPIO:
            return None

        now_ms = int(time.monotonic() * 1000)
        if (now_ms - self._last_read_ms) < self.DEBOUNCE_MS:
            return None

        for row_idx, row_pin in enumerate(self.row_pins):
            if GPIO.input(row_pin):
                key = self._scan_column(row_idx)
                if key is not None:
                    self._last_read_ms = now_ms
                    return key
        return None

    async def wait_for_keypress(self):
        while True:
            key = self.read_keypad()
            if key is not None:
                while self.read_keypad() is not None:
                    await asyncio.sleep(0.02)
                return key
            await asyncio.sleep(0.01)

    def cleanup(self):
        if not GPIO:
            return
        pins = self.row_pins + self.column_pins
        try:
            GPIO.cleanup(pins)
        except Exception:
            GPIO.cleanup()

import asyncio
import argparse
import sys
import time

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover - allows local development without GPIO
    GPIO = None

__version__ = '2.0.0'
__author__ = 'Teeraphat Kullanankanjana'
DEFAULT_DISCOVERY_PINS = [6, 13, 19, 26, 12, 16, 20, 21]

#These were the pins discovered by the below test app
row_pin_nums     = [6, 12, 19, 13]
column_pins_nums = [20, 26, 21, 16]


class KeypadException(Exception):
    """Exception class for keypad-related errors."""
    pass


class Keypad:
    DEBOUNCE_MS = 500

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
        self._key_latched = False
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

    def _any_row_active(self):
        if not GPIO:
            return False
        return any(GPIO.input(row_pin) for row_pin in self.row_pins)

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

        if not self._any_row_active():
            # Re-arm once every key is fully released.
            self._key_latched = False
            return None

        if self._key_latched:
            return None

        now_ms = int(time.monotonic() * 1000)
        if (now_ms - self._last_read_ms) < self.DEBOUNCE_MS:
            return None

        for row_idx, row_pin in enumerate(self.row_pins):
            if GPIO.input(row_pin):
                key = self._scan_column(row_idx)
                if key is not None:
                    self._last_read_ms = now_ms
                    self._key_latched = True
                    return key
        return None

    async def wait_for_keypress(self):
        while True:
            key = self.read_keypad()
            if key is not None:
                while self._any_row_active():
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


def _parse_pin_list(pins_csv):
    pins = []
    for part in pins_csv.split(','):
        token = part.strip()
        if not token:
            continue
        pins.append(int(token))
    if len(set(pins)) != len(pins):
        raise ValueError("Pins must be unique.")
    return pins


def _prepare_all_inputs(pins):
    for pin in pins:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)


def _detect_pressed_pair(candidate_pins):
    """
    Probe all candidate pins and return one undirected pair (pin_a, pin_b)
    that appears electrically connected by a held keypress.
    """
    hits = {}

    for src in candidate_pins:
        _prepare_all_inputs(candidate_pins)
        GPIO.setup(src, GPIO.OUT, initial=GPIO.HIGH)
        time.sleep(0.0002)

        targets = []
        for dst in candidate_pins:
            if dst == src:
                continue
            if GPIO.input(dst):
                targets.append(dst)

        GPIO.setup(src, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        if len(targets) == 1:
            pair = tuple(sorted((src, targets[0])))
            hits[pair] = hits.get(pair, 0) + 1

    if not hits:
        return None

    # Highest vote wins; usually a valid pair is seen from both directions.
    return max(hits.items(), key=lambda item: item[1])[0]


def _wait_for_new_pair(candidate_pins, seen_pairs):
    while True:
        pair = _detect_pressed_pair(candidate_pins)
        if pair is None:
            time.sleep(0.03)
            continue
        if pair in seen_pairs:
            print(f"  Detected known pair {pair}. Please press a different key.")
            while _detect_pressed_pair(candidate_pins) is not None:
                time.sleep(0.03)
            continue

        while _detect_pressed_pair(candidate_pins) is not None:
            time.sleep(0.03)
        return pair


def _infer_row_col_sets(pairs, candidate_pins, expected_rows, expected_cols):
    adjacency = {pin: set() for pin in candidate_pins}
    for a, b in pairs:
        adjacency[a].add(b)
        adjacency[b].add(a)

    colors = {}
    for pin in candidate_pins:
        if pin not in colors and adjacency[pin]:
            colors[pin] = 0
            stack = [pin]
            while stack:
                node = stack.pop()
                for nei in adjacency[node]:
                    if nei not in colors:
                        colors[nei] = 1 - colors[node]
                        stack.append(nei)
                    elif colors[nei] == colors[node]:
                        raise RuntimeError("Observed pairs are not bipartite. Check wiring and retry.")

    group0 = sorted([pin for pin, color in colors.items() if color == 0])
    group1 = sorted([pin for pin, color in colors.items() if color == 1])

    if len(group0) == expected_rows and len(group1) == expected_cols:
        return group0, group1
    if len(group1) == expected_rows and len(group0) == expected_cols:
        return group1, group0

    raise RuntimeError(
        "Could not match inferred pin groups to expected rows/columns. "
        f"Inferred groups: {group0} and {group1}."
    )


def _pair_to_row_col(pair, row_set, col_set):
    a, b = pair
    if a in row_set and b in col_set:
        return a, b
    if b in row_set and a in col_set:
        return b, a
    return None


def _wait_for_pair_in_sets(candidate_pins, row_set, col_set):
    while True:
        pair = _detect_pressed_pair(candidate_pins)
        if pair is None:
            time.sleep(0.03)
            continue

        mapped = _pair_to_row_col(pair, row_set, col_set)
        while _detect_pressed_pair(candidate_pins) is not None:
            time.sleep(0.03)

        if mapped is None:
            print(f"  Ignored pair {pair}; not between inferred row/column sets.")
            continue
        return mapped


def _run_mapping_cli(args):
    if not GPIO:
        print("RPi.GPIO is not available on this system.")
        return 1

    expected_total = args.rows + args.cols
    if len(args.pins) != expected_total:
        print(
            f"Expected exactly {expected_total} pins for a {args.rows}x{args.cols} keypad, "
            f"but got {len(args.pins)}: {args.pins}"
        )
        return 1

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    _prepare_all_inputs(args.pins)

    print("\\n=== Keypad Pin Discovery ===")
    print(f"Candidate GPIO pins (BCM): {args.pins}")
    print("Press and hold one key at a time, then release before next key.")
    print("Press Ctrl+C anytime to stop.\\n")

    target_samples = args.rows * args.cols
    seen_pairs = set()

    try:
        while len(seen_pairs) < target_samples:
            print(f"[{len(seen_pairs) + 1}/{target_samples}] Press a NEW key...")
            pair = _wait_for_new_pair(args.pins, seen_pairs)
            seen_pairs.add(pair)
            print(f"  Captured pair: {pair}\\n")

        row_pins_set, col_pins_set = _infer_row_col_sets(
            seen_pairs,
            args.pins,
            expected_rows=args.rows,
            expected_cols=args.cols,
        )

        print("Inferred pin groups:")
        print(f"  Row-group candidates: {row_pins_set}")
        print(f"  Col-group candidates: {col_pins_set}\\n")

        print("=== Ordering Calibration ===")
        print("Now we determine order (top->bottom rows, left->right cols).")
        print("Follow prompts using your keypad's physical layout.\\n")

        row_pins = [None] * args.rows
        for r in range(args.rows):
            print(f"Press key at row {r + 1}, column 1 (left-most)...")
            row_pin, _ = _wait_for_pair_in_sets(args.pins, set(row_pins_set), set(col_pins_set))
            row_pins[r] = row_pin
            print(f"  Row {r + 1} => GPIO {row_pin}\\n")

        col_pins = [None] * args.cols
        for c in range(args.cols):
            print(f"Press key at row 1, column {c + 1} (top row)...")
            _, col_pin = _wait_for_pair_in_sets(args.pins, set(row_pins_set), set(col_pins_set))
            col_pins[c] = col_pin
            print(f"  Col {c + 1} => GPIO {col_pin}\\n")

        if len(set(row_pins)) != len(row_pins) or len(set(col_pins)) != len(col_pins):
            print("Calibration captured duplicate row/column pins. Re-run and press exact requested keys.")
            return 1

        print("=== Final Mapping ===")
        print(f"row_pin_nums     = {row_pins}")
        print(f"column_pins_nums = {col_pins}")

        print("\\nUse these values in your keypad setup.")
        return 0

    except KeyboardInterrupt:
        print("\\nStopped by user.")
        return 0
    finally:
        try:
            GPIO.cleanup(args.pins)
        except Exception:
            GPIO.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Interactive GPIO keypad pin mapper (row/column discovery + ordering).'
    )
    parser.add_argument(
        '--pins',
        type=_parse_pin_list,
        default=DEFAULT_DISCOVERY_PINS,
        help='Comma-separated BCM pins connected to keypad (rows+cols). '
             f'Default: {DEFAULT_DISCOVERY_PINS}',
    )
    parser.add_argument('--rows', type=int, default=4, help='Number of keypad rows (default: 4).')
    parser.add_argument('--cols', type=int, default=4, help='Number of keypad columns (default: 4).')
    sys.exit(_run_mapping_cli(parser.parse_args()))

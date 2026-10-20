"""
rs485_modbus_client.py

Weather Station
P4 — Reusable Modbus RTU master over half-duplex RS-485.

BOARD:
    Raspberry Pi 5

HARDWARE:
    SparkFun RS-485 breakout (SP3485), DE and /RE tied to RTS.

    UART : /dev/ttyAMA0, 115200 8N1
    RTS  : GPIO17 (HIGH = transmit, LOW = receive)

PICO:
    bme688_modbus_rs485_pico.py (or any Register Map v1.x slave).

WHY THIS MODULE EXISTS:
    PyModbus' ModbusSerialClient (used in P2/P3 over direct UART) does
    not drive the RS-485 direction pin. This client packages what the
    P4-08 bench validation proved necessary, so every P4 master uses
    the same transport instead of copying it.

    Validated behaviour (P4-08, PRs #3-#6):

    1. "computed" RTS release
       RTS goes LOW after the computed frame time (busy-wait), not
       after uart.flush(). tcdrain() returned in 4-20 ms steps and the
       Pico's reply was lost with the Pi receiver still disabled.
       Measured release: ~150-200 us typical, 5.0 ms worst case under
       load. Pico waits RS485_TURNAROUND_MS = 10 before replying.

    2. Inter-frame silence (INTER_FRAME_GAP_S = 5 ms)
       The Pico keeps its driver enabled ~500 us after its last byte.
       Transmitting immediately collided on the bus. Also satisfies
       the Modbus RTU 1.75 ms inter-frame gap.

    3. Junk-tolerant reception
       The idle bus (no failsafe bias on the breakouts) produces junk
       bytes. The reader scans for Unit ID + function code instead of
       reading a fixed number of bytes blindly.

    4. Request/response matching
       Modbus RTU has no transaction id. Replies whose FC01/FC03/FC04
       byte count or FC05 echo do not match the outstanding request are
       rejected as likely late replies (docs/next_steps.md NS-06).

    5. Bounded retries (3) for transport failures. Modbus exception
       responses are valid answers and are NOT retried.

    6. Response timeout 1.5 s
       The Pico's cooperative loop does not service Modbus while reading
       sensors (up to ~820 ms; docs/next_steps.md NS-05).

API:
    Mirrors the subset of PyModbus used by the P3 long run, so a P3
    master can switch transport with minimal changes:

        client.connect() -> bool
        client.close()

        client.read_coils(address, count, device_id)
        client.read_holding_registers(address, count, device_id)
        client.read_input_registers(address, count, device_id)
        client.write_coil(address, value, device_id)

    Each call returns an object with .isError(). Successful reads expose
    .registers or .bits. A Modbus exception response returns an error
    response (isError() == True). A transport failure that persists
    after all retries raises RS485TransportError.

OBSERVABILITY:
    RS485Stats counts transactions, retries and failure causes. Pass the
    same RS485Stats instance across reconnects to keep totals for the
    whole run.
"""

from __future__ import annotations

import glob
import struct
import time

from dataclasses import asdict, dataclass
from typing import Callable

import gpiod
import serial

from gpiod.line import Direction, Value


# ================================================================
# Defaults (validated on the P4-08 bench)
# ================================================================

DEFAULT_PORT = "/dev/ttyAMA0"
DEFAULT_BAUDRATE = 115200
DEFAULT_RTS_GPIO = 17

DEFAULT_RESPONSE_TIMEOUT_S = 1.5
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY_S = 0.05
DEFAULT_INTER_FRAME_GAP_S = 0.005

# Extra characters kept on the bus after the computed frame time.
TX_MARGIN_CHARS = 2

# Driver enable -> first bit.
TX_ENABLE_SETTLE_S = 0.00005

# Serial read granularity for the junk-tolerant reader.
SERIAL_READ_TIMEOUT_S = 0.02


FC_READ_COILS = 1
FC_READ_HOLDING_REGISTERS = 3
FC_READ_INPUT_REGISTERS = 4
FC_WRITE_SINGLE_COIL = 5

SUPPORTED_FUNCTIONS = (
    FC_READ_COILS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_SINGLE_COIL,
)


# ================================================================
# Errors
#
# None of these subclass TimeoutError on purpose: callers (e.g. the
# P3/P4 long runs) treat TimeoutError as "READ_NOW did not complete",
# which is a different condition from a transport failure.
# ================================================================

class RS485TransportError(Exception):
    """Transport failure. Retried; raised only after all retries."""


class RS485NoResponseError(RS485TransportError):
    """No valid response header before the timeout."""


class RS485IncompleteError(RS485TransportError):
    """Response started but did not complete before the timeout."""


class RS485CRCError(RS485TransportError):
    """Response with an invalid CRC."""


class RS485MismatchError(RS485TransportError):
    """Valid frame that does not correspond to the request."""


# ================================================================
# Responses (PyModbus-like)
# ================================================================

class ModbusResponse:

    def __init__(
        self,
        function_code: int,
        registers: list[int] | None = None,
        bits: list[bool] | None = None,
    ):
        self.function_code = function_code
        self.registers = registers or []
        self.bits = bits or []

    def isError(self) -> bool:  # noqa: N802 (PyModbus naming)
        return False

    def __repr__(self) -> str:
        return (
            f"ModbusResponse(fc={self.function_code}, "
            f"registers={self.registers}, bits={self.bits})"
        )


class ModbusErrorResponse:

    def __init__(
        self,
        function_code: int,
        exception_code: int,
    ):
        self.function_code = function_code
        self.exception_code = exception_code

    def isError(self) -> bool:  # noqa: N802 (PyModbus naming)
        return True

    def __repr__(self) -> str:
        return (
            f"ModbusErrorResponse(fc={self.function_code}, "
            f"exception_code={self.exception_code})"
        )

    __str__ = __repr__


# ================================================================
# Statistics
# ================================================================

@dataclass
class RS485Stats:

    transactions: int = 0
    successful_transactions: int = 0
    failed_transactions: int = 0

    retries: int = 0

    no_response: int = 0
    incomplete: int = 0
    crc_errors: int = 0
    mismatches: int = 0

    exception_responses: int = 0

    junk_bytes: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)

    @staticmethod
    def delta(
        before: dict[str, int],
        after: dict[str, int],
    ) -> dict[str, int]:
        return {
            key: after[key] - before.get(key, 0)
            for key in after
        }


# ================================================================
# Helpers
# ================================================================

def crc16(data: bytes) -> int:

    crc = 0xFFFF

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

    return crc


def find_gpiochip(rts_gpio: int) -> str:

    expected_name = f"GPIO{rts_gpio}"

    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            with gpiod.Chip(path) as chip:
                if chip.get_line_info(rts_gpio).name == expected_name:
                    return path
        except (OSError, ValueError):
            continue

    raise RuntimeError(
        f"Could not locate {expected_name}. Check gpioinfo output."
    )


def _print_retry(message: str) -> None:
    print(message)


# ================================================================
# Client
# ================================================================

class RS485ModbusClient:

    def __init__(
        self,
        *,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUDRATE,
        rts_gpio: int = DEFAULT_RTS_GPIO,
        response_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
        inter_frame_gap_s: float = DEFAULT_INTER_FRAME_GAP_S,
        stats: RS485Stats | None = None,
        on_retry: Callable[[str], None] | None = _print_retry,
    ):
        self.port = port
        self.baudrate = baudrate
        self.rts_gpio = rts_gpio

        self.response_timeout_s = response_timeout_s
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.inter_frame_gap_s = inter_frame_gap_s

        self.stats = stats if stats is not None else RS485Stats()
        self.on_retry = on_retry

        self._char_time_s = 10 / baudrate

        self._uart: serial.Serial | None = None
        self._gpio = None

        # time.monotonic() at the end of the last reception (normal
        # reply, exception reply or failed attempt). None = no traffic.
        self._last_rx_end: float | None = None

    # ------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return (
            self._uart is not None
            and self._uart.is_open
            and self._gpio is not None
        )

    def connect(self) -> bool:
        """
        Open the UART and claim the RTS line (starting in RECEIVE).

        Returns True on success, False on failure (PyModbus-like).
        """

        if self.connected:
            return True

        try:
            chip_path = find_gpiochip(self.rts_gpio)

            self._gpio = gpiod.request_lines(
                chip_path,
                consumer="weather-rs485-master",
                config={
                    self.rts_gpio: gpiod.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=Value.INACTIVE,
                    )
                },
            )

            self._uart = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=SERIAL_READ_TIMEOUT_S,
            )

            self._last_rx_end = None

            return True

        except Exception:
            self.close()
            return False

    def close(self) -> None:

        if self._uart is not None:
            try:
                self._uart.close()
            except Exception:
                pass

            self._uart = None

        if self._gpio is not None:
            try:
                self._gpio.set_value(self.rts_gpio, Value.INACTIVE)
            except Exception:
                pass

            try:
                self._gpio.release()
            except Exception:
                pass

            self._gpio = None

    def __enter__(self):
        if not self.connect():
            raise RS485TransportError(
                f"Could not open {self.port} / GPIO{self.rts_gpio}"
            )
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------
    # Public Modbus API (PyModbus-like subset)
    # ------------------------------------------------------------

    def read_coils(
        self,
        address: int,
        count: int = 1,
        device_id: int = 1,
    ):
        payload = struct.pack(">HH", address, count)
        result = self._execute(device_id, FC_READ_COILS, payload)

        if isinstance(result, ModbusErrorResponse):
            return result

        data = result[3:-2]

        bits = [
            bool((data[i // 8] >> (i % 8)) & 1)
            for i in range(count)
        ]

        return ModbusResponse(FC_READ_COILS, bits=bits)

    def read_holding_registers(
        self,
        address: int,
        count: int = 1,
        device_id: int = 1,
    ):
        return self._read_registers(
            device_id,
            FC_READ_HOLDING_REGISTERS,
            address,
            count,
        )

    def read_input_registers(
        self,
        address: int,
        count: int = 1,
        device_id: int = 1,
    ):
        return self._read_registers(
            device_id,
            FC_READ_INPUT_REGISTERS,
            address,
            count,
        )

    def write_coil(
        self,
        address: int,
        value: bool,
        device_id: int = 1,
    ):
        payload = struct.pack(
            ">HH",
            address,
            0xFF00 if value else 0x0000,
        )

        result = self._execute(device_id, FC_WRITE_SINGLE_COIL, payload)

        if isinstance(result, ModbusErrorResponse):
            return result

        return ModbusResponse(FC_WRITE_SINGLE_COIL, bits=[bool(value)])

    # ------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------

    def _read_registers(
        self,
        device_id: int,
        function: int,
        address: int,
        count: int,
    ):
        payload = struct.pack(">HH", address, count)
        result = self._execute(device_id, function, payload)

        if isinstance(result, ModbusErrorResponse):
            return result

        data = result[3:-2]

        registers = list(struct.unpack(f">{count}H", data))

        return ModbusResponse(function, registers=registers)

    def _execute(
        self,
        device_id: int,
        function: int,
        payload: bytes,
    ):
        """
        One Modbus transaction with bounded retries.

        Returns the raw response bytes, or a ModbusErrorResponse for a
        Modbus exception reply. Raises RS485TransportError after all
        retries fail.

        Safe to retry: FC01/FC03/FC04 are idempotent, and FC05 sets an
        absolute value (READ_NOW=ON while already pending is a no-op on
        the Pico).
        """

        if not self.connected:
            raise RS485TransportError("Client not connected")

        if function not in SUPPORTED_FUNCTIONS:
            raise ValueError(f"Unsupported function code {function}")

        self.stats.transactions += 1

        last_error: RS485TransportError | None = None

        for attempt in range(self.retries + 1):

            if attempt > 0:
                self.stats.retries += 1

                if self.on_retry is not None:
                    self.on_retry(
                        f"    [RS485] retry {attempt}/{self.retries} "
                        f"(FC{function:02d}): {last_error}"
                    )

                time.sleep(self.retry_delay_s)

            try:
                result = self._exchange_once(device_id, function, payload)

            except RS485TransportError as exc:
                last_error = exc
                self._count_failure(exc)
                continue

            if isinstance(result, ModbusErrorResponse):
                self.stats.exception_responses += 1
            else:
                self.stats.successful_transactions += 1

            return result

        self.stats.failed_transactions += 1

        raise RS485TransportError(
            f"FC{function:02d} failed after "
            f"{self.retries + 1} attempts: {last_error}"
        )

    def _count_failure(self, exc: RS485TransportError) -> None:

        if isinstance(exc, RS485NoResponseError):
            self.stats.no_response += 1
        elif isinstance(exc, RS485IncompleteError):
            self.stats.incomplete += 1
        elif isinstance(exc, RS485CRCError):
            self.stats.crc_errors += 1
        elif isinstance(exc, RS485MismatchError):
            self.stats.mismatches += 1

    def _exchange_once(
        self,
        device_id: int,
        function: int,
        payload: bytes,
    ):
        request_data = bytes([device_id, function]) + payload
        request = request_data + struct.pack("<H", crc16(request_data))

        self._wait_inter_frame_gap()

        assert self._uart is not None
        self._uart.reset_input_buffer()

        self._transmit(request)

        try:
            return self._receive(device_id, function, payload)

        finally:
            # Also after an exception reply, timeout or CRC error: the
            # next attempt counts the silence from here.
            self._last_rx_end = time.monotonic()

    def _wait_inter_frame_gap(self) -> None:

        if self._last_rx_end is None:
            return

        remaining = self.inter_frame_gap_s - (
            time.monotonic() - self._last_rx_end
        )

        if remaining > 0:
            time.sleep(remaining)

    def _transmit(self, frame: bytes) -> None:
        """
        "computed" method: release RTS after the frame time counted
        from when write() RETURNS (transmission has already started,
        so the margin is conservative and the last byte is not cut).
        """

        assert self._uart is not None and self._gpio is not None

        frame_time_s = len(frame) * self._char_time_s
        margin_s = TX_MARGIN_CHARS * self._char_time_s

        self._gpio.set_value(self.rts_gpio, Value.ACTIVE)
        time.sleep(TX_ENABLE_SETTLE_S)

        try:
            written = self._uart.write(frame)

            if written != len(frame):
                raise RS485TransportError("Incomplete UART write")

            deadline = time.perf_counter() + frame_time_s + margin_s

            # Busy-wait: more precise than time.sleep() or flush().
            while time.perf_counter() < deadline:
                pass

        finally:
            self._gpio.set_value(self.rts_gpio, Value.INACTIVE)

    def _read_exact(self, count: int, deadline: float) -> bytes:

        assert self._uart is not None

        data = bytearray()

        while len(data) < count:

            if time.monotonic() >= deadline:
                raise RS485IncompleteError(
                    f"Expected {count} bytes, received {len(data)}"
                )

            chunk = self._uart.read(count - len(data))

            if chunk:
                data.extend(chunk)

        return bytes(data)

    def _read_header(
        self,
        device_id: int,
        function: int,
        deadline: float,
    ) -> bytes:
        """
        Scan for Unit ID followed by the expected function code (or its
        exception variant), discarding junk bytes from the idle bus.
        """

        assert self._uart is not None

        valid_functions = (function, function | 0x80)
        discarded = bytearray()
        previous: int | None = None

        while time.monotonic() < deadline:

            chunk = self._uart.read(1)

            if not chunk:
                continue

            current = chunk[0]

            if previous == device_id and current in valid_functions:
                self.stats.junk_bytes += len(discarded)
                return bytes([previous, current])

            if previous is not None:
                discarded.append(previous)

            previous = current

        if previous is not None:
            discarded.append(previous)

        self.stats.junk_bytes += len(discarded)

        raise RS485NoResponseError(
            "No Modbus response header within "
            f"{self.response_timeout_s} s "
            f"(junk: {discarded.hex(' ') or 'none'})"
        )

    def _receive(
        self,
        device_id: int,
        function: int,
        payload: bytes,
    ):
        deadline = time.monotonic() + self.response_timeout_s

        header = self._read_header(device_id, function, deadline)

        # Exception response: unit, fc|0x80, code, crc_lo, crc_hi
        if header[1] == (function | 0x80):

            rest = self._read_exact(3, deadline)
            response = header + rest

            if crc16(response) != 0:
                raise RS485CRCError("CRC error in exception response")

            return ModbusErrorResponse(function, rest[0])

        if function in (
            FC_READ_COILS,
            FC_READ_HOLDING_REGISTERS,
            FC_READ_INPUT_REGISTERS,
        ):
            count = self._read_exact(1, deadline)[0]
            data = self._read_exact(count + 2, deadline)
            response = header + bytes([count]) + data

        else:  # FC05: unit, fc, addr(2), value(2), crc(2)
            response = header + self._read_exact(6, deadline)

        if crc16(response) != 0:
            raise RS485CRCError("Modbus CRC mismatch")

        self._check_matches_request(function, payload, response)

        return response

    @staticmethod
    def _check_matches_request(
        function: int,
        payload: bytes,
        response: bytes,
    ) -> None:
        """
        Reject what Modbus RTU allows us to detect as a late reply to an
        earlier request:

            FC01        byte count == ceil(quantity / 8)
            FC03/FC04   byte count == quantity * 2
            FC05        exact echo of address + value

        LIMIT: a late reply with the same function and quantity but a
        different address is indistinguishable in Modbus RTU. The
        response timeout must cover the slave's real worst case so late
        replies do not happen in the first place (NS-06).
        """

        if function in (
            FC_READ_COILS,
            FC_READ_HOLDING_REGISTERS,
            FC_READ_INPUT_REGISTERS,
        ):
            _, quantity = struct.unpack(">HH", payload)

            expected = (
                (quantity + 7) // 8
                if function == FC_READ_COILS
                else quantity * 2
            )

            if response[2] != expected:
                raise RS485MismatchError(
                    f"Response does not match request: byte count "
                    f"{response[2]}, expected {expected} "
                    "(likely a late reply to an earlier request)"
                )

        elif function == FC_WRITE_SINGLE_COIL:

            if response[2:6] != payload:
                raise RS485MismatchError(
                    "Response does not match request: FC05 echo "
                    f"{response[2:6].hex(' ')}, "
                    f"expected {payload.hex(' ')} "
                    "(likely a late reply to an earlier request)"
                )

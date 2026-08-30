"""
p3_05_validation_test.py

P3-05 — Weather Station
BME688 + DHT11 environmental validation.

BOARD:
    Raspberry Pi 5

PICO:
    No changes required.
    The Pico must be running:
        bme688_modbus_pico.py

Test duration:
    15 minutes

Sampling:
    READ_NOW every 5 seconds

Outputs:
    - Console live diagnostics
    - CSV with every acquisition
    - TXT final validation report
"""

from __future__ import annotations

import csv
import math
import statistics
import time

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusSerialClient


# ================================================================
# Test configuration
# ================================================================

PORT = "/dev/ttyAMA0"

UNIT_ID = 1

BAUDRATE = 115200

EXPECTED_MAP_VERSION = 0x0101


TEST_DURATION_S = 15 * 60

SAMPLE_PERIOD_S = 5.0

READ_NOW_TIMEOUT_S = 10.0


# ================================================================
# Register Map
# ================================================================

COIL_RUN_ENABLE = 100
HREG_SAMPLE_INTERVAL = 101
COIL_READ_NOW = 102

IREG_START = 200
IREG_COUNT = 17


# ================================================================
# Sensor status
# ================================================================

SENSOR_OK = 0
SENSOR_ERR = 1
SENSOR_NO_DATA = 2


# ================================================================
# Device state
# ================================================================

STATE_STOPPED = 0
STATE_RUNNING = 1
STATE_FAULT = 2


SENSOR_STATUS_NAMES = {
    SENSOR_OK: "OK",
    SENSOR_ERR: "ERR_SENSOR",
    SENSOR_NO_DATA: "NO_DATA",
}


DEVICE_STATE_NAMES = {
    STATE_STOPPED: "STOPPED",
    STATE_RUNNING: "RUNNING",
    STATE_FAULT: "FAULT",
}


# ================================================================
# Validation ranges
#
# These are sanity / validation ranges, not precision claims.
# ================================================================

DHT_TEMP_MIN_C = 0.0
DHT_TEMP_MAX_C = 50.0

DHT_HUMIDITY_MIN_PCT = 0.0
DHT_HUMIDITY_MAX_PCT = 100.0


BME_TEMP_MIN_C = -40.0
BME_TEMP_MAX_C = 85.0

BME_HUMIDITY_MIN_PCT = 0.0
BME_HUMIDITY_MAX_PCT = 100.0

BME_PRESSURE_MIN_HPA = 900.0
BME_PRESSURE_MAX_HPA = 1100.0

BME_GAS_MIN_OHM = 1


# ================================================================
# Jump thresholds
#
# These do NOT necessarily mean corrupted data.
# They generate warnings for investigation.
# ================================================================

MAX_TEMP_JUMP_C = 2.0

MAX_HUMIDITY_JUMP_PCT = 8.0

MAX_PRESSURE_JUMP_HPA = 1.0

MAX_GAS_RATIO = 3.0


# ================================================================
# Cross-sensor comparison warning thresholds
# ================================================================

MAX_DHT_BME_TEMP_DIFF_C = 3.0

MAX_DHT_BME_HUMIDITY_DIFF_PCT = 15.0


# ================================================================
# Result files
# ================================================================

SCRIPT_DIR = Path(__file__).resolve().parent

RESULTS_DIR = (
    SCRIPT_DIR
    / "results"
)

RESULTS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


RUN_ID = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)


CSV_PATH = (
    RESULTS_DIR
    / f"p3_05_samples_{RUN_ID}.csv"
)


REPORT_PATH = (
    RESULTS_DIR
    / f"p3_05_report_{RUN_ID}.txt"
)


# ================================================================
# Helpers
# ================================================================

def decode_int16(value: int) -> int:

    value &= 0xFFFF

    if value & 0x8000:
        value -= 0x10000

    return value


def join_u32(
    high: int,
    low: int,
) -> int:

    return (
        ((high & 0xFFFF) << 16)
        | (low & 0xFFFF)
    )


def is_finite(value) -> bool:

    try:
        return math.isfinite(value)

    except TypeError:
        return False


def safe_mean(values):

    if not values:
        return None

    return statistics.mean(values)


def safe_stdev(values):

    if len(values) < 2:
        return 0.0

    return statistics.stdev(values)


def safe_min(values):

    if not values:
        return None

    return min(values)


def safe_max(values):

    if not values:
        return None

    return max(values)


def fmt(value, digits=2):

    if value is None:
        return "N/A"

    return f"{value:.{digits}f}"


def bool_text(value: bool) -> str:

    return "YES" if value else "NO"


# ================================================================
# Modbus helpers
# ================================================================

def ensure_ok(
    response,
    operation: str,
):

    if response is None:

        raise RuntimeError(
            f"{operation}: no response"
        )

    if response.isError():

        raise RuntimeError(
            f"{operation}: {response}"
        )

    return response


# ================================================================
# Decoded environmental snapshot
# ================================================================

@dataclass
class Snapshot:

    timestamp_utc: str

    elapsed_s: float

    trigger_latency_ms: float

    sample_counter: int

    uptime_s: int

    device_state: int

    map_version: int

    dht_temperature_c: float

    dht_humidity_pct: float

    dht_status: int

    bme_temperature_c: float

    bme_humidity_pct: float

    bme_pressure_hpa: float

    bme_gas_ohm: int

    bme_status: int

    gas_valid: bool

    heater_stable: bool

    bme_flags: int

    raw_registers: list[int]

    warnings: list[str] = field(
        default_factory=list
    )


# ================================================================
# Statistics collector
# ================================================================

@dataclass
class TestStats:

    attempts: int = 0

    successful_samples: int = 0

    failed_samples: int = 0

    transport_errors: int = 0

    read_now_timeouts: int = 0

    counter_errors: int = 0

    uptime_errors: int = 0

    map_errors: int = 0

    dht_status_errors: int = 0

    bme_status_errors: int = 0

    gas_invalid_count: int = 0

    heater_unstable_count: int = 0

    range_warnings: int = 0

    jump_warnings: int = 0

    saturation_warnings: int = 0

    comparison_warnings: int = 0

    samples: list[Snapshot] = field(
        default_factory=list
    )


# ================================================================
# Client
# ================================================================

client = ModbusSerialClient(

    port=PORT,

    baudrate=BAUDRATE,

    bytesize=8,

    parity="N",

    stopbits=1,

    timeout=1.0,

    retries=3,
)


# ================================================================
# Connection / state helpers
# ================================================================

def connect():

    if client.connect():
        return

    raise RuntimeError(
        f"Cannot open {PORT}"
    )


def read_run_enable():

    response = client.read_coils(

        address=COIL_RUN_ENABLE,

        count=1,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read run_enable",
    )

    return bool(
        response.bits[0]
    )


def set_run_enable(value: bool):

    response = client.write_coil(

        address=COIL_RUN_ENABLE,

        value=value,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Write run_enable",
    )


def read_sample_interval():

    response = client.read_holding_registers(

        address=HREG_SAMPLE_INTERVAL,

        count=1,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read sample_interval",
    )

    return response.registers[0]


def read_map_version():

    response = client.read_input_registers(

        address=208,

        count=1,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read register map version",
    )

    return response.registers[0]


def read_sample_counter():

    response = client.read_input_registers(

        address=206,

        count=2,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read sample_counter",
    )

    return join_u32(
        response.registers[0],
        response.registers[1],
    )


# ================================================================
# READ_NOW
# ================================================================

def trigger_read_now():

    counter_before = (
        read_sample_counter()
    )


    trigger_start = (
        time.monotonic()
    )


    response = client.write_coil(

        address=COIL_READ_NOW,

        value=True,

        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Trigger READ_NOW",
    )


    deadline = (
        time.monotonic()
        + READ_NOW_TIMEOUT_S
    )


    while (
        time.monotonic()
        < deadline
    ):

        coil_response = (
            client.read_coils(

                address=COIL_READ_NOW,

                count=1,

                device_id=UNIT_ID,
            )
        )

        ensure_ok(
            coil_response,
            "Read READ_NOW",
        )


        pending = bool(
            coil_response.bits[0]
        )


        counter_after = (
            read_sample_counter()
        )


        if (
            not pending
            and counter_after
            > counter_before
        ):

            latency_ms = (
                time.monotonic()
                - trigger_start
            ) * 1000.0


            return (
                counter_before,
                counter_after,
                latency_ms,
            )


        time.sleep(
            0.05
        )


    raise TimeoutError(
        "READ_NOW timed out"
    )


# ================================================================
# Read snapshot
# ================================================================

def read_snapshot(
    elapsed_s,
    trigger_latency_ms,
):

    response = (
        client.read_input_registers(

            address=IREG_START,

            count=IREG_COUNT,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read environmental block 200-216",
    )


    regs = response.registers


    if len(regs) != IREG_COUNT:

        raise RuntimeError(
            "Invalid register block length: "
            f"{len(regs)}"
        )


    # ------------------------------------------------------------
    # DHT11 / core
    # ------------------------------------------------------------

    dht_temperature_c = (
        decode_int16(
            regs[0]
        )
        / 100.0
    )


    dht_humidity_pct = (
        regs[1]
        / 100.0
    )


    dht_status = regs[2]


    device_state = regs[3]


    uptime_s = join_u32(
        regs[4],
        regs[5],
    )


    sample_counter = join_u32(
        regs[6],
        regs[7],
    )


    map_version = regs[8]


    # ------------------------------------------------------------
    # BME688
    # ------------------------------------------------------------

    bme_temperature_c = (
        decode_int16(
            regs[9]
        )
        / 100.0
    )


    bme_humidity_pct = (
        regs[10]
        / 100.0
    )


    pressure_pa = join_u32(
        regs[11],
        regs[12],
    )


    bme_pressure_hpa = (
        pressure_pa
        / 100.0
    )


    bme_gas_ohm = join_u32(
        regs[13],
        regs[14],
    )


    bme_flags = regs[15]


    gas_valid = bool(
        bme_flags
        & 0x0001
    )


    heater_stable = bool(
        bme_flags
        & 0x0002
    )


    bme_status = regs[16]


    return Snapshot(

        timestamp_utc=(
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        elapsed_s=elapsed_s,

        trigger_latency_ms=(
            trigger_latency_ms
        ),

        sample_counter=(
            sample_counter
        ),

        uptime_s=uptime_s,

        device_state=(
            device_state
        ),

        map_version=(
            map_version
        ),

        dht_temperature_c=(
            dht_temperature_c
        ),

        dht_humidity_pct=(
            dht_humidity_pct
        ),

        dht_status=(
            dht_status
        ),

        bme_temperature_c=(
            bme_temperature_c
        ),

        bme_humidity_pct=(
            bme_humidity_pct
        ),

        bme_pressure_hpa=(
            bme_pressure_hpa
        ),

        bme_gas_ohm=(
            bme_gas_ohm
        ),

        bme_status=(
            bme_status
        ),

        gas_valid=(
            gas_valid
        ),

        heater_stable=(
            heater_stable
        ),

        bme_flags=(
            bme_flags
        ),

        raw_registers=regs,
    )


# ================================================================
# Validation
# ================================================================

def validate_snapshot(
    snapshot: Snapshot,
    previous: Snapshot | None,
):

    warnings = []


    # ============================================================
    # Map version
    # ============================================================

    if (
        snapshot.map_version
        != EXPECTED_MAP_VERSION
    ):

        warnings.append(
            "MAP_VERSION"
        )


    # ============================================================
    # Sensor status
    # ============================================================

    if (
        snapshot.dht_status
        != SENSOR_OK
    ):

        warnings.append(
            "DHT_STATUS"
        )


    if (
        snapshot.bme_status
        != SENSOR_OK
    ):

        warnings.append(
            "BME_STATUS"
        )


    # ============================================================
    # DHT11 ranges
    # ============================================================

    if not (
        DHT_TEMP_MIN_C
        <= snapshot.dht_temperature_c
        <= DHT_TEMP_MAX_C
    ):

        warnings.append(
            "DHT_TEMP_RANGE"
        )


    if not (
        DHT_HUMIDITY_MIN_PCT
        <= snapshot.dht_humidity_pct
        <= DHT_HUMIDITY_MAX_PCT
    ):

        warnings.append(
            "DHT_RH_RANGE"
        )


    # ============================================================
    # BME688 ranges
    # ============================================================

    if not (
        BME_TEMP_MIN_C
        <= snapshot.bme_temperature_c
        <= BME_TEMP_MAX_C
    ):

        warnings.append(
            "BME_TEMP_RANGE"
        )


    if not (
        BME_HUMIDITY_MIN_PCT
        <= snapshot.bme_humidity_pct
        <= BME_HUMIDITY_MAX_PCT
    ):

        warnings.append(
            "BME_RH_RANGE"
        )


    if not (
        BME_PRESSURE_MIN_HPA
        <= snapshot.bme_pressure_hpa
        <= BME_PRESSURE_MAX_HPA
    ):

        warnings.append(
            "PRESSURE_RANGE"
        )


    if (
        snapshot.bme_gas_ohm
        < BME_GAS_MIN_OHM
    ):

        warnings.append(
            "GAS_RANGE"
        )


    # ============================================================
    # Saturation detection
    # ============================================================

    if snapshot.dht_humidity_pct in (
        0.0,
        100.0,
    ):

        warnings.append(
            "DHT_RH_SATURATION"
        )


    if snapshot.bme_humidity_pct in (
        0.0,
        100.0,
    ):

        warnings.append(
            "BME_RH_SATURATION"
        )


    # ============================================================
    # Gas state
    # ============================================================

    if not snapshot.gas_valid:

        warnings.append(
            "GAS_NOT_VALID"
        )


    if not snapshot.heater_stable:

        warnings.append(
            "HEATER_NOT_STABLE"
        )


    # ============================================================
    # Cross-sensor comparison
    # ============================================================

    temperature_diff = (
        snapshot.dht_temperature_c
        - snapshot.bme_temperature_c
    )


    humidity_diff = (
        snapshot.dht_humidity_pct
        - snapshot.bme_humidity_pct
    )


    if (
        abs(temperature_diff)
        > MAX_DHT_BME_TEMP_DIFF_C
    ):

        warnings.append(
            "DHT_BME_TEMP_DIFF"
        )


    if (
        abs(humidity_diff)
        > MAX_DHT_BME_HUMIDITY_DIFF_PCT
    ):

        warnings.append(
            "DHT_BME_RH_DIFF"
        )


    # ============================================================
    # Inter-sample changes
    # ============================================================

    if previous is not None:

        # --------------------------------------------------------
        # sample_counter
        # --------------------------------------------------------

        expected_counter = (
            previous.sample_counter
            + 1
        ) & 0xFFFFFFFF


        if (
            snapshot.sample_counter
            != expected_counter
        ):

            warnings.append(
                "COUNTER_DISCONTINUITY"
            )


        # --------------------------------------------------------
        # uptime
        # --------------------------------------------------------

        if (
            snapshot.uptime_s
            < previous.uptime_s
        ):

            warnings.append(
                "UPTIME_REGRESSION"
            )


        # --------------------------------------------------------
        # Temperature jump
        # --------------------------------------------------------

        dht_temp_jump = abs(
            snapshot.dht_temperature_c
            - previous.dht_temperature_c
        )


        bme_temp_jump = abs(
            snapshot.bme_temperature_c
            - previous.bme_temperature_c
        )


        if (
            dht_temp_jump
            > MAX_TEMP_JUMP_C
        ):

            warnings.append(
                "DHT_TEMP_JUMP"
            )


        if (
            bme_temp_jump
            > MAX_TEMP_JUMP_C
        ):

            warnings.append(
                "BME_TEMP_JUMP"
            )


        # --------------------------------------------------------
        # Humidity jump
        # --------------------------------------------------------

        dht_rh_jump = abs(
            snapshot.dht_humidity_pct
            - previous.dht_humidity_pct
        )


        bme_rh_jump = abs(
            snapshot.bme_humidity_pct
            - previous.bme_humidity_pct
        )


        if (
            dht_rh_jump
            > MAX_HUMIDITY_JUMP_PCT
        ):

            warnings.append(
                "DHT_RH_JUMP"
            )


        if (
            bme_rh_jump
            > MAX_HUMIDITY_JUMP_PCT
        ):

            warnings.append(
                "BME_RH_JUMP"
            )


        # --------------------------------------------------------
        # Pressure jump
        # --------------------------------------------------------

        pressure_jump = abs(
            snapshot.bme_pressure_hpa
            - previous.bme_pressure_hpa
        )


        if (
            pressure_jump
            > MAX_PRESSURE_JUMP_HPA
        ):

            warnings.append(
                "PRESSURE_JUMP"
            )


        # --------------------------------------------------------
        # Gas ratio jump
        # --------------------------------------------------------

        if (
            previous.bme_gas_ohm > 0
            and snapshot.bme_gas_ohm > 0
        ):

            gas_ratio = max(
                snapshot.bme_gas_ohm,
                previous.bme_gas_ohm,
            ) / min(
                snapshot.bme_gas_ohm,
                previous.bme_gas_ohm,
            )


            if (
                gas_ratio
                > MAX_GAS_RATIO
            ):

                warnings.append(
                    "GAS_LARGE_CHANGE"
                )


    snapshot.warnings = warnings


# ================================================================
# CSV
# ================================================================

CSV_HEADERS = [

    "timestamp_utc",

    "elapsed_s",

    "trigger_latency_ms",

    "sample_counter",

    "uptime_s",

    "device_state",

    "map_version",

    "dht_temperature_c",

    "dht_humidity_pct",

    "dht_status",

    "bme_temperature_c",

    "bme_humidity_pct",

    "bme_pressure_hpa",

    "bme_gas_ohm",

    "bme_status",

    "gas_valid",

    "heater_stable",

    "bme_flags",

    "temp_diff_dht_minus_bme",

    "humidity_diff_dht_minus_bme",

    "warnings",

    "raw_registers",
]


def write_csv_row(
    writer,
    snapshot: Snapshot,
):

    writer.writerow({

        "timestamp_utc":
            snapshot.timestamp_utc,

        "elapsed_s":
            f"{snapshot.elapsed_s:.3f}",

        "trigger_latency_ms":
            f"{snapshot.trigger_latency_ms:.3f}",

        "sample_counter":
            snapshot.sample_counter,

        "uptime_s":
            snapshot.uptime_s,

        "device_state":
            DEVICE_STATE_NAMES.get(
                snapshot.device_state,
                str(snapshot.device_state),
            ),

        "map_version":
            f"0x{snapshot.map_version:04X}",

        "dht_temperature_c":
            snapshot.dht_temperature_c,

        "dht_humidity_pct":
            snapshot.dht_humidity_pct,

        "dht_status":
            SENSOR_STATUS_NAMES.get(
                snapshot.dht_status,
                str(snapshot.dht_status),
            ),

        "bme_temperature_c":
            snapshot.bme_temperature_c,

        "bme_humidity_pct":
            snapshot.bme_humidity_pct,

        "bme_pressure_hpa":
            snapshot.bme_pressure_hpa,

        "bme_gas_ohm":
            snapshot.bme_gas_ohm,

        "bme_status":
            SENSOR_STATUS_NAMES.get(
                snapshot.bme_status,
                str(snapshot.bme_status),
            ),

        "gas_valid":
            snapshot.gas_valid,

        "heater_stable":
            snapshot.heater_stable,

        "bme_flags":
            f"0x{snapshot.bme_flags:04X}",

        "temp_diff_dht_minus_bme":
            (
                snapshot.dht_temperature_c
                - snapshot.bme_temperature_c
            ),

        "humidity_diff_dht_minus_bme":
            (
                snapshot.dht_humidity_pct
                - snapshot.bme_humidity_pct
            ),

        "warnings":
            "|".join(
                snapshot.warnings
            ),

        "raw_registers":
            " ".join(
                str(x)
                for x
                in snapshot.raw_registers
            ),
    })


# ================================================================
# Live output
# ================================================================

def print_sample(
    index: int,
    snapshot: Snapshot,
):

    temp_diff = (
        snapshot.dht_temperature_c
        - snapshot.bme_temperature_c
    )


    rh_diff = (
        snapshot.dht_humidity_pct
        - snapshot.bme_humidity_pct
    )


    warning_text = (

        "OK"

        if not snapshot.warnings

        else "WARN: "
        + ", ".join(
            snapshot.warnings
        )
    )


    print(
        f"[{index:03d}] "
        f"t={snapshot.elapsed_s:7.1f}s "
        f"ctr={snapshot.sample_counter:<5} "
        f"acq={snapshot.trigger_latency_ms:6.1f}ms"
    )


    print(
        "      "
        f"DHT11  "
        f"T={snapshot.dht_temperature_c:6.2f} C  "
        f"RH={snapshot.dht_humidity_pct:6.2f} %  "
        f"status="
        f"{SENSOR_STATUS_NAMES.get(snapshot.dht_status)}"
    )


    print(
        "      "
        f"BME688 "
        f"T={snapshot.bme_temperature_c:6.2f} C  "
        f"RH={snapshot.bme_humidity_pct:6.2f} %  "
        f"P={snapshot.bme_pressure_hpa:8.2f} hPa  "
        f"Gas={snapshot.bme_gas_ohm:8d} ohm"
    )


    print(
        "      "
        f"GasValid={bool_text(snapshot.gas_valid):3}  "
        f"HeaterStable={bool_text(snapshot.heater_stable):3}  "
        f"BME status="
        f"{SENSOR_STATUS_NAMES.get(snapshot.bme_status)}"
    )


    print(
        "      "
        f"DHT-BME: "
        f"dT={temp_diff:+6.2f} C  "
        f"dRH={rh_diff:+7.2f} pp  "
        f"| {warning_text}"
    )


# ================================================================
# Final report
# ================================================================

def build_report(
    stats: TestStats,
    test_start_wall: datetime,
    test_end_wall: datetime,
    original_run_enable: bool,
    sample_interval_s: int,
):

    samples = stats.samples


    dht_temp = [
        x.dht_temperature_c
        for x in samples
    ]


    dht_rh = [
        x.dht_humidity_pct
        for x in samples
    ]


    bme_temp = [
        x.bme_temperature_c
        for x in samples
    ]


    bme_rh = [
        x.bme_humidity_pct
        for x in samples
    ]


    pressure = [
        x.bme_pressure_hpa
        for x in samples
    ]


    gas = [
        x.bme_gas_ohm
        for x in samples
    ]


    latencies = [
        x.trigger_latency_ms
        for x in samples
    ]


    temp_diff = [

        (
            x.dht_temperature_c
            - x.bme_temperature_c
        )

        for x in samples
    ]


    rh_diff = [

        (
            x.dht_humidity_pct
            - x.bme_humidity_pct
        )

        for x in samples
    ]


    temp_abs_error = [
        abs(x)
        for x in temp_diff
    ]


    rh_abs_error = [
        abs(x)
        for x in rh_diff
    ]


    def max_adjacent_delta(values):

        if len(values) < 2:
            return 0.0

        return max(
            abs(
                values[i]
                - values[i - 1]
            )

            for i in range(
                1,
                len(values),
            )
        )


    gas_ratios = []


    for i in range(
        1,
        len(gas),
    ):

        a = gas[i - 1]
        b = gas[i]

        if a > 0 and b > 0:

            gas_ratios.append(

                max(a, b)
                / min(a, b)
            )


    warning_counts = {}


    for sample in samples:

        for warning in sample.warnings:

            warning_counts[warning] = (
                warning_counts.get(
                    warning,
                    0,
                )
                + 1
            )


    success_rate = (

        (
            stats.successful_samples
            / stats.attempts
            * 100.0
        )

        if stats.attempts

        else 0.0
    )


    gas_valid_count = sum(
        1
        for x in samples
        if x.gas_valid
    )


    heater_stable_count = sum(
        1
        for x in samples
        if x.heater_stable
    )


    gas_valid_pct = (

        gas_valid_count
        / len(samples)
        * 100.0

        if samples

        else 0.0
    )


    heater_stable_pct = (

        heater_stable_count
        / len(samples)
        * 100.0

        if samples

        else 0.0
    )


    # ============================================================
    # Hard integrity checks
    # ============================================================

    hard_fail_reasons = []


    if stats.map_errors:

        hard_fail_reasons.append(
            "Register Map mismatch"
        )


    if stats.counter_errors:

        hard_fail_reasons.append(
            "sample_counter discontinuity"
        )


    if stats.uptime_errors:

        hard_fail_reasons.append(
            "uptime regression / reboot"
        )


    if success_rate < 95.0:

        hard_fail_reasons.append(
            "sample success rate < 95%"
        )


    if warning_counts.get(
        "PRESSURE_RANGE",
        0,
    ):

        hard_fail_reasons.append(
            "pressure outside validation range"
        )


    if warning_counts.get(
        "GAS_RANGE",
        0,
    ):

        hard_fail_reasons.append(
            "invalid gas resistance"
        )


    if hard_fail_reasons:

        final_result = "FAIL"

    elif warning_counts:

        final_result = (
            "PASS WITH WARNINGS"
        )

    else:

        final_result = "PASS"


    lines = []


    def add(text=""):

        lines.append(text)


    add(
        "============================================================"
    )

    add(
        " P3-05 ENVIRONMENTAL VALIDATION REPORT"
    )

    add(
        "============================================================"
    )

    add()


    add(
        f"RESULT: {final_result}"
    )

    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " TEST CONFIGURATION"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        f"Start UTC             : "
        f"{test_start_wall.isoformat()}"
    )


    add(
        f"End UTC               : "
        f"{test_end_wall.isoformat()}"
    )


    actual_duration = (
        test_end_wall
        - test_start_wall
    ).total_seconds()


    add(
        f"Actual duration        : "
        f"{actual_duration:.1f} s"
    )


    add(
        f"Requested duration     : "
        f"{TEST_DURATION_S} s"
    )


    add(
        f"Sampling period        : "
        f"{SAMPLE_PERIOD_S:.1f} s"
    )


    add(
        f"UART                   : "
        f"{PORT} @ {BAUDRATE} 8N1"
    )


    add(
        f"Unit ID                : "
        f"{UNIT_ID}"
    )


    add(
        f"Register Map           : "
        f"0x{EXPECTED_MAP_VERSION:04X}"
    )


    add(
        f"Previous run_enable    : "
        f"{original_run_enable}"
    )


    add(
        f"Configured Pico interval: "
        f"{sample_interval_s} s"
    )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " ACQUISITION / TRANSPORT"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        f"Attempts               : "
        f"{stats.attempts}"
    )


    add(
        f"Successful samples     : "
        f"{stats.successful_samples}"
    )


    add(
        f"Failed samples         : "
        f"{stats.failed_samples}"
    )


    add(
        f"Success rate           : "
        f"{success_rate:.3f} %"
    )


    add(
        f"Transport errors       : "
        f"{stats.transport_errors}"
    )


    add(
        f"READ_NOW timeouts      : "
        f"{stats.read_now_timeouts}"
    )


    add(
        f"Counter errors         : "
        f"{stats.counter_errors}"
    )


    add(
        f"Uptime errors          : "
        f"{stats.uptime_errors}"
    )


    add(
        f"Map errors             : "
        f"{stats.map_errors}"
    )


    add(
        f"Trigger latency mean   : "
        f"{fmt(safe_mean(latencies), 2)} ms"
    )


    add(
        f"Trigger latency min    : "
        f"{fmt(safe_min(latencies), 2)} ms"
    )


    add(
        f"Trigger latency max    : "
        f"{fmt(safe_max(latencies), 2)} ms"
    )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " DHT11 STATISTICS"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        f"Temperature min        : "
        f"{fmt(safe_min(dht_temp))} C"
    )


    add(
        f"Temperature mean       : "
        f"{fmt(safe_mean(dht_temp))} C"
    )


    add(
        f"Temperature max        : "
        f"{fmt(safe_max(dht_temp))} C"
    )


    add(
        f"Temperature stdev      : "
        f"{fmt(safe_stdev(dht_temp), 3)} C"
    )


    add(
        f"Max temperature jump   : "
        f"{fmt(max_adjacent_delta(dht_temp))} C"
    )


    add(
        f"Humidity min           : "
        f"{fmt(safe_min(dht_rh))} %"
    )


    add(
        f"Humidity mean          : "
        f"{fmt(safe_mean(dht_rh))} %"
    )


    add(
        f"Humidity max           : "
        f"{fmt(safe_max(dht_rh))} %"
    )


    add(
        f"Humidity stdev         : "
        f"{fmt(safe_stdev(dht_rh), 3)} %"
    )


    add(
        f"Max humidity jump      : "
        f"{fmt(max_adjacent_delta(dht_rh))} pp"
    )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " BME688 STATISTICS"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        f"Temperature min        : "
        f"{fmt(safe_min(bme_temp))} C"
    )


    add(
        f"Temperature mean       : "
        f"{fmt(safe_mean(bme_temp))} C"
    )


    add(
        f"Temperature max        : "
        f"{fmt(safe_max(bme_temp))} C"
    )


    add(
        f"Temperature stdev      : "
        f"{fmt(safe_stdev(bme_temp), 3)} C"
    )


    add(
        f"Max temperature jump   : "
        f"{fmt(max_adjacent_delta(bme_temp))} C"
    )


    add(
        f"Humidity min           : "
        f"{fmt(safe_min(bme_rh))} %"
    )


    add(
        f"Humidity mean          : "
        f"{fmt(safe_mean(bme_rh))} %"
    )


    add(
        f"Humidity max           : "
        f"{fmt(safe_max(bme_rh))} %"
    )


    add(
        f"Humidity stdev         : "
        f"{fmt(safe_stdev(bme_rh), 3)} %"
    )


    add(
        f"Max humidity jump      : "
        f"{fmt(max_adjacent_delta(bme_rh))} pp"
    )


    add(
        f"Pressure min           : "
        f"{fmt(safe_min(pressure))} hPa"
    )


    add(
        f"Pressure mean          : "
        f"{fmt(safe_mean(pressure))} hPa"
    )


    add(
        f"Pressure max           : "
        f"{fmt(safe_max(pressure))} hPa"
    )


    add(
        f"Pressure stdev         : "
        f"{fmt(safe_stdev(pressure), 4)} hPa"
    )


    add(
        f"Max pressure jump      : "
        f"{fmt(max_adjacent_delta(pressure), 3)} hPa"
    )


    add(
        f"Gas resistance min     : "
        f"{safe_min(gas)} ohm"
    )


    add(
        f"Gas resistance mean    : "
        f"{fmt(safe_mean(gas), 1)} ohm"
    )


    add(
        f"Gas resistance max     : "
        f"{safe_max(gas)} ohm"
    )


    add(
        f"Gas resistance stdev   : "
        f"{fmt(safe_stdev(gas), 1)} ohm"
    )


    add(
        f"Max gas step ratio     : "
        f"{fmt(safe_max(gas_ratios), 3)}x"
    )


    add(
        f"Gas valid samples      : "
        f"{gas_valid_count}/{len(samples)} "
        f"({gas_valid_pct:.2f} %)"
    )


    add(
        f"Heater stable samples  : "
        f"{heater_stable_count}/{len(samples)} "
        f"({heater_stable_pct:.2f} %)"
    )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " DHT11 vs BME688"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        "Temperature difference defined as:"
    )


    add(
        "    DHT11 - BME688"
    )


    add(
        f"Temperature bias       : "
        f"{fmt(safe_mean(temp_diff), 3)} C"
    )


    add(
        f"Temperature MAE        : "
        f"{fmt(safe_mean(temp_abs_error), 3)} C"
    )


    add(
        f"Temperature max abs diff: "
        f"{fmt(safe_max(temp_abs_error), 3)} C"
    )


    add()


    add(
        "Humidity difference defined as:"
    )


    add(
        "    DHT11 - BME688"
    )


    add(
        f"Humidity bias          : "
        f"{fmt(safe_mean(rh_diff), 3)} pp"
    )


    add(
        f"Humidity MAE           : "
        f"{fmt(safe_mean(rh_abs_error), 3)} pp"
    )


    add(
        f"Humidity max abs diff  : "
        f"{fmt(safe_max(rh_abs_error), 3)} pp"
    )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " WARNING SUMMARY"
    )

    add(
        "------------------------------------------------------------"
    )


    if not warning_counts:

        add(
            "No warnings detected."
        )

    else:

        for key in sorted(
            warning_counts
        ):

            add(
                f"{key:<30}: "
                f"{warning_counts[key]}"
            )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " HARD FAILURE REASONS"
    )

    add(
        "------------------------------------------------------------"
    )


    if not hard_fail_reasons:

        add(
            "None."
        )

    else:

        for reason in hard_fail_reasons:

            add(
                f"- {reason}"
            )


    add()


    add(
        "------------------------------------------------------------"
    )

    add(
        " ARTIFACTS"
    )

    add(
        "------------------------------------------------------------"
    )


    add(
        f"CSV                   : {CSV_PATH}"
    )


    add(
        f"Report                : {REPORT_PATH}"
    )


    add()


    add(
        "============================================================"
    )

    add(
        f" FINAL RESULT: {final_result}"
    )

    add(
        "============================================================"
    )


    return "\n".join(
        lines
    )


# ================================================================
# Main
# ================================================================

def main():

    stats = TestStats()


    print()
    print(
        "============================================================"
    )

    print(
        " P3-05 ENVIRONMENTAL VALIDATION"
    )

    print(
        " Raspberry Pi 5"
    )

    print(
        "============================================================"
    )

    print()

    print(
        f"Duration      : {TEST_DURATION_S / 60:.1f} min"
    )

    print(
        f"Sample period : {SAMPLE_PERIOD_S:.1f} s"
    )

    print(
        f"Port          : {PORT}"
    )

    print(
        f"Unit ID       : {UNIT_ID}"
    )

    print(
        f"Expected map  : 0x{EXPECTED_MAP_VERSION:04X}"
    )

    print(
        f"CSV           : {CSV_PATH}"
    )

    print(
        f"Report        : {REPORT_PATH}"
    )

    print()


    print(
        "[SETUP] Connecting..."
    )


    connect()


    original_run_enable = False

    sample_interval_s = 0


    try:

        # ========================================================
        # Validate map
        # ========================================================

        version = (
            read_map_version()
        )


        print(
            f"[SETUP] Register Map: 0x{version:04X}"
        )


        if (
            version
            != EXPECTED_MAP_VERSION
        ):

            raise RuntimeError(
                "Wrong Register Map. "
                f"Expected 0x{EXPECTED_MAP_VERSION:04X}, "
                f"got 0x{version:04X}"
            )


        # ========================================================
        # Record Pico configuration
        # ========================================================

        original_run_enable = (
            read_run_enable()
        )


        sample_interval_s = (
            read_sample_interval()
        )


        print(
            f"[SETUP] run_enable: "
            f"{original_run_enable}"
        )


        print(
            f"[SETUP] sample_interval_s: "
            f"{sample_interval_s}"
        )


        # ========================================================
        # Controlled test mode
        #
        # Avoid periodic samples interfering with exact
        # sample_counter continuity.
        # ========================================================

        if original_run_enable:

            print(
                "[SETUP] Temporarily stopping "
                "periodic sampling..."
            )

            set_run_enable(
                False
            )


        print(
            "[SETUP] Test uses READ_NOW only."
        )

        print()


        # ========================================================
        # CSV open
        # ========================================================

        with CSV_PATH.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as csv_file:

            writer = csv.DictWriter(

                csv_file,

                fieldnames=CSV_HEADERS,
            )


            writer.writeheader()


            test_start_wall = (
                datetime.now(
                    timezone.utc
                )
            )


            test_start = (
                time.monotonic()
            )


            deadline = (
                test_start
                + TEST_DURATION_S
            )


            next_sample_time = (
                test_start
            )


            previous = None

            sample_index = 0


            print(
                "Starting acquisitions..."
            )

            print()


            # ====================================================
            # 15-minute test
            # ====================================================

            while (
                time.monotonic()
                < deadline
            ):

                # ------------------------------------------------
                # Hold cadence
                # ------------------------------------------------

                now = (
                    time.monotonic()
                )


                if (
                    now
                    < next_sample_time
                ):

                    time.sleep(
                        next_sample_time
                        - now
                    )


                if (
                    time.monotonic()
                    >= deadline
                ):

                    break


                stats.attempts += 1

                sample_index += 1


                elapsed_s = (
                    time.monotonic()
                    - test_start
                )


                try:

                    (
                        counter_before,
                        counter_after,
                        latency_ms,
                    ) = trigger_read_now()


                    snapshot = read_snapshot(

                        elapsed_s=elapsed_s,

                        trigger_latency_ms=(
                            latency_ms
                        ),
                    )


                    validate_snapshot(

                        snapshot,

                        previous,
                    )


                    # ============================================
                    # Internal integrity counters
                    # ============================================

                    if (
                        snapshot.map_version
                        != EXPECTED_MAP_VERSION
                    ):

                        stats.map_errors += 1


                    if previous is not None:

                        expected_counter = (
                            previous.sample_counter
                            + 1
                        ) & 0xFFFFFFFF


                        if (
                            snapshot.sample_counter
                            != expected_counter
                        ):

                            stats.counter_errors += 1


                        if (
                            snapshot.uptime_s
                            < previous.uptime_s
                        ):

                            stats.uptime_errors += 1


                    if (
                        snapshot.dht_status
                        != SENSOR_OK
                    ):

                        stats.dht_status_errors += 1


                    if (
                        snapshot.bme_status
                        != SENSOR_OK
                    ):

                        stats.bme_status_errors += 1


                    if not snapshot.gas_valid:

                        stats.gas_invalid_count += 1


                    if not snapshot.heater_stable:

                        stats.heater_unstable_count += 1


                    for warning in snapshot.warnings:

                        if "RANGE" in warning:

                            stats.range_warnings += 1


                        if "JUMP" in warning:

                            stats.jump_warnings += 1


                        if "SATURATION" in warning:

                            stats.saturation_warnings += 1


                        if (
                            "DHT_BME" in warning
                        ):

                            stats.comparison_warnings += 1


                    stats.successful_samples += 1

                    stats.samples.append(
                        snapshot
                    )


                    write_csv_row(
                        writer,
                        snapshot,
                    )


                    csv_file.flush()


                    print_sample(
                        sample_index,
                        snapshot,
                    )


                    previous = snapshot


                except TimeoutError as exc:

                    stats.failed_samples += 1

                    stats.read_now_timeouts += 1


                    print(
                        f"[{sample_index:03d}] "
                        f"READ_NOW TIMEOUT: {exc}"
                    )


                except Exception as exc:

                    stats.failed_samples += 1

                    stats.transport_errors += 1


                    print(
                        f"[{sample_index:03d}] "
                        f"ERROR: {repr(exc)}"
                    )


                    # --------------------------------------------
                    # Attempt controlled reconnect
                    # --------------------------------------------

                    try:

                        client.close()

                    except Exception:

                        pass


                    time.sleep(
                        0.5
                    )


                    try:

                        connect()

                        print(
                            "      Reconnect: OK"
                        )

                    except Exception as reconnect_exc:

                        print(
                            "      Reconnect failed:",
                            repr(reconnect_exc),
                        )


                print()


                # ------------------------------------------------
                # Absolute cadence avoids cumulative drift
                # ------------------------------------------------

                next_sample_time += (
                    SAMPLE_PERIOD_S
                )


            # ====================================================
            # Test complete
            # ====================================================

            test_end_wall = (
                datetime.now(
                    timezone.utc
                )
            )


        # ========================================================
        # Build final report
        # ========================================================

        report = build_report(

            stats=stats,

            test_start_wall=(
                test_start_wall
            ),

            test_end_wall=(
                test_end_wall
            ),

            original_run_enable=(
                original_run_enable
            ),

            sample_interval_s=(
                sample_interval_s
            ),
        )


        REPORT_PATH.write_text(

            report,

            encoding="utf-8",
        )


        print()
        print()
        print(report)


    finally:

        # ========================================================
        # Restore previous run state
        # ========================================================

        try:

            if original_run_enable:

                print()
                print(
                    "[CLEANUP] Restoring "
                    "run_enable=True..."
                )

                set_run_enable(
                    True
                )

        except Exception as exc:

            print(
                "[CLEANUP] Could not restore "
                f"run state: {repr(exc)}"
            )


        client.close()


if __name__ == "__main__":

    main()
"""
p3_06_long_run.py

Weather Station
P3-06 — Long-duration environmental stability run.

BOARD:
    Raspberry Pi 5

PICO:
    No changes required.

    Must be running:
        bme688_modbus_pico.py

The program runs indefinitely.

STOP:
    Ctrl+C

Intended execution:
    tmux session, independent from SSH / VS Code.

Pipeline:

    DHT11 + BME688
          |
          v
       Pico 2
          |
          | Modbus RTU
          v
    Raspberry Pi 5
          |
          +--> validation / warm-up
          |
          +--> local JSONL audit log
          |
          v
      PostgreSQL
          |
          +--> DHT11 row
          +--> BME688 row

Every environmental acquisition produces exactly:

    one DHT11 row
    one BME688 row

within one PostgreSQL transaction.

Register Map:
    v1.1 = 0x0101

Sampling:
    READ_NOW every 5 seconds

Warm-up:
    minimum 10 seconds
    AND
    3 consecutive valid/stable BME688 gas samples.

Warm-up is latched:
    once completed, it never restarts during the same process.

However gas_ready remains a per-sample quality flag:

    gas_ready =
        warmup_complete
        AND BME status OK
        AND gas_valid
        AND heater_stable
"""

from __future__ import annotations

import json
import sys
import time
import traceback

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from pymodbus.client import ModbusSerialClient


# ================================================================
# Local imports
# ================================================================

SCRIPT_DIR = Path(__file__).resolve().parent


if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(SCRIPT_DIR),
    )


import db


# ================================================================
# Configuration
# ================================================================

PORT = "/dev/ttyAMA0"

UNIT_ID = 1

BAUDRATE = 115200


EXPECTED_MAP_VERSION = 0x0101


# ------------------------------------------------
# There is NO total run duration.
#
# Sampling continues until Ctrl+C.
# ------------------------------------------------

SAMPLE_PERIOD_S = 5.0

READ_NOW_TIMEOUT_S = 10.0


# ------------------------------------------------
# Reconnection
# ------------------------------------------------

RECONNECT_INITIAL_S = 1.0

RECONNECT_MAX_S = 30.0


# ------------------------------------------------
# PostgreSQL insert retries
#
# A lost connection (OperationalError) is retried forever via
# recover_db() — that is a transport problem, not a data problem.
#
# Any other insert failure (bad data, constraint violation, ...)
# is retried a bounded number of times instead. Retrying that kind
# of failure forever would stall the whole acquisition loop on one
# sample, since persist_sample() blocks main() until it returns.
# ------------------------------------------------

DB_INSERT_MAX_RETRIES = 3

DB_INSERT_RETRY_DELAY_S = 2.0


# ------------------------------------------------
# Console health summary
#
# This does NOT stop or control the run.
# It only prints accumulated statistics.
# ------------------------------------------------

HEALTH_SUMMARY_EVERY_SAMPLES = 60


# ================================================================
# BME688 warm-up
# ================================================================

WARMUP_MIN_S = 10.0

WARMUP_STABLE_SAMPLES = 3


# ================================================================
# Register Map
# ================================================================

COIL_RUN_ENABLE = 100

HREG_SAMPLE_INTERVAL = 101

COIL_READ_NOW = 102


IREG_START = 200

IREG_COUNT = 17


# ================================================================
# Status enums
# ================================================================

SENSOR_OK = 0

SENSOR_ERR = 1

SENSOR_NO_DATA = 2


STATE_STOPPED = 0

STATE_RUNNING = 1

STATE_FAULT = 2


SENSOR_STATUS_NAMES = {

    SENSOR_OK:
        "OK",

    SENSOR_ERR:
        "ERR_SENSOR",

    SENSOR_NO_DATA:
        "NO_DATA",
}


DEVICE_STATE_NAMES = {

    STATE_STOPPED:
        "STOPPED",

    STATE_RUNNING:
        "RUNNING",

    STATE_FAULT:
        "FAULT",
}


# ================================================================
# Result / audit files
# ================================================================

RESULTS_DIR = (

    SCRIPT_DIR
    / "results"
)


RESULTS_DIR.mkdir(

    parents=True,

    exist_ok=True,
)


RUN_TIMESTAMP = (

    datetime.now()
    .strftime(
        "%Y%m%d_%H%M%S"
    )
)


AUDIT_LOG_PATH = (

    RESULTS_DIR
    / f"p3_06_long_run_{RUN_TIMESTAMP}.jsonl"
)


ERROR_LOG_PATH = (

    RESULTS_DIR
    / f"p3_06_long_run_errors_{RUN_TIMESTAMP}.log"
)


# ================================================================
# Helpers
# ================================================================

def utc_now_iso() -> str:

    return (

        datetime.now(
            timezone.utc
        )
        .isoformat()
    )


def decode_int16(
    value: int,
) -> int:

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
# Persistent local audit log
# ================================================================

def append_jsonl(
    payload: dict[str, Any],
) -> None:

    """
    Store every successfully decoded acquisition locally.

    PostgreSQL remains the primary storage.

    This JSONL file is a secondary audit trail useful if
    PostgreSQL becomes temporarily unavailable.
    """

    with AUDIT_LOG_PATH.open(

        "a",

        encoding="utf-8",

    ) as file:

        file.write(

            json.dumps(
                payload,
                ensure_ascii=False,
            )

            + "\n"
        )


def log_error(
    message: str,
    exc: Exception | None = None,
) -> None:

    timestamp = utc_now_iso()


    lines = [

        f"[{timestamp}] {message}",
    ]


    if exc is not None:

        lines.append(
            repr(exc)
        )

        lines.append(
            traceback.format_exc()
        )


    text = "\n".join(
        lines
    )


    print(
        "[ERROR]",
        message,
    )


    with ERROR_LOG_PATH.open(

        "a",

        encoding="utf-8",

    ) as file:

        file.write(
            text
        )

        file.write(
            "\n\n"
        )


# ================================================================
# Statistics
# ================================================================

@dataclass
class RunStats:

    acquisition_attempts: int = 0

    successful_acquisitions: int = 0

    failed_acquisitions: int = 0

    db_successful_samples: int = 0

    db_failed_samples: int = 0

    modbus_errors: int = 0

    modbus_reconnects: int = 0

    db_errors: int = 0

    db_reconnects: int = 0

    read_now_timeouts: int = 0

    sequence_errors: int = 0

    uptime_regressions: int = 0

    dht_sensor_errors: int = 0

    bme_sensor_errors: int = 0

    gas_invalid_samples: int = 0

    heater_unstable_samples: int = 0

    gas_ready_samples: int = 0

    first_sequence: int | None = None

    last_sequence: int | None = None

    previous_uptime_ms: int | None = None

    previous_sequence: int | None = None

    latencies_ms: list[float] = field(
        default_factory=list
    )


stats = RunStats()


# ================================================================
# Modbus client
# ================================================================

def make_modbus_client():

    return ModbusSerialClient(

        port=PORT,

        baudrate=BAUDRATE,

        bytesize=8,

        parity="N",

        stopbits=1,

        timeout=1.0,

        retries=3,
    )


modbus_client = (
    make_modbus_client()
)


# ================================================================
# Modbus connection
# ================================================================

def connect_modbus_with_retry():

    global modbus_client


    delay = (
        RECONNECT_INITIAL_S
    )


    while True:

        try:

            try:

                modbus_client.close()

            except Exception:

                pass


            modbus_client = (
                make_modbus_client()
            )


            if modbus_client.connect():

                print(
                    "[MODBUS] Connected:",
                    PORT,
                )

                return


            raise RuntimeError(
                f"Could not open {PORT}"
            )


        except KeyboardInterrupt:

            raise


        except Exception as exc:

            stats.modbus_errors += 1


            log_error(
                "Modbus connection failed",
                exc,
            )


            print(
                f"[MODBUS] Retry in "
                f"{delay:.1f} s..."
            )


            time.sleep(
                delay
            )


            delay = min(

                delay * 2.0,

                RECONNECT_MAX_S,
            )


def recover_modbus():

    stats.modbus_reconnects += 1


    print()
    print(
        "[MODBUS] Attempting recovery..."
    )


    connect_modbus_with_retry()


    version = (
        read_map_version()
    )


    if (
        version
        != EXPECTED_MAP_VERSION
    ):

        raise RuntimeError(
            "Register Map changed after reconnect: "
            f"0x{version:04X}"
        )


    print(
        "[MODBUS] Recovery complete."
    )


# ================================================================
# Modbus operations
# ================================================================

def read_map_version() -> int:

    response = (
        modbus_client
        .read_input_registers(

            address=208,

            count=1,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read Register Map version",
    )


    return int(
        response.registers[0]
    )


def read_run_enable() -> bool:

    response = (
        modbus_client
        .read_coils(

            address=COIL_RUN_ENABLE,

            count=1,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read run_enable",
    )


    return bool(
        response.bits[0]
    )


def set_run_enable(
    value: bool,
) -> None:

    response = (
        modbus_client
        .write_coil(

            address=COIL_RUN_ENABLE,

            value=value,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Write run_enable",
    )


def read_sample_interval() -> int:

    response = (
        modbus_client
        .read_holding_registers(

            address=HREG_SAMPLE_INTERVAL,

            count=1,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read sample_interval",
    )


    return int(
        response.registers[0]
    )


def read_sample_counter() -> int:

    response = (
        modbus_client
        .read_input_registers(

            address=206,

            count=2,

            device_id=UNIT_ID,
        )
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


    trigger_started = (
        time.monotonic()
    )


    response = (
        modbus_client
        .write_coil(

            address=COIL_READ_NOW,

            value=True,

            device_id=UNIT_ID,
        )
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

        response = (
            modbus_client
            .read_coils(

                address=COIL_READ_NOW,

                count=1,

                device_id=UNIT_ID,
            )
        )


        ensure_ok(
            response,
            "Read READ_NOW",
        )


        pending = bool(
            response.bits[0]
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
                - trigger_started

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
        "READ_NOW timeout"
    )


# ================================================================
# Environmental snapshot
# ================================================================

def read_environmental_snapshot():

    response = (
        modbus_client
        .read_input_registers(

            address=IREG_START,

            count=IREG_COUNT,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read environmental block",
    )


    regs = list(
        response.registers
    )


    if (
        len(regs)
        != IREG_COUNT
    ):

        raise RuntimeError(
            "Invalid register count: "
            f"{len(regs)}"
        )


    # ============================================================
    # P2 core / DHT11
    # ============================================================

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


    dht_status_raw = (
        regs[2]
    )


    device_state = (
        regs[3]
    )


    uptime_s = join_u32(

        regs[4],

        regs[5],
    )


    sequence = join_u32(

        regs[6],

        regs[7],
    )


    register_map_version = (
        regs[8]
    )


    # ============================================================
    # BME688
    # ============================================================

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


    pressure_hpa = (

        pressure_pa

        / 100.0
    )


    gas_resistance_ohm = join_u32(

        regs[13],

        regs[14],
    )


    flags = (
        regs[15]
    )


    gas_valid = bool(

        flags
        & 0x0001
    )


    heater_stable = bool(

        flags
        & 0x0002
    )


    bme_status_raw = (
        regs[16]
    )


    return {

        "timestamp_utc":
            utc_now_iso(),

        "sequence":
            sequence,

        "uptime_ms":
            uptime_s * 1000,

        "device_state":
            device_state,

        "register_map_version":
            register_map_version,

        "raw_registers":
            regs,


        "dht11": {

            "temperature_c":
                dht_temperature_c,

            "humidity_pct":
                dht_humidity_pct,

            "status":
                SENSOR_STATUS_NAMES.get(

                    dht_status_raw,

                    f"UNKNOWN_{dht_status_raw}",
                ),

            "status_raw":
                dht_status_raw,
        },


        "bme688": {

            "temperature_c":
                bme_temperature_c,

            "humidity_pct":
                bme_humidity_pct,

            "pressure_hpa":
                pressure_hpa,

            "gas_resistance_ohm":
                gas_resistance_ohm,

            "gas_valid":
                gas_valid,

            "heater_stable":
                heater_stable,

            "status":
                SENSOR_STATUS_NAMES.get(

                    bme_status_raw,

                    f"UNKNOWN_{bme_status_raw}",
                ),

            "status_raw":
                bme_status_raw,

            "flags":
                flags,
        },
    }


# ================================================================
# PostgreSQL connection
# ================================================================

postgres_conn = None


def connect_db_with_retry():

    global postgres_conn


    delay = (
        RECONNECT_INITIAL_S
    )


    while True:

        try:

            if postgres_conn is not None:

                try:

                    postgres_conn.close()

                except Exception:

                    pass


            postgres_conn = (
                db.get_connection()
            )


            if not db.ping(
                postgres_conn
            ):

                raise RuntimeError(
                    "PostgreSQL ping failed"
                )


            print(
                "[DB] Connected:",
                db.DB_NAME,
            )


            return


        except KeyboardInterrupt:

            raise


        except Exception as exc:

            stats.db_errors += 1


            log_error(
                "PostgreSQL connection failed",
                exc,
            )


            print(
                f"[DB] Retry in "
                f"{delay:.1f} s..."
            )


            time.sleep(
                delay
            )


            delay = min(

                delay * 2.0,

                RECONNECT_MAX_S,
            )


def recover_db():

    stats.db_reconnects += 1


    print()
    print(
        "[DB] Attempting recovery..."
    )


    connect_db_with_retry()


    print(
        "[DB] Recovery complete."
    )


# ================================================================
# Sample integrity
# ================================================================

def validate_sample_integrity(
    sample: dict[str, Any],
):

    sequence = (
        sample["sequence"]
    )


    uptime_ms = (
        sample["uptime_ms"]
    )


    # ------------------------------------------------------------
    # Register Map
    # ------------------------------------------------------------

    if (
        sample[
            "register_map_version"
        ]
        != EXPECTED_MAP_VERSION
    ):

        raise RuntimeError(
            "Unexpected Register Map: "
            f"0x{sample['register_map_version']:04X}"
        )


    # ------------------------------------------------------------
    # Sequence
    # ------------------------------------------------------------

    if (
        stats.previous_sequence
        is not None
    ):

        expected = (

            stats.previous_sequence
            + 1

        ) & 0xFFFFFFFF


        if (
            sequence
            != expected
        ):

            stats.sequence_errors += 1


            print(
                "[WARN] sample_counter discontinuity:",
                stats.previous_sequence,
                "->",
                sequence,
            )


    # ------------------------------------------------------------
    # Uptime
    # ------------------------------------------------------------

    if (
        stats.previous_uptime_ms
        is not None

        and uptime_ms
        < stats.previous_uptime_ms
    ):

        stats.uptime_regressions += 1


        print(
            "[WARN] Pico uptime regression:",
            stats.previous_uptime_ms,
            "->",
            uptime_ms,
        )


    stats.previous_sequence = (
        sequence
    )


    stats.previous_uptime_ms = (
        uptime_ms
    )


    if (
        stats.first_sequence
        is None
    ):

        stats.first_sequence = (
            sequence
        )


    stats.last_sequence = (
        sequence
    )


# ================================================================
# BME688 warm-up state
# ================================================================

warmup_started_at = None

warmup_complete = False

stable_streak = 0


def apply_bme_warmup(
    sample: dict[str, Any],
):

    global warmup_started_at
    global warmup_complete
    global stable_streak


    bme = (
        sample["bme688"]
    )


    now = (
        time.monotonic()
    )


    if (
        warmup_started_at
        is None
    ):

        warmup_started_at = (
            now
        )


    warmup_elapsed_s = (

        now
        - warmup_started_at
    )


    stable_now = (

        bme["status"]
        == "OK"

        and bme["gas_valid"]

        and bme["heater_stable"]
    )


    if not warmup_complete:

        if stable_now:

            stable_streak += 1

        else:

            stable_streak = 0


        if (

            warmup_elapsed_s
            >= WARMUP_MIN_S

            and stable_streak
            >= WARMUP_STABLE_SAMPLES
        ):

            warmup_complete = True


            print()
            print(
                "============================================================"
            )

            print(
                " BME688 GAS WARM-UP COMPLETE"
            )

            print(
                "============================================================"
            )

            print(
                f"Elapsed       : "
                f"{warmup_elapsed_s:.1f} s"
            )

            print(
                f"Stable streak : "
                f"{stable_streak}"
            )

            print()


    # ------------------------------------------------------------
    # Per-sample usability
    # ------------------------------------------------------------

    gas_ready = (

        warmup_complete

        and stable_now
    )


    bme[
        "gas_ready"
    ] = (
        gas_ready
    )


    sample[
        "warmup"
    ] = {

        "elapsed_s":
            warmup_elapsed_s,

        "stable_streak":
            stable_streak,

        "warmup_complete":
            warmup_complete,

        "gas_ready":
            gas_ready,
    }


    return gas_ready


# ================================================================
# Stats update
# ================================================================

def update_sensor_stats(
    sample,
):

    dht = (
        sample["dht11"]
    )


    bme = (
        sample["bme688"]
    )


    if (
        dht["status"]
        != "OK"
    ):

        stats.dht_sensor_errors += 1


    if (
        bme["status"]
        != "OK"
    ):

        stats.bme_sensor_errors += 1


    if not bme[
        "gas_valid"
    ]:

        stats.gas_invalid_samples += 1


    if not bme[
        "heater_stable"
    ]:

        stats.heater_unstable_samples += 1


    if bme[
        "gas_ready"
    ]:

        stats.gas_ready_samples += 1


# ================================================================
# Database persistence
# ================================================================

def _reconcile_committed_sample(
    run_id: int,
    sample: dict[str, Any],
):

    """
    After a lost connection, check whether the insert we just
    attempted actually committed on the server before the
    connection dropped.

    Our DB unique constraint protects against duplicate
    run_id + sequence + sensor_type writes, so it is safe to
    look this up and simply reuse the existing rows.

    Returns (dht_raw_id, bme_raw_id) if both rows are already
    present, otherwise None.
    """

    global postgres_conn


    try:

        with postgres_conn.cursor() as cur: # type: ignore

            cur.execute(
                """
                SELECT
                    sensor_type,
                    id

                FROM weather_measurement_raw

                WHERE
                    run_id = %s
                    AND sequence = %s

                ORDER BY sensor_type
                """,
                (
                    run_id,
                    sample[
                        "sequence"
                    ],
                ),
            )


            existing = (
                cur.fetchall()
            )


        postgres_conn.commit() # type: ignore


        existing_map = {

            sensor_type:
                raw_id

            for (
                sensor_type,
                raw_id,
            )
            in existing
        }


        if (

            "DHT11"
            in existing_map

            and "BME688"
            in existing_map
        ):

            print(
                "[DB] Sample already exists after reconnect; "
                "treating as committed."
            )


            return (

                int(
                    existing_map[
                        "DHT11"
                    ]
                ),

                int(
                    existing_map[
                        "BME688"
                    ]
                ),
            )


    except Exception as verification_exc:

        log_error(
            "Could not verify sample after DB reconnect",
            verification_exc,
        )


    return None


def persist_sample(
    run_id: int,
    sample: dict[str, Any],
):

    """
    Persist one environmental sample, retrying as needed.

    The decoded sample is already present in the JSONL audit log
    before this function is called, so even a prolonged DB outage
    or a permanent give-up below does not erase the observation.

    Two very different failure kinds are handled differently:

        OperationalError (lost connection)
            Transport problem. Reconnect and retry forever —
            this is exactly what "wait out a DB outage" means.

        Anything else (bad data, constraint violation, ...)
            Not a transport problem — reconnecting will not fix
            it, and the same insert would just fail again.
            Retried a bounded number of times, then given up on,
            so one bad sample cannot stall the acquisition loop
            indefinitely.
    """

    global postgres_conn


    data_error_attempts = 0


    while True:

        try:

            (
                dht_raw_id,
                bme_raw_id,

            ) = db.insert_environmental_sample(

                postgres_conn, # type: ignore

                run_id=run_id,

                sample=sample,
            )


            stats.db_successful_samples += 1


            return (

                dht_raw_id,

                bme_raw_id,
            )


        except KeyboardInterrupt:

            raise


        except psycopg.OperationalError as exc:

            stats.db_errors += 1

            stats.db_failed_samples += 1


            log_error(
                "PostgreSQL connection lost during insert",
                exc,
            )


            recover_db()


            reconciled = (
                _reconcile_committed_sample(
                    run_id,
                    sample,
                )
            )


            if reconciled is not None:

                stats.db_successful_samples += 1


                return reconciled


            # Connection is back but the insert did not commit.
            # Retry the same sample — this branch never gives up,
            # since the failure was transport-level, not data.


        except Exception as exc:

            data_error_attempts += 1

            stats.db_errors += 1

            stats.db_failed_samples += 1


            log_error(
                "PostgreSQL insert failed "
                f"(attempt {data_error_attempts}/"
                f"{DB_INSERT_MAX_RETRIES})",
                exc,
            )


            if (
                data_error_attempts
                >= DB_INSERT_MAX_RETRIES
            ):

                log_error(
                    "Giving up on PostgreSQL insert for this "
                    f"sample (sequence={sample['sequence']}) "
                    f"after {data_error_attempts} attempts. "
                    "Sample remains in the JSONL audit log only.",
                )


                return (
                    None,
                    None,
                )


            time.sleep(
                DB_INSERT_RETRY_DELAY_S
            )


# ================================================================
# Console sample output
# ================================================================

def print_sample(
    sample_number: int,
    sample: dict[str, Any],
    latency_ms: float,
    dht_raw_id: int | None,
    bme_raw_id: int | None,
):

    dht = (
        sample["dht11"]
    )


    bme = (
        sample["bme688"]
    )


    warmup = (
        sample["warmup"]
    )


    delta_t = (

        dht["temperature_c"]
        - bme["temperature_c"]
    )


    delta_rh = (

        dht["humidity_pct"]
        - bme["humidity_pct"]
    )


    state_name = (

        DEVICE_STATE_NAMES
        .get(

            sample[
                "device_state"
            ],

            str(
                sample[
                    "device_state"
                ]
            ),
        )
    )


    print(
        f"[{sample_number:06d}] "
        f"{sample['timestamp_utc']}"
    )


    print(
        f"    seq / uptime : "
        f"{sample['sequence']} / "
        f"{sample['uptime_ms'] / 1000:.0f}s"
    )


    print(
        f"    device       : "
        f"{state_name}"
    )


    print(
        f"    READ_NOW     : "
        f"{latency_ms:.2f} ms"
    )


    print(
        f"    DHT11        : "
        f"T={dht['temperature_c']:.2f} C  "
        f"RH={dht['humidity_pct']:.2f} %  "
        f"status={dht['status']}"
    )


    print(
        f"    BME688       : "
        f"T={bme['temperature_c']:.2f} C  "
        f"RH={bme['humidity_pct']:.2f} %  "
        f"P={bme['pressure_hpa']:.2f} hPa"
    )


    print(
        f"                   "
        f"Gas={bme['gas_resistance_ohm']} ohm  "
        f"valid={bme['gas_valid']}  "
        f"heater={bme['heater_stable']}  "
        f"ready={bme['gas_ready']}"
    )


    print(
        f"    warm-up      : "
        f"{warmup['elapsed_s']:.1f}s  "
        f"stable={warmup['stable_streak']}/"
        f"{WARMUP_STABLE_SAMPLES}  "
        f"complete={warmup['warmup_complete']}"
    )


    print(
        f"    DHT-BME      : "
        f"dT={delta_t:+.2f} C  "
        f"dRH={delta_rh:+.2f} pp"
    )


    if (

        dht_raw_id is None

        or bme_raw_id is None
    ):

        db_line = (
            "FAILED — see JSONL audit log"
        )

    else:

        db_line = (
            f"DHT={dht_raw_id}, "
            f"BME={bme_raw_id}"
        )


    print(
        f"    PostgreSQL   : "
        f"{db_line}"
    )


    print()


# ================================================================
# Health summary
# ================================================================

def print_health_summary(
    run_id: int,
    run_name: str,
):

    latencies = (
        stats.latencies_ms
    )


    latency_mean = (

        sum(latencies)
        / len(latencies)

        if latencies

        else 0.0
    )


    latency_min = (

        min(latencies)

        if latencies

        else 0.0
    )


    latency_max = (

        max(latencies)

        if latencies

        else 0.0
    )


    print()
    print(
        "============================================================"
    )

    print(
        " P3-06 HEALTH SUMMARY"
    )

    print(
        "============================================================"
    )


    print(
        "run_id                   :",
        run_id,
    )


    print(
        "run_name                 :",
        run_name,
    )


    print(
        "acquisition attempts     :",
        stats.acquisition_attempts,
    )


    print(
        "successful acquisitions  :",
        stats.successful_acquisitions,
    )


    print(
        "failed acquisitions      :",
        stats.failed_acquisitions,
    )


    print(
        "DB persisted samples     :",
        stats.db_successful_samples,
    )


    print(
        "DB failed attempts       :",
        stats.db_failed_samples,
    )


    print(
        "Modbus errors            :",
        stats.modbus_errors,
    )


    print(
        "Modbus reconnects        :",
        stats.modbus_reconnects,
    )


    print(
        "DB errors                :",
        stats.db_errors,
    )


    print(
        "DB reconnects            :",
        stats.db_reconnects,
    )


    print(
        "READ_NOW timeouts        :",
        stats.read_now_timeouts,
    )


    print(
        "sequence discontinuities :",
        stats.sequence_errors,
    )


    print(
        "uptime regressions       :",
        stats.uptime_regressions,
    )


    print(
        "DHT sensor errors        :",
        stats.dht_sensor_errors,
    )


    print(
        "BME sensor errors        :",
        stats.bme_sensor_errors,
    )


    print(
        "gas invalid samples      :",
        stats.gas_invalid_samples,
    )


    print(
        "heater unstable samples  :",
        stats.heater_unstable_samples,
    )


    print(
        "gas_ready samples        :",
        stats.gas_ready_samples,
    )


    print(
        "warmup complete          :",
        warmup_complete,
    )


    print(
        "first sequence           :",
        stats.first_sequence,
    )


    print(
        "last sequence            :",
        stats.last_sequence,
    )


    print(
        "READ_NOW latency mean    : "
        f"{latency_mean:.2f} ms"
    )


    print(
        "READ_NOW latency min     : "
        f"{latency_min:.2f} ms"
    )


    print(
        "READ_NOW latency max     : "
        f"{latency_max:.2f} ms"
    )


    print(
        "audit JSONL              :",
        AUDIT_LOG_PATH,
    )


    print(
        "error log                :",
        ERROR_LOG_PATH,
    )


    print(
        "============================================================"
    )

    print()


# ================================================================
# Database run finalization
# ================================================================

def finish_run_safely(
    run_id: int,
    *,
    status: str,
    notes: str,
):

    global postgres_conn


    for attempt in range(
        2
    ):

        try:

            db.finish_test_run(

                postgres_conn, # type: ignore

                run_id=run_id,

                status=status,

                notes=notes,
            )


            return True


        except Exception as exc:

            log_error(
                "Could not finalize weather_test_run",
                exc,
            )


            if attempt == 0:

                try:

                    recover_db()

                except Exception:

                    pass


    return False


# ================================================================
# Main
# ================================================================

def main():

    global postgres_conn


    run_id = None

    original_run_enable = False


    # ============================================================
    # Unique run
    # ============================================================

    run_name = (

        "P3-06-LONG-"

        + datetime.now()
        .strftime(
            "%Y%m%d-%H%M%S"
        )
    )


    print()
    print(
        "============================================================"
    )

    print(
        " P3-06 LONG-DURATION ENVIRONMENTAL RUN"
    )

    print(
        " Raspberry Pi 5"
    )

    print(
        "============================================================"
    )

    print()


    print(
        "Run mode            : INDEFINITE"
    )


    print(
        "Stop                : Ctrl+C"
    )


    print(
        "Sampling period     :",
        SAMPLE_PERIOD_S,
        "s",
    )


    print(
        "BME warm-up         :",
        WARMUP_MIN_S,
        "s +",
        WARMUP_STABLE_SAMPLES,
        "stable samples",
    )


    print(
        "Run name            :",
        run_name,
    )


    print(
        "Audit log           :",
        AUDIT_LOG_PATH,
    )


    print(
        "Error log           :",
        ERROR_LOG_PATH,
    )


    print()


    try:

        # ========================================================
        # PostgreSQL
        # ========================================================

        print(
            "[SETUP] PostgreSQL..."
        )


        connect_db_with_retry()


        # ========================================================
        # Modbus
        # ========================================================

        print(
            "[SETUP] Modbus..."
        )


        connect_modbus_with_retry()


        version = (
            read_map_version()
        )


        print(
            "[MODBUS] Register Map:",
            f"0x{version:04X}",
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
        # Pico state
        # ========================================================

        original_run_enable = (
            read_run_enable()
        )


        pico_interval = (
            read_sample_interval()
        )


        print(
            "[MODBUS] Original run_enable:",
            original_run_enable,
        )


        print(
            "[MODBUS] Pico interval:",
            pico_interval,
            "s",
        )


        # ========================================================
        # READ_NOW-only controlled mode
        # ========================================================

        if original_run_enable:

            print(
                "[MODBUS] Disabling periodic Pico sampling."
            )


            set_run_enable(
                False
            )


        # ========================================================
        # Database run
        # ========================================================

        run_id = (
            db.create_test_run(

                postgres_conn, # type: ignore

                run_name=run_name,

                sample_period_s=(
                    SAMPLE_PERIOD_S
                ),

                register_map_version=(
                    EXPECTED_MAP_VERSION
                ),

                notes=(
                    "P3-06 long-duration environmental stability run. "
                    "Manual stop via Ctrl+C. "
                    "DHT11 + BME688 over Modbus RTU. "
                    "BME688 gas warm-up requires minimum 10 seconds "
                    "and 3 consecutive OK/gas_valid/heater_stable samples."
                ),
            )
        )


        print(
            "[DB] run_id:",
            run_id,
        )


        print()
        print(
            "============================================================"
        )

        print(
            " ACQUISITION STARTED"
        )

        print(
            " Ctrl+C to stop cleanly"
        )

        print(
            "============================================================"
        )

        print()


        # ========================================================
        # Continuous acquisition
        # ========================================================

        next_sample_at = (
            time.monotonic()
        )


        sample_number = 0


        while True:

            # ====================================================
            # Absolute sample cadence
            # ====================================================

            now = (
                time.monotonic()
            )


            if now < next_sample_at:

                time.sleep(
                    next_sample_at
                    - now
                )


            sample_number += 1

            stats.acquisition_attempts += 1


            # ====================================================
            # Acquisition
            # ====================================================

            try:

                (
                    counter_before,
                    counter_after,
                    latency_ms,

                ) = trigger_read_now()


                sample = (
                    read_environmental_snapshot()
                )


                if (
                    sample["sequence"]
                    != counter_after
                ):

                    raise RuntimeError(
                        "READ_NOW / snapshot sequence mismatch: "
                        f"{counter_after} vs "
                        f"{sample['sequence']}"
                    )


                sample[
                    "trigger_latency_ms"
                ] = (
                    latency_ms
                )


                sample[
                    "counter_before"
                ] = (
                    counter_before
                )


                sample[
                    "counter_after"
                ] = (
                    counter_after
                )


                stats.latencies_ms.append(
                    latency_ms
                )


                # =================================================
                # Integrity
                # =================================================

                validate_sample_integrity(
                    sample
                )


                # =================================================
                # Warm-up
                # =================================================

                apply_bme_warmup(
                    sample
                )


                update_sensor_stats(
                    sample
                )


                # =================================================
                # Local durable audit log FIRST
                # =================================================

                append_jsonl(
                    sample
                )


                # =================================================
                # PostgreSQL
                # =================================================

                (
                    dht_raw_id,
                    bme_raw_id,

                ) = persist_sample(

                    run_id,

                    sample,
                )


                stats.successful_acquisitions += 1


                # =================================================
                # Console
                # =================================================

                print_sample(

                    sample_number,

                    sample,

                    latency_ms,

                    dht_raw_id,

                    bme_raw_id,
                )


            # ====================================================
            # READ_NOW timeout
            # ====================================================

            except TimeoutError as exc:

                stats.failed_acquisitions += 1

                stats.read_now_timeouts += 1

                stats.modbus_errors += 1


                log_error(
                    "READ_NOW timeout",
                    exc,
                )


                recover_modbus()


            # ====================================================
            # Other Modbus / acquisition failures
            # ====================================================

            except KeyboardInterrupt:

                raise


            except Exception as exc:

                stats.failed_acquisitions += 1

                stats.modbus_errors += 1


                log_error(
                    "Environmental acquisition failed",
                    exc,
                )


                recover_modbus()


            # ====================================================
            # Health summary
            # ====================================================

            if (

                sample_number
                % HEALTH_SUMMARY_EVERY_SAMPLES

                == 0
            ):

                print_health_summary(

                    run_id,

                    run_name,
                )


            # ====================================================
            # Absolute cadence
            #
            # If recovery took longer than one period, don't attempt
            # to "catch up" by firing many immediate samples.
            # ====================================================

            next_sample_at += (
                SAMPLE_PERIOD_S
            )


            now = (
                time.monotonic()
            )


            if (
                next_sample_at
                < now
            ):

                next_sample_at = (

                    now
                    + SAMPLE_PERIOD_S
                )


    # ============================================================
    # Manual stop
    # ============================================================

    except KeyboardInterrupt:

        print()
        print()
        print(
            "============================================================"
        )

        print(
            " MANUAL STOP RECEIVED"
        )

        print(
            "============================================================"
        )

        print()


        if (
            run_id is not None
        ):

            print_health_summary(

                run_id,

                run_name,
            )


            finish_run_safely(

                run_id,

                status="COMPLETED",

                notes=(
                    "Long-duration P3-06 run stopped manually "
                    "with Ctrl+C as intended. "
                    f"Acquisition attempts={stats.acquisition_attempts}, "
                    f"successful={stats.successful_acquisitions}, "
                    f"failed={stats.failed_acquisitions}, "
                    f"DB persisted={stats.db_successful_samples}, "
                    f"Modbus errors={stats.modbus_errors}, "
                    f"DB errors={stats.db_errors}, "
                    f"sequence errors={stats.sequence_errors}, "
                    f"uptime regressions={stats.uptime_regressions}."
                ),
            )


            print(
                "[DB] Run marked COMPLETED."
            )


    # ============================================================
    # Fatal failure
    # ============================================================

    except Exception as exc:

        log_error(
            "Fatal P3-06 runner failure",
            exc,
        )


        if (
            run_id is not None
        ):

            finish_run_safely(

                run_id,

                status="FAILED",

                notes=(
                    "P3-06 runner terminated due to fatal exception: "
                    + repr(exc)
                ),
            )


        raise


    # ============================================================
    # Cleanup
    # ============================================================

    finally:

        # --------------------------------------------------------
        # Restore Pico previous state
        # --------------------------------------------------------

        try:

            if original_run_enable:

                print(
                    "[CLEANUP] Restoring Pico run_enable=True..."
                )


                set_run_enable(
                    True
                )


        except Exception as exc:

            log_error(
                "Could not restore Pico run_enable",
                exc,
            )


        # --------------------------------------------------------
        # Close Modbus
        # --------------------------------------------------------

        try:

            modbus_client.close()

        except Exception:

            pass


        # --------------------------------------------------------
        # Close PostgreSQL
        # --------------------------------------------------------

        if (
            postgres_conn
            is not None
        ):

            try:

                postgres_conn.close()

            except Exception:

                pass


        print(
            "[CLEANUP] Connections closed."
        )


        print(
            "[CLEANUP] Audit log:",
            AUDIT_LOG_PATH,
        )


        print(
            "[CLEANUP] Error log:",
            ERROR_LOG_PATH,
        )


# ================================================================
# Entry point
# ================================================================

if __name__ == "__main__":

    main()
"""
p3_06_db_smoke_test.py

Weather Station
P3-06 — PostgreSQL persistence smoke test.

BOARD:
    Raspberry Pi 5

PICO:
    No changes required.

    Must be running:
        bme688_modbus_pico.py

Purpose:
    Validate the complete P3-06 pipeline before starting
    the 24-48 hour long-duration test.

Pipeline:

    BME688 + DHT11
          |
          v
        Pico 2
          |
          | Modbus RTU
          v
    Raspberry Pi 5
          |
          | decode Register Map v1.1
          v
      warm-up logic
          |
          v
     PostgreSQL db.py
          |
          +--> DHT11 raw row
          |
          +--> BME688 raw row
          |
          v
    weather_p3_sensor_comparison


Test duration:
    20 seconds

Sampling:
    READ_NOW every 5 seconds

Expected:
    approximately 4 complete environmental acquisitions
    = 8 raw PostgreSQL rows
"""

from __future__ import annotations

import sys
import time

from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusSerialClient


# ================================================================
# Local db module
# ================================================================

SCRIPT_DIR = Path(
    __file__
).resolve().parent


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


TEST_DURATION_S = 20.0

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
# BME688 warm-up policy
#
# Gas is considered ready only when:
#
#   1. At least 10 seconds have elapsed since the first
#      BME688 acquisition.
#
#   AND
#
#   2. There have been at least 3 consecutive acquisitions with:
#
#          status == OK
#          gas_valid == True
#          heater_stable == True
#
# Raw values are ALWAYS stored, even during warm-up.
# ================================================================

WARMUP_MIN_S = 10.0

WARMUP_STABLE_SAMPLES = 3


# ================================================================
# Helpers
# ================================================================

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
# Modbus client
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
# Modbus helpers
# ================================================================

def connect_modbus() -> None:

    if not client.connect():

        raise RuntimeError(
            f"Could not open {PORT}"
        )


def read_map_version() -> int:

    response = client.read_input_registers(

        address=208,

        count=1,

        device_id=UNIT_ID,
    )


    ensure_ok(
        response,
        "Read Register Map version",
    )


    return int(
        response.registers[0]
    )


def read_run_enable() -> bool:

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


def set_run_enable(
    value: bool,
) -> None:

    response = client.write_coil(

        address=COIL_RUN_ENABLE,

        value=value,

        device_id=UNIT_ID,
    )


    ensure_ok(
        response,
        "Write run_enable",
    )


def read_sample_interval() -> int:

    response = (
        client.read_holding_registers(

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
        client.read_input_registers(

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
            "Read READ_NOW coil",
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
        "READ_NOW did not complete "
        f"within {READ_NOW_TIMEOUT_S} seconds"
    )


# ================================================================
# Environmental snapshot
# ================================================================

def read_environmental_snapshot():

    response = (
        client.read_input_registers(

            address=IREG_START,

            count=IREG_COUNT,

            device_id=UNIT_ID,
        )
    )


    ensure_ok(
        response,
        "Read Input Registers 200-216",
    )


    regs = list(
        response.registers
    )


    if len(regs) != IREG_COUNT:

        raise RuntimeError(
            "Invalid Modbus block length. "
            f"Expected {IREG_COUNT}, "
            f"received {len(regs)}"
        )


    # ============================================================
    # Core / DHT11
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


    bme_flags = (
        regs[15]
    )


    bme_status_raw = (
        regs[16]
    )


    gas_valid = bool(

        bme_flags
        & 0x0001
    )


    heater_stable = bool(

        bme_flags
        & 0x0002
    )


    return {

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
                bme_flags,
        },
    }


# ================================================================
# PostgreSQL preflight
# ================================================================

def database_preflight(
    conn,
):

    print()
    print(
        "------------------------------------------------------------"
    )

    print(
        " DATABASE PREFLIGHT"
    )

    print(
        "------------------------------------------------------------"
    )


    if not db.ping(
        conn
    ):

        raise RuntimeError(
            "PostgreSQL ping failed"
        )


    print(
        "[DB] Connection                  : OK"
    )


    print(
        "[DB] Database                    :",
        db.DB_NAME,
    )


    print(
        "[DB] Node ID                     :",
        db.NODE_ID,
    )


    if db.ENV_FILE is not None:

        print(
            "[DB] .env                        :",
            db.ENV_FILE,
        )


    required_relations = [

        "weather_measurement_raw",

        "weather_test_run",

        "weather_p3_sensor_comparison",
    ]


    with conn.cursor() as cur:

        for relation in required_relations:

            cur.execute(
                """
                SELECT to_regclass(%s)
                """,
                (
                    relation,
                ),
            )


            row = (
                cur.fetchone()
            )


            found = (

                row is not None
                and row[0] is not None
            )


            print(
                f"[DB] {relation:<27}: "
                f"{'OK' if found else 'MISSING'}"
            )


            if not found:

                raise RuntimeError(
                    f"Missing PostgreSQL relation: "
                    f"{relation}"
                )


    conn.commit()


# ================================================================
# PostgreSQL verification
# ================================================================

def verify_database_run(
    conn,
    run_id: int,
    expected_samples: int,
):

    print()
    print(
        "============================================================"
    )

    print(
        " POSTGRESQL VERIFICATION"
    )

    print(
        "============================================================"
    )

    print()


    # ============================================================
    # Run metadata
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                run_name,
                phase,
                node_id,
                started_at_utc,
                ended_at_utc,
                status,
                sample_period_s,
                register_map_version

            FROM weather_test_run

            WHERE id = %s
            """,
            (
                run_id,
            ),
        )


        run_row = (
            cur.fetchone()
        )


    if run_row is None:

        raise RuntimeError(
            "weather_test_run row disappeared"
        )


    (
        run_name,
        phase,
        node_id,
        started_at,
        ended_at,
        run_status,
        stored_period,
        stored_map,
    ) = run_row


    print(
        "RUN"
    )

    print(
        "  run_id               :",
        run_id,
    )

    print(
        "  run_name             :",
        run_name,
    )

    print(
        "  phase                :",
        phase,
    )

    print(
        "  node_id              :",
        node_id,
    )

    print(
        "  started              :",
        started_at,
    )

    print(
        "  ended                :",
        ended_at,
    )

    print(
        "  status               :",
        run_status,
    )

    print(
        "  sample_period_s      :",
        stored_period,
    )

    print(
        "  register_map         : "
        f"0x{stored_map:04X}"
    )


    # ============================================================
    # Raw counts
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT

                count(*) AS total_rows,

                count(*) FILTER (
                    WHERE sensor_type = 'DHT11'
                ) AS dht_rows,

                count(*) FILTER (
                    WHERE sensor_type = 'BME688'
                ) AS bme_rows,

                count(DISTINCT sequence)
                    AS distinct_sequences,

                min(sequence)
                    AS min_sequence,

                max(sequence)
                    AS max_sequence

            FROM weather_measurement_raw

            WHERE run_id = %s
            """,
            (
                run_id,
            ),
        )


        counts = (
            cur.fetchone()
        )


    (
        total_rows,
        dht_rows,
        bme_rows,
        distinct_sequences,
        min_sequence,
        max_sequence,
    ) = counts


    expected_raw_rows = (
        expected_samples
        * 2
    )


    print()
    print(
        "RAW STORAGE"
    )

    print(
        f"  expected samples     : "
        f"{expected_samples}"
    )

    print(
        f"  distinct sequences   : "
        f"{distinct_sequences}"
    )

    print(
        f"  expected raw rows    : "
        f"{expected_raw_rows}"
    )

    print(
        f"  actual raw rows      : "
        f"{total_rows}"
    )

    print(
        f"  DHT11 rows           : "
        f"{dht_rows}"
    )

    print(
        f"  BME688 rows          : "
        f"{bme_rows}"
    )

    print(
        f"  first sequence       : "
        f"{min_sequence}"
    )

    print(
        f"  last sequence        : "
        f"{max_sequence}"
    )


    # ============================================================
    # Complete pairs
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT count(*)

            FROM weather_p3_sensor_comparison

            WHERE run_id = %s
            """,
            (
                run_id,
            ),
        )


        comparison_rows = int(
            cur.fetchone()[0]
        )


    print()
    print(
        "PAIRING"
    )

    print(
        f"  comparison rows      : "
        f"{comparison_rows}"
    )

    print(
        f"  expected pairs       : "
        f"{expected_samples}"
    )


    # ============================================================
    # Look explicitly for incomplete acquisitions
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                sequence,
                count(*) AS sensor_rows

            FROM weather_measurement_raw

            WHERE run_id = %s

            GROUP BY sequence

            HAVING count(*) <> 2

            ORDER BY sequence
            """,
            (
                run_id,
            ),
        )


        incomplete_pairs = (
            cur.fetchall()
        )


    print(
        f"  incomplete pairs     : "
        f"{len(incomplete_pairs)}"
    )


    # ============================================================
    # BME688 warmup states
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT

                count(*) FILTER (
                    WHERE gas_ready IS TRUE
                ),

                count(*) FILTER (
                    WHERE gas_ready IS FALSE
                ),

                count(*) FILTER (
                    WHERE gas_valid IS TRUE
                ),

                count(*) FILTER (
                    WHERE heater_stable IS TRUE
                )

            FROM weather_measurement_raw

            WHERE
                run_id = %s
                AND sensor_type = 'BME688'
            """,
            (
                run_id,
            ),
        )


        (
            gas_ready_rows,
            warmup_rows,
            gas_valid_rows,
            heater_stable_rows,
        ) = cur.fetchone()


    print()
    print(
        "BME688 GAS STATE"
    )

    print(
        f"  gas_ready=True       : "
        f"{gas_ready_rows}"
    )

    print(
        f"  warmup rows          : "
        f"{warmup_rows}"
    )

    print(
        f"  gas_valid=True       : "
        f"{gas_valid_rows}"
    )

    print(
        f"  heater_stable=True   : "
        f"{heater_stable_rows}"
    )


    # ============================================================
    # Sensor comparison
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT

                avg(temperature_diff_c),

                max(abs(temperature_diff_c)),

                avg(humidity_diff_pct),

                max(abs(humidity_diff_pct))

            FROM weather_p3_sensor_comparison

            WHERE run_id = %s
            """,
            (
                run_id,
            ),
        )


        (
            avg_temp_diff,
            max_temp_diff,
            avg_rh_diff,
            max_rh_diff,
        ) = cur.fetchone()


    print()
    print(
        "DHT11 vs BME688"
    )

    print(
        "  convention           : "
        "DHT11 - BME688"
    )

    print(
        f"  mean ΔT              : "
        f"{float(avg_temp_diff):+.3f} C"
    )

    print(
        f"  max |ΔT|             : "
        f"{float(max_temp_diff):.3f} C"
    )

    print(
        f"  mean ΔRH             : "
        f"{float(avg_rh_diff):+.3f} pp"
    )

    print(
        f"  max |ΔRH|            : "
        f"{float(max_rh_diff):.3f} pp"
    )


    # ============================================================
    # Show every paired sample
    # ============================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT

                sequence,

                dht_temperature_c,

                bme_temperature_c,

                temperature_diff_c,

                dht_humidity_pct,

                bme_humidity_pct,

                humidity_diff_pct,

                pressure_hpa,

                gas_resistance_ohm,

                gas_valid,

                heater_stable,

                gas_ready

            FROM weather_p3_sensor_comparison

            WHERE run_id = %s

            ORDER BY sequence
            """,
            (
                run_id,
            ),
        )


        comparison_data = (
            cur.fetchall()
        )


    print()
    print(
        "PAIRED DATA"
    )


    for row in comparison_data:

        (
            sequence,
            dht_t,
            bme_t,
            delta_t,
            dht_rh,
            bme_rh,
            delta_rh,
            pressure,
            gas,
            gas_valid,
            heater_stable,
            gas_ready,
        ) = row


        print(
            f"  seq={sequence:<6} "
            f"DHT={dht_t:5.2f}C/{dht_rh:5.2f}%  "
            f"BME={bme_t:5.2f}C/{bme_rh:5.2f}%  "
            f"dT={delta_t:+5.2f}C  "
            f"dRH={delta_rh:+6.2f}pp  "
            f"P={pressure:7.2f}hPa  "
            f"Gas={gas:8.0f}ohm  "
            f"GV={gas_valid}  "
            f"HS={heater_stable}  "
            f"READY={gas_ready}"
        )


    # ============================================================
    # PASS / FAIL
    # ============================================================

    hard_failures = []


    if run_status != "COMPLETED":

        hard_failures.append(
            "run status is not COMPLETED"
        )


    if total_rows != expected_raw_rows:

        hard_failures.append(
            "incorrect raw row count"
        )


    if dht_rows != expected_samples:

        hard_failures.append(
            "incorrect DHT11 row count"
        )


    if bme_rows != expected_samples:

        hard_failures.append(
            "incorrect BME688 row count"
        )


    if (
        distinct_sequences
        != expected_samples
    ):

        hard_failures.append(
            "incorrect distinct sequence count"
        )


    if (
        comparison_rows
        != expected_samples
    ):

        hard_failures.append(
            "not all environmental samples paired"
        )


    if incomplete_pairs:

        hard_failures.append(
            "incomplete DHT11/BME688 database pairs"
        )


    warnings = []


    if gas_ready_rows == 0:

        warnings.append(
            "gas did not leave warm-up during the "
            "20-second smoke test"
        )


    if (
        heater_stable_rows
        < bme_rows - 1
    ):

        warnings.append(
            "more than one BME688 sample had "
            "heater_stable=False"
        )


    if (
        gas_valid_rows
        != bme_rows
    ):

        warnings.append(
            "one or more BME688 samples had "
            "gas_valid=False"
        )


    print()
    print(
        "------------------------------------------------------------"
    )

    print(
        " DATABASE INTEGRITY RESULT"
    )

    print(
        "------------------------------------------------------------"
    )


    if hard_failures:

        for item in hard_failures:

            print(
                "FAIL:",
                item,
            )


        final_result = (
            "FAIL"
        )


    elif warnings:

        for item in warnings:

            print(
                "WARN:",
                item,
            )


        final_result = (
            "PASS WITH WARNINGS"
        )


    else:

        print(
            "No database integrity problems detected."
        )


        final_result = (
            "PASS"
        )


    print()
    print(
        "============================================================"
    )

    print(
        " P3-06 DB SMOKE TEST:",
        final_result,
    )

    print(
        "============================================================"
    )


    return final_result


# ================================================================
# Main
# ================================================================

def main():

    run_id = None

    postgres_conn = None

    original_run_enable = False

    completed_samples = 0

    run_finished = False

    stable_streak = 0

    first_bme_acquisition_time = None


    # ============================================================
    # Unique run name
    # ============================================================

    run_name = (

        "P3-06-SMOKE-"

        + datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )
    )


    print()
    print(
        "============================================================"
    )

    print(
        " P3-06 DATABASE SMOKE TEST"
    )

    print(
        " Raspberry Pi 5"
    )

    print(
        "============================================================"
    )

    print()

    print(
        "Duration           :",
        TEST_DURATION_S,
        "s",
    )

    print(
        "Sample period      :",
        SAMPLE_PERIOD_S,
        "s",
    )

    print(
        "Warm-up minimum    :",
        WARMUP_MIN_S,
        "s",
    )

    print(
        "Stable samples     :",
        WARMUP_STABLE_SAMPLES,
    )

    print(
        "Run name           :",
        run_name,
    )

    print()


    try:

        # ========================================================
        # PostgreSQL
        # ========================================================

        print(
            "[SETUP] Connecting PostgreSQL..."
        )


        postgres_conn = (
            db.get_connection()
        )


        database_preflight(
            postgres_conn
        )


        # ========================================================
        # Modbus
        # ========================================================

        print()
        print(
            "[SETUP] Connecting Modbus..."
        )


        connect_modbus()


        print(
            "[MODBUS] Port                       :",
            PORT,
        )


        print(
            "[MODBUS] Serial                     : "
            "115200 8N1"
        )


        version = (
            read_map_version()
        )


        print(
            "[MODBUS] Register Map               : "
            f"0x{version:04X}"
        )


        if (
            version
            != EXPECTED_MAP_VERSION
        ):

            raise RuntimeError(
                "Expected Register Map "
                f"0x{EXPECTED_MAP_VERSION:04X}, "
                f"got 0x{version:04X}"
            )


        # ========================================================
        # Pico current state
        # ========================================================

        original_run_enable = (
            read_run_enable()
        )


        pico_sample_interval = (
            read_sample_interval()
        )


        print(
            "[MODBUS] Original run_enable        :",
            original_run_enable,
        )


        print(
            "[MODBUS] Pico sample_interval_s     :",
            pico_sample_interval,
        )


        # ========================================================
        # Controlled mode
        #
        # We want exact one-request / one-sample semantics.
        # ========================================================

        if original_run_enable:

            print(
                "[MODBUS] Temporarily stopping "
                "periodic sampling..."
            )


            set_run_enable(
                False
            )


        # ========================================================
        # Create DB test run
        # ========================================================

        run_id = (
            db.create_test_run(

                postgres_conn,

                run_name=run_name,

                sample_period_s=(
                    SAMPLE_PERIOD_S
                ),

                register_map_version=(
                    EXPECTED_MAP_VERSION
                ),

                notes=(
                    "20-second P3-06 database smoke test. "
                    "DHT11 + BME688 via Modbus RTU. "
                    "BME688 warm-up policy: minimum 10 s "
                    "plus 3 consecutive OK/gas_valid/"
                    "heater_stable samples."
                ),
            )
        )


        print()
        print(
            "[DB] Created weather_test_run"
        )

        print(
            "[DB] run_id:",
            run_id,
        )


        # ========================================================
        # Sampling
        # ========================================================

        test_start = (
            time.monotonic()
        )


        deadline = (
            test_start
            + TEST_DURATION_S
        )


        next_sample_at = (
            test_start
        )


        sample_number = 0


        print()
        print(
            "============================================================"
        )

        print(
            " ACQUISITION + DATABASE INSERT"
        )

        print(
            "============================================================"
        )

        print()


        while (
            time.monotonic()
            < deadline
        ):

            now = (
                time.monotonic()
            )


            if now < next_sample_at:

                time.sleep(
                    next_sample_at
                    - now
                )


            if (
                time.monotonic()
                >= deadline
            ):

                break


            sample_number += 1


            # ====================================================
            # Trigger acquisition
            # ====================================================

            (
                counter_before,
                counter_after,
                latency_ms,

            ) = trigger_read_now()


            # ====================================================
            # Decode
            # ====================================================

            sample = (
                read_environmental_snapshot()
            )


            sample[
                "trigger_latency_ms"
            ] = latency_ms


            if (
                sample["sequence"]
                != counter_after
            ):

                raise RuntimeError(
                    "Snapshot sequence mismatch: "
                    f"READ_NOW ended at {counter_after}, "
                    f"snapshot contains "
                    f"{sample['sequence']}"
                )


            # ====================================================
            # BME688 warm-up
            # ====================================================

            if (
                first_bme_acquisition_time
                is None
            ):

                first_bme_acquisition_time = (
                    time.monotonic()
                )


            warmup_elapsed_s = (

                time.monotonic()
                - first_bme_acquisition_time
            )


            bme = (
                sample["bme688"]
            )


            bme_stable_now = (

                bme["status"]
                == "OK"

                and bme["gas_valid"]

                and bme["heater_stable"]
            )


            if bme_stable_now:

                stable_streak += 1

            else:

                stable_streak = 0


            gas_ready = (

                warmup_elapsed_s
                >= WARMUP_MIN_S

                and stable_streak
                >= WARMUP_STABLE_SAMPLES
            )


            bme[
                "gas_ready"
            ] = gas_ready


            # ====================================================
            # Database insert
            #
            # One transaction:
            #
            #     DHT11 + BME688
            # ====================================================

            (
                dht_raw_id,
                bme_raw_id,

            ) = db.insert_environmental_sample(

                postgres_conn,

                run_id=run_id,

                sample=sample,
            )


            completed_samples += 1


            # ====================================================
            # Thorough live output
            # ====================================================

            dht = (
                sample["dht11"]
            )


            device_state_name = (
                DEVICE_STATE_NAMES.get(

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
                f"[{sample_number:02d}] "
                f"sequence={sample['sequence']} "
                f"counter {counter_before}->{counter_after}"
            )


            print(
                f"     uptime      : "
                f"{sample['uptime_ms'] / 1000:.0f} s"
            )


            print(
                f"     device      : "
                f"{device_state_name}"
            )


            print(
                f"     latency     : "
                f"{latency_ms:.2f} ms"
            )


            print(
                f"     DHT11       : "
                f"T={dht['temperature_c']:.2f} C  "
                f"RH={dht['humidity_pct']:.2f} %  "
                f"status={dht['status']}"
            )


            print(
                f"     BME688      : "
                f"T={bme['temperature_c']:.2f} C  "
                f"RH={bme['humidity_pct']:.2f} %  "
                f"P={bme['pressure_hpa']:.2f} hPa"
            )


            print(
                f"                   "
                f"Gas={bme['gas_resistance_ohm']} ohm  "
                f"valid={bme['gas_valid']}  "
                f"heater={bme['heater_stable']}"
            )


            print(
                f"     warmup      : "
                f"{warmup_elapsed_s:.1f} / "
                f"{WARMUP_MIN_S:.1f} s  "
                f"stable_streak="
                f"{stable_streak}/"
                f"{WARMUP_STABLE_SAMPLES}"
            )


            print(
                f"     gas_ready   : "
                f"{gas_ready}"
            )


            print(
                f"     DB raw IDs  : "
                f"DHT11={dht_raw_id}, "
                f"BME688={bme_raw_id}"
            )


            print(
                f"     Delta       : "
                f"dT="
                f"{dht['temperature_c'] - bme['temperature_c']:+.2f} C  "
                f"dRH="
                f"{dht['humidity_pct'] - bme['humidity_pct']:+.2f} pp"
            )


            print()


            # ====================================================
            # Absolute cadence
            # ====================================================

            next_sample_at += (
                SAMPLE_PERIOD_S
            )


        # ========================================================
        # Complete run
        # ========================================================

        db.finish_test_run(

            postgres_conn,

            run_id=run_id,

            status="COMPLETED",

            notes=(
                f"Smoke test completed successfully. "
                f"Persisted {completed_samples} complete "
                f"environmental acquisitions / "
                f"{completed_samples * 2} raw sensor rows."
            ),
        )


        run_finished = True


        # ========================================================
        # Verify what PostgreSQL actually contains
        # ========================================================

        final_result = (
            verify_database_run(

                postgres_conn,

                run_id=run_id,

                expected_samples=(
                    completed_samples
                ),
            )
        )


        print()
        print(
            "Run name for later inspection:"
        )

        print(
            "   ",
            run_name,
        )


        print()
        print(
            "Final result:",
            final_result,
        )


    except KeyboardInterrupt:

        print()
        print(
            "[TEST] Interrupted by user."
        )


        if (
            postgres_conn is not None
            and run_id is not None
            and not run_finished
        ):

            try:

                db.finish_test_run(

                    postgres_conn,

                    run_id=run_id,

                    status="ABORTED",

                    notes=(
                        "Smoke test interrupted "
                        "by user."
                    ),
                )

                run_finished = True

            except Exception as exc:

                print(
                    "[DB] Could not mark "
                    "run ABORTED:",
                    repr(exc),
                )


    except Exception as exc:

        print()
        print(
            "============================================================"
        )

        print(
            " P3-06 DB SMOKE TEST: FAIL"
        )

        print(
            "============================================================"
        )

        print()

        print(
            "ERROR:",
            repr(exc),
        )


        if (
            postgres_conn is not None
            and run_id is not None
            and not run_finished
        ):

            try:

                db.finish_test_run(

                    postgres_conn,

                    run_id=run_id,

                    status="FAILED",

                    notes=(
                        "Smoke test failed: "
                        + repr(exc)
                    ),
                )

                run_finished = True

            except Exception as finish_exc:

                print(
                    "[DB] Could not mark "
                    "run FAILED:",
                    repr(finish_exc),
                )


        raise


    finally:

        # ========================================================
        # Restore Pico state
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


                print(
                    "[CLEANUP] Pico state restored."
                )


        except Exception as exc:

            print(
                "[CLEANUP] Could not restore Pico state:",
                repr(exc),
            )


        # ========================================================
        # Close connections
        # ========================================================

        try:

            client.close()

        except Exception:

            pass


        if postgres_conn is not None:

            try:

                postgres_conn.close()

            except Exception:

                pass


        print()
        print(
            "[CLEANUP] Connections closed."
        )


if __name__ == "__main__":

    main()
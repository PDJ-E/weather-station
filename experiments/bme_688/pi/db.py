"""
db.py

Weather Station
P3-06 — PostgreSQL persistence layer.

BOARD:
    Raspberry Pi 5

Database:
    weather_station_dev

Role:
    weather_station_app

Storage model:

For every environmental acquisition:

    sample_counter N
        |
        +-- DHT11 row
        |
        +-- BME688 row

Both rows are inserted in ONE PostgreSQL transaction.

If either insert fails:

    neither row is committed.

This guarantees that sensor-to-sensor comparison never receives
a half-written acquisition.
"""

from __future__ import annotations

import os

from pathlib import Path
from typing import Any

import psycopg

from dotenv import load_dotenv
from psycopg.types.json import Jsonb


# ================================================================
# Paths
# ================================================================

SCRIPT_DIR = (
    Path(__file__)
    .resolve()
    .parent
)

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)


# ================================================================
# Environment
#
# Search order:
#
# 1. experiments/bme_688/pi/.env
# 2. existing P1 experiments/dht_11/.env
#
# This means P3 works immediately with the existing secret.
# ================================================================

ENV_CANDIDATES = [

    SCRIPT_DIR
    / ".env",

    PROJECT_ROOT
    / "experiments"
    / "dht_11"
    / ".env",
]


ENV_FILE = None


for candidate in ENV_CANDIDATES:

    if candidate.exists():

        load_dotenv(
            candidate
        )

        ENV_FILE = candidate

        break


# ================================================================
# PostgreSQL configuration
# ================================================================

DB_HOST = "localhost"

DB_PORT = 5432

DB_NAME = "weather_station_dev"

DB_USER = "weather_station_app"


DB_PASSWORD = os.environ.get(
    "DB_PASSWORD"
)


if not DB_PASSWORD:

    searched = "\n".join(
        f"  - {path}"
        for path
        in ENV_CANDIDATES
    )

    raise RuntimeError(
        "DB_PASSWORD not found.\n"
        "Searched:\n"
        f"{searched}"
    )


# ================================================================
# Physical node
#
# P1 used:
#
#     pico-01-dht11
#
# The node now contains multiple environmental sensors, so the
# identifier describes the physical/environmental node instead
# of one specific sensor.
# ================================================================

NODE_ID = "pico-01-environment"


# ================================================================
# SQL
# ================================================================

CREATE_RUN_SQL = """

    INSERT INTO weather_test_run
        (
            run_name,
            phase,
            node_id,
            sample_period_s,
            register_map_version,
            notes
        )

    VALUES
        (
            %(run_name)s,
            %(phase)s,
            %(node_id)s,
            %(sample_period_s)s,
            %(register_map_version)s,
            %(notes)s
        )

    RETURNING id

"""


FINISH_RUN_SQL = """

    UPDATE weather_test_run

    SET
        ended_at_utc = now(),
        status = %(status)s,
        notes = CASE

            WHEN %(notes)s::text IS NULL
                THEN notes

            WHEN notes IS NULL
                THEN %(notes)s::text

            ELSE
                notes
                || E'\\n'
                || %(notes)s::text

        END

    WHERE id = %(run_id)s

"""


INSERT_RAW_SQL = """

    INSERT INTO weather_measurement_raw
        (
            run_id,
            node_id,
            sequence,
            uptime_ms,

            sensor_type,

            temperature_c,
            humidity_pct,

            pressure_hpa,
            gas_resistance_ohm,

            gas_valid,
            heater_stable,
            gas_ready,

            sensor_status,

            raw_payload
        )

    VALUES
        (
            %(run_id)s,
            %(node_id)s,
            %(sequence)s,
            %(uptime_ms)s,

            %(sensor_type)s,

            %(temperature_c)s,
            %(humidity_pct)s,

            %(pressure_hpa)s,
            %(gas_resistance_ohm)s,

            %(gas_valid)s,
            %(heater_stable)s,
            %(gas_ready)s,

            %(sensor_status)s,

            %(raw_payload)s
        )

    RETURNING id

"""


# ================================================================
# Connection
# ================================================================

def get_connection() -> psycopg.Connection:
    """
    Open PostgreSQL connection.

    autocommit=False intentionally.

    A complete environmental sample contains two rows
    (DHT11 + BME688), so they must commit atomically.
    """

    return psycopg.connect(

        host=DB_HOST,

        port=DB_PORT,

        dbname=DB_NAME,

        user=DB_USER,

        password=DB_PASSWORD,

        autocommit=False,
    )


# ================================================================
# Run lifecycle
# ================================================================

def create_test_run(
    conn: psycopg.Connection,
    *,
    run_name: str,
    sample_period_s: float,
    register_map_version: int,
    notes: str | None = None,
) -> int:
    """
    Create P3-06 run and return run_id.
    """

    with conn.cursor() as cur:

        cur.execute(

            CREATE_RUN_SQL,

            {
                "run_name":
                    run_name,

                "phase":
                    "P3-06",

                "node_id":
                    NODE_ID,

                "sample_period_s":
                    sample_period_s,

                "register_map_version":
                    register_map_version,

                "notes":
                    notes,
            },
        )


        row = cur.fetchone()


        if row is None:

            conn.rollback()

            raise RuntimeError(
                "weather_test_run INSERT "
                "did not return an id"
            )


        run_id = int(
            row[0]
        )


    conn.commit()


    return run_id


def finish_test_run(
    conn: psycopg.Connection,
    *,
    run_id: int,
    status: str,
    notes: str | None = None,
) -> None:
    """
    Close one long-run test.

    status:
        COMPLETED
        ABORTED
        FAILED
    """

    allowed = {
        "COMPLETED",
        "ABORTED",
        "FAILED",
    }


    if status not in allowed:

        raise ValueError(
            "Invalid final run status: "
            f"{status}"
        )


    try:

        with conn.cursor() as cur:

            cur.execute(

                FINISH_RUN_SQL,

                {
                    "run_id":
                        run_id,

                    "status":
                        status,

                    "notes":
                        notes,
                },
            )


        conn.commit()


    except Exception:

        conn.rollback()

        raise


# ================================================================
# Raw row builder
# ================================================================

def _build_raw_payload(
    *,
    sensor_name: str,
    sample: dict[str, Any],
    sensor: dict[str, Any],
) -> dict[str, Any]:
    """
    Preserve both decoded values and the Modbus/system context.

    This is useful if the normalized schema changes later:
    the original observation can still be reconstructed.
    """

    return {

        "phase":
            "P3-06",

        "sensor":
            sensor_name,

        "sequence":
            sample["sequence"],

        "uptime_ms":
            sample["uptime_ms"],

        "system": {

            "device_state":
                sample.get(
                    "device_state"
                ),

            "register_map_version":
                sample.get(
                    "register_map_version"
                ),

            "trigger_latency_ms":
                sample.get(
                    "trigger_latency_ms"
                ),

            "raw_registers":
                sample.get(
                    "raw_registers"
                ),
        },

        "measurement":
            sensor,
    }


# ================================================================
# Environmental sample insert
# ================================================================

def insert_environmental_sample(
    conn: psycopg.Connection,
    *,
    run_id: int,
    sample: dict[str, Any],
) -> tuple[int, int]:
    """
    Persist one complete acquisition.

    sample contract:

    {
        "sequence": 123,
        "uptime_ms": 456000,

        "device_state": 0,
        "register_map_version": 0x0101,
        "trigger_latency_ms": 437.2,

        "raw_registers": [...],

        "dht11": {
            "temperature_c": 31.0,
            "humidity_pct": 62.0,
            "status": "OK"
        },

        "bme688": {
            "temperature_c": 31.1,
            "humidity_pct": 66.5,
            "pressure_hpa": 1007.2,
            "gas_resistance_ohm": 43000,
            "status": "OK",
            "gas_valid": True,
            "heater_stable": True,
            "gas_ready": True
        }
    }

    Both raw rows are committed atomically.

    Returns:
        (dht_raw_id, bme_raw_id)
    """

    dht = sample[
        "dht11"
    ]

    bme = sample[
        "bme688"
    ]


    common = {

        "run_id":
            run_id,

        "node_id":
            NODE_ID,

        "sequence":
            int(
                sample["sequence"]
            ),

        "uptime_ms":
            int(
                sample["uptime_ms"]
            ),
    }


    dht_params = {

        **common,

        "sensor_type":
            "DHT11",

        "temperature_c":
            dht.get(
                "temperature_c"
            ),

        "humidity_pct":
            dht.get(
                "humidity_pct"
            ),

        "pressure_hpa":
            None,

        "gas_resistance_ohm":
            None,

        "gas_valid":
            None,

        "heater_stable":
            None,

        "gas_ready":
            None,

        "sensor_status":
            dht.get(
                "status",
                "NO_DATA",
            ),

        "raw_payload":
            Jsonb(
                _build_raw_payload(

                    sensor_name="DHT11",

                    sample=sample,

                    sensor=dht,
                )
            ),
    }


    bme_params = {

        **common,

        "sensor_type":
            "BME688",

        "temperature_c":
            bme.get(
                "temperature_c"
            ),

        "humidity_pct":
            bme.get(
                "humidity_pct"
            ),

        "pressure_hpa":
            bme.get(
                "pressure_hpa"
            ),

        "gas_resistance_ohm":
            bme.get(
                "gas_resistance_ohm"
            ),

        "gas_valid":
            bme.get(
                "gas_valid"
            ),

        "heater_stable":
            bme.get(
                "heater_stable"
            ),

        "gas_ready":
            bme.get(
                "gas_ready"
            ),

        "sensor_status":
            bme.get(
                "status",
                "NO_DATA",
            ),

        "raw_payload":
            Jsonb(
                _build_raw_payload(

                    sensor_name="BME688",

                    sample=sample,

                    sensor=bme,
                )
            ),
    }


    try:

        with conn.cursor() as cur:

            # ----------------------------------------------------
            # DHT11
            # ----------------------------------------------------

            cur.execute(
                INSERT_RAW_SQL,
                dht_params,
            )


            dht_row = (
                cur.fetchone()
            )


            if dht_row is None:

                raise RuntimeError(
                    "DHT11 INSERT "
                    "did not return raw id"
                )


            dht_raw_id = int(
                dht_row[0]
            )


            # ----------------------------------------------------
            # BME688
            # ----------------------------------------------------

            cur.execute(
                INSERT_RAW_SQL,
                bme_params,
            )


            bme_row = (
                cur.fetchone()
            )


            if bme_row is None:

                raise RuntimeError(
                    "BME688 INSERT "
                    "did not return raw id"
                )


            bme_raw_id = int(
                bme_row[0]
            )


        # --------------------------------------------------------
        # One atomic environmental sample
        # --------------------------------------------------------

        conn.commit()


        return (
            dht_raw_id,
            bme_raw_id,
        )


    except Exception:

        conn.rollback()

        raise


# ================================================================
# Health check
# ================================================================

def ping(
    conn: psycopg.Connection,
) -> bool:
    """
    Cheap PostgreSQL liveness check.
    """

    try:

        with conn.cursor() as cur:

            cur.execute(
                "SELECT 1"
            )

            row = (
                cur.fetchone()
            )


        return (
            row is not None
            and row[0] == 1
        )


    except Exception:

        return False
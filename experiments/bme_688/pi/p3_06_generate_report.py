"""
p3_06_generate_report.py

Weather Station
P3-06 — Consolidated long-run validation report.

BOARD:
    Raspberry Pi 5

Purpose:
    Generate ONE text report containing everything needed to evaluate
    the most recent P3-06-LONG run.

Sources:
    1. PostgreSQL weather_station_dev
    2. Local P3-06 JSONL audit log
    3. Local P3-06 error log

No Modbus communication is performed.
The Pico does not need to be connected or running.

Output:
    experiments/bme_688/pi/results/
        p3_06_consolidated_report_<run timestamp>.txt
"""

from __future__ import annotations

import json
import re
import statistics
import sys

from collections import Counter
from pathlib import Path
from typing import Any


# ================================================================
# Local db module
# ================================================================

SCRIPT_DIR = Path(__file__).resolve().parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(SCRIPT_DIR),
    )

import db


# ================================================================
# Paths
# ================================================================

RESULTS_DIR = SCRIPT_DIR / "results"

RESULTS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ================================================================
# Helpers
# ================================================================

def fmt_num(
    value: Any,
    digits: int = 3,
) -> str:

    if value is None:
        return "N/A"

    try:
        return f"{float(value):.{digits}f}"

    except (TypeError, ValueError):
        return str(value)


def fmt_int(
    value: Any,
) -> str:

    if value is None:
        return "N/A"

    return str(
        int(value)
    )


def percent(
    numerator: int,
    denominator: int,
) -> str:

    if denominator == 0:
        return "N/A"

    return (
        f"{100.0 * numerator / denominator:.4f}%"
    )


def scalar(
    conn,
    sql: str,
    params=(),
):

    with conn.cursor() as cur:

        cur.execute(
            sql,
            params,
        )

        row = cur.fetchone()

    if row is None:
        return None

    return row[0]


def fetchone(
    conn,
    sql: str,
    params=(),
):

    with conn.cursor() as cur:

        cur.execute(
            sql,
            params,
        )

        return cur.fetchone()


def fetchall(
    conn,
    sql: str,
    params=(),
):

    with conn.cursor() as cur:

        cur.execute(
            sql,
            params,
        )

        return cur.fetchall()


# ================================================================
# Report writer
# ================================================================

class Report:

    def __init__(self):

        self.lines: list[str] = []


    def add(
        self,
        text: str = "",
    ) -> None:

        self.lines.append(
            str(text)
        )


    def section(
        self,
        title: str,
    ) -> None:

        self.add()
        self.add(
            "------------------------------------------------------------"
        )
        self.add(
            f" {title}"
        )
        self.add(
            "------------------------------------------------------------"
        )


    def render(
        self,
    ) -> str:

        return "\n".join(
            self.lines
        )


# ================================================================
# Find latest P3-06 long run
# ================================================================

LATEST_RUN_SQL = """

SELECT

    id,
    run_name,
    phase,
    node_id,
    started_at_utc,
    ended_at_utc,
    status,
    sample_period_s,
    register_map_version,
    notes

FROM weather_test_run

WHERE run_name LIKE 'P3-06-LONG-%%'

ORDER BY started_at_utc DESC

LIMIT 1

"""


# ================================================================
# Local audit file detection
# ================================================================

def get_run_file_stamp(
    run_name: str,
) -> str | None:

    prefix = "P3-06-LONG-"

    if not run_name.startswith(
        prefix
    ):
        return None

    value = run_name[
        len(prefix):
    ]

    # run:
    # 20260830-182500
    #
    # file:
    # 20260830_182500

    if "-" not in value:
        return None

    date_part, time_part = (
        value.split(
            "-",
            1,
        )
    )

    return (
        f"{date_part}_{time_part}"
    )


# ================================================================
# Parse JSONL
# ================================================================

def parse_audit_jsonl(
    path: Path,
) -> dict[str, Any]:

    result = {

        "exists":
            path.exists(),

        "valid_lines":
            0,

        "invalid_lines":
            0,

        "first_timestamp":
            None,

        "last_timestamp":
            None,

        "first_sequence":
            None,

        "last_sequence":
            None,

        "latencies":
            [],

        "gas_ready":
            0,

        "gas_not_ready":
            0,

        "gas_invalid":
            0,

        "heater_unstable":
            0,

        "dht_non_ok":
            0,

        "bme_non_ok":
            0,

        "warmup_complete_first_sequence":
            None,
    }


    if not path.exists():

        return result


    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = (
                line.strip()
            )

            if not line:
                continue


            try:

                payload = json.loads(
                    line
                )

            except json.JSONDecodeError:

                result[
                    "invalid_lines"
                ] += 1

                continue


            result[
                "valid_lines"
            ] += 1


            timestamp = (
                payload.get(
                    "timestamp_utc"
                )
            )


            sequence = (
                payload.get(
                    "sequence"
                )
            )


            latency = (
                payload.get(
                    "trigger_latency_ms"
                )
            )


            if (
                result[
                    "first_timestamp"
                ]
                is None
            ):

                result[
                    "first_timestamp"
                ] = timestamp


            result[
                "last_timestamp"
            ] = timestamp


            if (
                result[
                    "first_sequence"
                ]
                is None
            ):

                result[
                    "first_sequence"
                ] = sequence


            result[
                "last_sequence"
            ] = sequence


            if latency is not None:

                try:

                    result[
                        "latencies"
                    ].append(
                        float(
                            latency
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    pass


            dht = (
                payload.get(
                    "dht11",
                    {},
                )
            )


            bme = (
                payload.get(
                    "bme688",
                    {},
                )
            )


            warmup = (
                payload.get(
                    "warmup",
                    {},
                )
            )


            if (
                dht.get(
                    "status"
                )
                != "OK"
            ):

                result[
                    "dht_non_ok"
                ] += 1


            if (
                bme.get(
                    "status"
                )
                != "OK"
            ):

                result[
                    "bme_non_ok"
                ] += 1


            if not bme.get(
                "gas_valid",
                False,
            ):

                result[
                    "gas_invalid"
                ] += 1


            if not bme.get(
                "heater_stable",
                False,
            ):

                result[
                    "heater_unstable"
                ] += 1


            if bme.get(
                "gas_ready",
                False,
            ):

                result[
                    "gas_ready"
                ] += 1

            else:

                result[
                    "gas_not_ready"
                ] += 1


            if (
                warmup.get(
                    "warmup_complete"
                )
                and result[
                    "warmup_complete_first_sequence"
                ]
                is None
            ):

                result[
                    "warmup_complete_first_sequence"
                ] = sequence


    return result


# ================================================================
# Parse error log
# ================================================================

ERROR_EVENT_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}T[^\]]+)\]\s+(.*)$"
)


def parse_error_log(
    path: Path,
) -> dict[str, Any]:

    result = {

        "exists":
            path.exists(),

        "events":
            [],

        "counts":
            Counter(),
    }


    if not path.exists():

        return result


    with path.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as file:

        for line in file:

            match = (
                ERROR_EVENT_PATTERN
                .match(
                    line.strip()
                )
            )


            if match is None:
                continue


            timestamp = (
                match.group(1)
            )

            message = (
                match.group(2)
            )


            result[
                "events"
            ].append(
                (
                    timestamp,
                    message,
                )
            )


            result[
                "counts"
            ][message] += 1


    return result


# ================================================================
# Main
# ================================================================

def main():

    report = Report()


    print()
    print(
        "Connecting to PostgreSQL..."
    )


    conn = (
        db.get_connection()
    )


    try:

        run = fetchone(
            conn,
            LATEST_RUN_SQL,
        )


        if run is None:

            raise RuntimeError(
                "No P3-06-LONG run found in weather_test_run."
            )


        (
            run_id,
            run_name,
            phase,
            node_id,
            started_at,
            ended_at,
            run_status,
            sample_period_s,
            register_map_version,
            run_notes,
        ) = run


        # ========================================================
        # Run filenames
        # ========================================================

        file_stamp = (
            get_run_file_stamp(
                run_name
            )
        )


        if file_stamp is None:

            raise RuntimeError(
                f"Could not derive file timestamp from {run_name}"
            )


        audit_path = (

            RESULTS_DIR
            / (
                "p3_06_long_run_"
                f"{file_stamp}.jsonl"
            )
        )


        error_path = (

            RESULTS_DIR
            / (
                "p3_06_long_run_errors_"
                f"{file_stamp}.log"
            )
        )


        output_path = (

            RESULTS_DIR
            / (
                "p3_06_consolidated_report_"
                f"{file_stamp}.txt"
            )
        )


        # ========================================================
        # Basic run duration
        # ========================================================

        duration_seconds = None


        if (
            started_at is not None
            and ended_at is not None
        ):

            duration_seconds = (

                ended_at
                - started_at

            ).total_seconds()


        # ========================================================
        # 1. Raw storage
        # ========================================================

        raw = fetchone(
            conn,
            """

            SELECT

                COUNT(*),

                COUNT(*) FILTER (
                    WHERE sensor_type = 'DHT11'
                ),

                COUNT(*) FILTER (
                    WHERE sensor_type = 'BME688'
                ),

                COUNT(DISTINCT sequence),

                MIN(sequence),

                MAX(sequence),

                MIN(received_at_utc),

                MAX(received_at_utc)

            FROM weather_measurement_raw

            WHERE run_id = %s

            """,
            (
                run_id,
            ),
        )


        (
            total_raw_rows,
            dht_rows,
            bme_rows,
            distinct_sequences,
            first_sequence,
            last_sequence,
            first_measurement,
            last_measurement,
        ) = raw


        measurement_duration_s = None


        if (
            first_measurement is not None
            and last_measurement is not None
        ):

            measurement_duration_s = (

                last_measurement
                - first_measurement

            ).total_seconds()


        # ========================================================
        # 2. Paired samples
        # ========================================================

        paired_samples = int(
            scalar(
                conn,
                """

                SELECT COUNT(*)

                FROM weather_p3_sensor_comparison

                WHERE run_id = %s

                """,
                (
                    run_id,
                ),
            )
            or 0
        )


        # ========================================================
        # 3. Incomplete acquisitions
        # ========================================================

        incomplete = fetchall(
            conn,
            """

            SELECT

                sequence,

                COUNT(*) AS rows_found,

                STRING_AGG(
                    sensor_type,
                    ', '
                    ORDER BY sensor_type
                )

            FROM weather_measurement_raw

            WHERE run_id = %s

            GROUP BY sequence

            HAVING COUNT(*) <> 2

            ORDER BY sequence

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 4. Sequence gaps
        # ========================================================

        sequence_gaps = fetchall(
            conn,
            """

            WITH seq AS (

                SELECT DISTINCT
                    sequence

                FROM weather_measurement_raw

                WHERE run_id = %s

            ),

            ordered AS (

                SELECT

                    sequence,

                    LAG(sequence) OVER (
                        ORDER BY sequence
                    ) AS previous_sequence

                FROM seq

            )

            SELECT

                previous_sequence,

                sequence,

                sequence - previous_sequence,

                sequence - previous_sequence - 1

            FROM ordered

            WHERE
                previous_sequence IS NOT NULL

                AND sequence
                    <> previous_sequence + 1

            ORDER BY sequence

            """,
            (
                run_id,
            ),
        )


        missing_sequence_count = sum(

            int(row[3])

            for row
            in sequence_gaps

            if row[3] is not None
        )


        # ========================================================
        # 5. Status counts
        # ========================================================

        status_rows = fetchall(
            conn,
            """

            SELECT

                sensor_type,
                sensor_status,
                COUNT(*)

            FROM weather_measurement_raw

            WHERE run_id = %s

            GROUP BY
                sensor_type,
                sensor_status

            ORDER BY
                sensor_type,
                sensor_status

            """,
            (
                run_id,
            ),
        )


        status_counts = {

            (
                sensor_type,
                sensor_status
            ):
                int(count)

            for (
                sensor_type,
                sensor_status,
                count,
            )
            in status_rows
        }


        dht_non_ok_db = sum(

            count

            for (
                sensor,
                status
            ),
            count
            in status_counts.items()

            if (
                sensor == "DHT11"
                and status != "OK"
            )
        )


        bme_non_ok_db = sum(

            count

            for (
                sensor,
                status
            ),
            count
            in status_counts.items()

            if (
                sensor == "BME688"
                and status != "OK"
            )
        )


        # ========================================================
        # 6. Gas quality
        # ========================================================

        gas_quality = fetchone(
            conn,
            """

            SELECT

                COUNT(*),

                COUNT(*) FILTER (
                    WHERE gas_valid IS TRUE
                ),

                COUNT(*) FILTER (
                    WHERE gas_valid IS FALSE
                ),

                COUNT(*) FILTER (
                    WHERE heater_stable IS TRUE
                ),

                COUNT(*) FILTER (
                    WHERE heater_stable IS FALSE
                ),

                COUNT(*) FILTER (
                    WHERE gas_ready IS TRUE
                ),

                COUNT(*) FILTER (
                    WHERE gas_ready IS FALSE
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
            total_bme,
            gas_valid_count,
            gas_invalid_count,
            heater_stable_count,
            heater_unstable_count,
            gas_ready_count,
            gas_not_ready_count,
        ) = gas_quality


        # ========================================================
        # 7. DHT statistics
        # ========================================================

        dht_stats = fetchone(
            conn,
            """

            SELECT

                MIN(temperature_c),
                AVG(temperature_c),
                MAX(temperature_c),
                STDDEV_SAMP(temperature_c),

                MIN(humidity_pct),
                AVG(humidity_pct),
                MAX(humidity_pct),
                STDDEV_SAMP(humidity_pct)

            FROM weather_measurement_raw

            WHERE
                run_id = %s
                AND sensor_type = 'DHT11'

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 8. BME statistics
        # ========================================================

        bme_stats = fetchone(
            conn,
            """

            SELECT

                MIN(temperature_c),
                AVG(temperature_c),
                MAX(temperature_c),
                STDDEV_SAMP(temperature_c),

                MIN(humidity_pct),
                AVG(humidity_pct),
                MAX(humidity_pct),
                STDDEV_SAMP(humidity_pct),

                MIN(pressure_hpa),
                AVG(pressure_hpa),
                MAX(pressure_hpa),
                STDDEV_SAMP(pressure_hpa),

                MIN(gas_resistance_ohm),
                AVG(gas_resistance_ohm),
                MAX(gas_resistance_ohm),
                STDDEV_SAMP(gas_resistance_ohm)

            FROM weather_measurement_raw

            WHERE
                run_id = %s
                AND sensor_type = 'BME688'

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 9. DHT vs BME
        # ========================================================

        comparison = fetchone(
            conn,
            """

            SELECT

                AVG(temperature_diff_c),

                AVG(
                    ABS(
                        temperature_diff_c
                    )
                ),

                MAX(
                    ABS(
                        temperature_diff_c
                    )
                ),

                AVG(humidity_diff_pct),

                AVG(
                    ABS(
                        humidity_diff_pct
                    )
                ),

                MAX(
                    ABS(
                        humidity_diff_pct
                    )
                )

            FROM weather_p3_sensor_comparison

            WHERE run_id = %s

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 10. Sampling cadence
        # ========================================================

        cadence = fetchone(
            conn,
            """

            WITH samples AS (

                SELECT

                    sequence,

                    dht_received_at_utc
                        AS ts

                FROM weather_p3_sensor_comparison

                WHERE run_id = %s

            ),

            delta AS (

                SELECT

                    sequence,

                    EXTRACT(
                        EPOCH FROM (
                            ts
                            - LAG(ts) OVER (
                                ORDER BY sequence
                            )
                        )
                    ) AS delta_s

                FROM samples

            )

            SELECT

                COUNT(delta_s),

                MIN(delta_s),

                AVG(delta_s),

                MAX(delta_s),

                STDDEV_SAMP(delta_s),

                COUNT(*) FILTER (
                    WHERE delta_s > 6
                ),

                COUNT(*) FILTER (
                    WHERE delta_s > 10
                ),

                COUNT(*) FILTER (
                    WHERE delta_s > 30
                )

            FROM delta

            """,
            (
                run_id,
            ),
        )


        (
            cadence_intervals,
            cadence_min,
            cadence_avg,
            cadence_max,
            cadence_stddev,
            cadence_over_6,
            cadence_over_10,
            cadence_over_30,
        ) = cadence


        # ========================================================
        # 11. Largest gaps
        # ========================================================

        largest_intervals = fetchall(
            conn,
            """

            WITH samples AS (

                SELECT

                    sequence,

                    dht_received_at_utc
                        AS ts

                FROM weather_p3_sensor_comparison

                WHERE run_id = %s

            ),

            delta AS (

                SELECT

                    sequence,

                    ts,

                    LAG(sequence) OVER (
                        ORDER BY sequence
                    ) AS previous_sequence,

                    LAG(ts) OVER (
                        ORDER BY sequence
                    ) AS previous_ts

                FROM samples

            )

            SELECT

                previous_sequence,

                sequence,

                previous_ts,

                ts,

                EXTRACT(
                    EPOCH FROM (
                        ts - previous_ts
                    )
                )

            FROM delta

            WHERE previous_ts IS NOT NULL

            ORDER BY 5 DESC

            LIMIT 20

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 12. Hourly summary
        # ========================================================

        hourly = fetchall(
            conn,
            """

            SELECT

                DATE_TRUNC(
                    'hour',
                    bme_received_at_utc
                ) AS hour,

                COUNT(*),

                AVG(dht_temperature_c),

                AVG(bme_temperature_c),

                AVG(temperature_diff_c),

                AVG(dht_humidity_pct),

                AVG(bme_humidity_pct),

                AVG(humidity_diff_pct),

                AVG(pressure_hpa),

                MIN(gas_resistance_ohm),

                AVG(gas_resistance_ohm),

                MAX(gas_resistance_ohm)

            FROM weather_p3_sensor_comparison

            WHERE run_id = %s

            GROUP BY 1

            ORDER BY 1

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # 13. Stable gas channel
        # ========================================================

        stable_gas = fetchone(
            conn,
            """

            SELECT

                COUNT(*),

                MIN(gas_resistance_ohm),

                AVG(gas_resistance_ohm),

                MAX(gas_resistance_ohm),

                STDDEV_SAMP(
                    gas_resistance_ohm
                )

            FROM weather_p3_sensor_comparison

            WHERE
                run_id = %s
                AND gas_ready IS TRUE

            """,
            (
                run_id,
            ),
        )


        # ========================================================
        # Local audit files
        # ========================================================

        audit = (
            parse_audit_jsonl(
                audit_path
            )
        )


        errors = (
            parse_error_log(
                error_path
            )
        )


        # ========================================================
        # Automatic evaluation
        # ========================================================

        hard_failures: list[str] = []

        warnings: list[str] = []


        if run_status != "COMPLETED":

            hard_failures.append(
                f"Run status is {run_status}, not COMPLETED."
            )


        if dht_rows != bme_rows:

            hard_failures.append(
                "DHT11 and BME688 raw row counts do not match."
            )


        if paired_samples != dht_rows:

            hard_failures.append(
                "Not every DHT11/BME688 acquisition produced a complete pair."
            )


        if incomplete:

            hard_failures.append(
                f"{len(incomplete)} incomplete acquisition(s) detected."
            )


        if sequence_gaps:

            hard_failures.append(
                f"{len(sequence_gaps)} sequence gap(s), "
                f"{missing_sequence_count} missing sample(s)."
            )


        if (
            duration_seconds is not None
            and duration_seconds < 23 * 3600
        ):

            warnings.append(
                "Recorded run duration is below 23 hours."
            )


        if bme_non_ok_db > 0:

            bme_error_pct = (

                100.0
                * bme_non_ok_db
                / total_bme

                if total_bme

                else 0.0
            )


            if bme_error_pct >= 1.0:

                hard_failures.append(
                    "BME688 non-OK sensor status occurred "
                    f"in {bme_error_pct:.3f}% of samples."
                )

            else:

                warnings.append(
                    f"BME688 had {bme_non_ok_db} isolated "
                    "non-OK sample(s)."
                )


        if dht_non_ok_db > 0:

            warnings.append(
                f"DHT11 had {dht_non_ok_db} non-OK sample(s)."
            )


        if gas_invalid_count > 0:

            warnings.append(
                f"{gas_invalid_count} BME688 sample(s) "
                "had gas_valid=False."
            )


        if heater_unstable_count > 1:

            warnings.append(
                f"{heater_unstable_count} BME688 sample(s) "
                "had heater_stable=False."
            )


        if cadence_over_30 > 0:

            warnings.append(
                f"{cadence_over_30} sampling interval(s) "
                "were longer than 30 seconds."
            )


        if errors["events"]:

            warnings.append(
                f"{len(errors['events'])} runner error event(s) "
                "were recorded in the local error log."
            )


        if audit["exists"]:

            if (
                audit["valid_lines"]
                != paired_samples
            ):

                warnings.append(
                    "JSONL acquisition count does not exactly match "
                    "the PostgreSQL paired-sample count."
                )

        else:

            warnings.append(
                "Matching JSONL audit file was not found."
            )


        if hard_failures:

            verdict = "FAIL"

        elif warnings:

            verdict = "PASS WITH WARNINGS"

        else:

            verdict = "PASS"


        # ========================================================
        # Build report
        # ========================================================

        report.add(
            "============================================================"
        )

        report.add(
            " P3-06 LONG-DURATION CONSOLIDATED REPORT"
        )

        report.add(
            "============================================================"
        )

        report.add()

        report.add(
            f"FINAL VERDICT: {verdict}"
        )


        # ========================================================
        # Run
        # ========================================================

        report.section(
            "RUN INFORMATION"
        )


        report.add(
            f"Run ID                 : {run_id}"
        )

        report.add(
            f"Run name               : {run_name}"
        )

        report.add(
            f"Phase                  : {phase}"
        )

        report.add(
            f"Node                   : {node_id}"
        )

        report.add(
            f"Status                 : {run_status}"
        )

        report.add(
            f"Started                : {started_at}"
        )

        report.add(
            f"Ended                  : {ended_at}"
        )

        report.add(
            f"Run duration           : "
            f"{fmt_num(duration_seconds / 3600 if duration_seconds else None, 4)} h"
        )

        report.add(
            f"Measurement duration   : "
            f"{fmt_num(measurement_duration_s / 3600 if measurement_duration_s else None, 4)} h"
        )

        report.add(
            f"Configured period      : {sample_period_s} s"
        )

        report.add(
            f"Register Map           : 0x{register_map_version:04X}"
        )

        report.add(
            f"Notes                  : {run_notes}"
        )


        # ========================================================
        # Integrity
        # ========================================================

        report.section(
            "STORAGE / DATA INTEGRITY"
        )


        report.add(
            f"Total raw rows         : {total_raw_rows}"
        )

        report.add(
            f"DHT11 rows             : {dht_rows}"
        )

        report.add(
            f"BME688 rows            : {bme_rows}"
        )

        report.add(
            f"Paired samples         : {paired_samples}"
        )

        report.add(
            f"Distinct sequences     : {distinct_sequences}"
        )

        report.add(
            f"First sequence         : {first_sequence}"
        )

        report.add(
            f"Last sequence          : {last_sequence}"
        )

        report.add(
            f"Incomplete pairs       : {len(incomplete)}"
        )

        report.add(
            f"Sequence gaps          : {len(sequence_gaps)}"
        )

        report.add(
            f"Missing samples        : {missing_sequence_count}"
        )


        if incomplete:

            report.add()
            report.add(
                "Incomplete acquisitions:"
            )

            for row in incomplete[:20]:

                report.add(
                    f"  seq={row[0]} rows={row[1]} sensors={row[2]}"
                )


        if sequence_gaps:

            report.add()
            report.add(
                "Sequence gaps:"
            )

            for (
                previous_seq,
                sequence,
                jump,
                missing,
            ) in sequence_gaps[:20]:

                report.add(
                    f"  {previous_seq} -> {sequence} "
                    f"(jump={jump}, missing={missing})"
                )


        # ========================================================
        # Status
        # ========================================================

        report.section(
            "SENSOR STATUS"
        )


        for (
            sensor_type,
            sensor_status,
            count,
        ) in status_rows:

            report.add(
                f"{sensor_type:<10} {sensor_status:<15}: {count}"
            )


        # ========================================================
        # Gas
        # ========================================================

        report.section(
            "BME688 GAS / HEATER QUALITY"
        )


        report.add(
            f"Total BME samples      : {total_bme}"
        )

        report.add(
            f"Gas valid              : "
            f"{gas_valid_count} "
            f"({percent(gas_valid_count, total_bme)})"
        )

        report.add(
            f"Gas invalid            : "
            f"{gas_invalid_count} "
            f"({percent(gas_invalid_count, total_bme)})"
        )

        report.add(
            f"Heater stable          : "
            f"{heater_stable_count} "
            f"({percent(heater_stable_count, total_bme)})"
        )

        report.add(
            f"Heater unstable        : "
            f"{heater_unstable_count} "
            f"({percent(heater_unstable_count, total_bme)})"
        )

        report.add(
            f"Gas ready              : "
            f"{gas_ready_count} "
            f"({percent(gas_ready_count, total_bme)})"
        )

        report.add(
            f"Warmup/not-ready       : {gas_not_ready_count}"
        )


        # ========================================================
        # DHT
        # ========================================================

        report.section(
            "DHT11 STATISTICS"
        )


        (
            dht_t_min,
            dht_t_avg,
            dht_t_max,
            dht_t_std,

            dht_h_min,
            dht_h_avg,
            dht_h_max,
            dht_h_std,

        ) = dht_stats


        report.add(
            f"Temperature min        : {fmt_num(dht_t_min)} C"
        )

        report.add(
            f"Temperature mean       : {fmt_num(dht_t_avg)} C"
        )

        report.add(
            f"Temperature max        : {fmt_num(dht_t_max)} C"
        )

        report.add(
            f"Temperature stdev      : {fmt_num(dht_t_std)} C"
        )

        report.add(
            f"Humidity min           : {fmt_num(dht_h_min)} %"
        )

        report.add(
            f"Humidity mean          : {fmt_num(dht_h_avg)} %"
        )

        report.add(
            f"Humidity max           : {fmt_num(dht_h_max)} %"
        )

        report.add(
            f"Humidity stdev         : {fmt_num(dht_h_std)} %"
        )


        # ========================================================
        # BME
        # ========================================================

        report.section(
            "BME688 STATISTICS"
        )


        (
            bme_t_min,
            bme_t_avg,
            bme_t_max,
            bme_t_std,

            bme_h_min,
            bme_h_avg,
            bme_h_max,
            bme_h_std,

            pressure_min,
            pressure_avg,
            pressure_max,
            pressure_std,

            gas_min,
            gas_avg,
            gas_max,
            gas_std,

        ) = bme_stats


        report.add(
            f"Temperature min        : {fmt_num(bme_t_min)} C"
        )

        report.add(
            f"Temperature mean       : {fmt_num(bme_t_avg)} C"
        )

        report.add(
            f"Temperature max        : {fmt_num(bme_t_max)} C"
        )

        report.add(
            f"Temperature stdev      : {fmt_num(bme_t_std)} C"
        )

        report.add(
            f"Humidity min           : {fmt_num(bme_h_min)} %"
        )

        report.add(
            f"Humidity mean          : {fmt_num(bme_h_avg)} %"
        )

        report.add(
            f"Humidity max           : {fmt_num(bme_h_max)} %"
        )

        report.add(
            f"Humidity stdev         : {fmt_num(bme_h_std)} %"
        )

        report.add(
            f"Pressure min           : {fmt_num(pressure_min)} hPa"
        )

        report.add(
            f"Pressure mean          : {fmt_num(pressure_avg)} hPa"
        )

        report.add(
            f"Pressure max           : {fmt_num(pressure_max)} hPa"
        )

        report.add(
            f"Pressure stdev         : {fmt_num(pressure_std, 4)} hPa"
        )

        report.add(
            f"Gas resistance min     : {fmt_num(gas_min, 1)} ohm"
        )

        report.add(
            f"Gas resistance mean    : {fmt_num(gas_avg, 1)} ohm"
        )

        report.add(
            f"Gas resistance max     : {fmt_num(gas_max, 1)} ohm"
        )

        report.add(
            f"Gas resistance stdev   : {fmt_num(gas_std, 1)} ohm"
        )


        # ========================================================
        # Comparison
        # ========================================================

        report.section(
            "DHT11 vs BME688"
        )


        (
            temp_bias,
            temp_mae,
            temp_max_diff,
            humidity_bias,
            humidity_mae,
            humidity_max_diff,

        ) = comparison


        report.add(
            "Difference convention   : DHT11 - BME688"
        )

        report.add(
            f"Temperature bias       : {fmt_num(temp_bias)} C"
        )

        report.add(
            f"Temperature MAE        : {fmt_num(temp_mae)} C"
        )

        report.add(
            f"Temperature max |diff| : {fmt_num(temp_max_diff)} C"
        )

        report.add(
            f"Humidity bias          : {fmt_num(humidity_bias)} pp"
        )

        report.add(
            f"Humidity MAE           : {fmt_num(humidity_mae)} pp"
        )

        report.add(
            f"Humidity max |diff|    : {fmt_num(humidity_max_diff)} pp"
        )


        # ========================================================
        # Cadence
        # ========================================================

        report.section(
            "SAMPLING CADENCE"
        )


        report.add(
            f"Intervals              : {cadence_intervals}"
        )

        report.add(
            f"Minimum                : {fmt_num(cadence_min, 4)} s"
        )

        report.add(
            f"Mean                   : {fmt_num(cadence_avg, 4)} s"
        )

        report.add(
            f"Maximum                : {fmt_num(cadence_max, 4)} s"
        )

        report.add(
            f"Stddev                 : {fmt_num(cadence_stddev, 4)} s"
        )

        report.add(
            f"Intervals > 6 s        : {cadence_over_6}"
        )

        report.add(
            f"Intervals > 10 s       : {cadence_over_10}"
        )

        report.add(
            f"Intervals > 30 s       : {cadence_over_30}"
        )


        report.add()
        report.add(
            "20 largest intervals:"
        )


        for row in largest_intervals:

            (
                prev_seq,
                seq,
                prev_ts,
                ts,
                interval_s,
            ) = row


            report.add(
                f"  {prev_seq} -> {seq}: "
                f"{fmt_num(interval_s, 4)} s "
                f"({prev_ts} -> {ts})"
            )


        # ========================================================
        # Stable gas
        # ========================================================

        report.section(
            "STABLE GAS CHANNEL — gas_ready = TRUE"
        )


        (
            stable_gas_count,
            stable_gas_min,
            stable_gas_avg,
            stable_gas_max,
            stable_gas_std,

        ) = stable_gas


        report.add(
            f"Samples                : {stable_gas_count}"
        )

        report.add(
            f"Minimum                : {fmt_num(stable_gas_min, 1)} ohm"
        )

        report.add(
            f"Mean                   : {fmt_num(stable_gas_avg, 1)} ohm"
        )

        report.add(
            f"Maximum                : {fmt_num(stable_gas_max, 1)} ohm"
        )

        report.add(
            f"Stddev                 : {fmt_num(stable_gas_std, 1)} ohm"
        )


        # ========================================================
        # Reconstructed health summary from JSONL
        # ========================================================

        report.section(
            "LOCAL JSONL / RECONSTRUCTED RUNNER HEALTH"
        )


        report.add(
            f"Audit file             : {audit_path}"
        )

        report.add(
            f"Audit file exists      : {audit['exists']}"
        )

        report.add(
            f"Valid JSONL samples    : {audit['valid_lines']}"
        )

        report.add(
            f"Invalid JSONL lines    : {audit['invalid_lines']}"
        )

        report.add(
            f"First sequence         : {audit['first_sequence']}"
        )

        report.add(
            f"Last sequence          : {audit['last_sequence']}"
        )

        report.add(
            f"First timestamp        : {audit['first_timestamp']}"
        )

        report.add(
            f"Last timestamp         : {audit['last_timestamp']}"
        )

        report.add(
            f"DHT non-OK             : {audit['dht_non_ok']}"
        )

        report.add(
            f"BME non-OK             : {audit['bme_non_ok']}"
        )

        report.add(
            f"Gas invalid            : {audit['gas_invalid']}"
        )

        report.add(
            f"Heater unstable        : {audit['heater_unstable']}"
        )

        report.add(
            f"Gas ready              : {audit['gas_ready']}"
        )

        report.add(
            f"Gas not ready          : {audit['gas_not_ready']}"
        )

        report.add(
            "Warmup completed at seq: "
            f"{audit['warmup_complete_first_sequence']}"
        )


        latencies = (
            audit[
                "latencies"
            ]
        )


        if latencies:

            report.add(
                f"READ_NOW latency mean  : "
                f"{statistics.mean(latencies):.3f} ms"
            )

            report.add(
                f"READ_NOW latency min   : "
                f"{min(latencies):.3f} ms"
            )

            report.add(
                f"READ_NOW latency max   : "
                f"{max(latencies):.3f} ms"
            )

            if len(latencies) >= 2:

                report.add(
                    f"READ_NOW latency stdev : "
                    f"{statistics.stdev(latencies):.3f} ms"
                )


        # ========================================================
        # Error log
        # ========================================================

        report.section(
            "RUNNER ERROR LOG"
        )


        report.add(
            f"Error log              : {error_path}"
        )

        report.add(
            f"Error log exists       : {errors['exists']}"
        )

        report.add(
            f"Recorded error events  : {len(errors['events'])}"
        )


        if errors[
            "counts"
        ]:

            report.add()
            report.add(
                "Error event counts:"
            )


            for (
                message,
                count,
            ) in errors[
                "counts"
            ].most_common():

                report.add(
                    f"  {count:5d}  {message}"
                )


        if errors[
            "events"
        ]:

            report.add()
            report.add(
                "First 20 error events:"
            )


            for (
                timestamp,
                message,
            ) in errors[
                "events"
            ][:20]:

                report.add(
                    f"  {timestamp}  {message}"
                )


        # ========================================================
        # Hourly
        # ========================================================

        report.section(
            "HOURLY ENVIRONMENTAL SUMMARY"
        )


        report.add(
            "hour | samples | DHT T | BME T | dT | "
            "DHT RH | BME RH | dRH | pressure | "
            "gas min | gas avg | gas max"
        )


        for row in hourly:

            (
                hour,
                samples,

                dht_temp,
                bme_temp,
                delta_temp,

                dht_hum,
                bme_hum,
                delta_hum,

                pressure,

                gas_hour_min,
                gas_hour_avg,
                gas_hour_max,

            ) = row


            report.add(

                f"{hour} | "
                f"{samples} | "

                f"{fmt_num(dht_temp, 2)} | "
                f"{fmt_num(bme_temp, 2)} | "
                f"{fmt_num(delta_temp, 2)} | "

                f"{fmt_num(dht_hum, 2)} | "
                f"{fmt_num(bme_hum, 2)} | "
                f"{fmt_num(delta_hum, 2)} | "

                f"{fmt_num(pressure, 2)} | "

                f"{fmt_num(gas_hour_min, 0)} | "
                f"{fmt_num(gas_hour_avg, 0)} | "
                f"{fmt_num(gas_hour_max, 0)}"
            )


        # ========================================================
        # Verdict
        # ========================================================

        report.section(
            "AUTOMATIC VALIDATION"
        )


        report.add(
            f"VERDICT: {verdict}"
        )


        report.add()


        if hard_failures:

            report.add(
                "Hard failures:"
            )


            for item in hard_failures:

                report.add(
                    f"  - {item}"
                )

        else:

            report.add(
                "Hard failures: NONE"
            )


        report.add()


        if warnings:

            report.add(
                "Warnings:"
            )


            for item in warnings:

                report.add(
                    f"  - {item}"
                )

        else:

            report.add(
                "Warnings: NONE"
            )


        report.add()

        report.add(
            "============================================================"
        )

        report.add(
            f" FINAL VERDICT: {verdict}"
        )

        report.add(
            "============================================================"
        )


        # ========================================================
        # Write consolidated file
        # ========================================================

        output_path.write_text(

            report.render(),

            encoding="utf-8",
        )


        print()
        print(
            "============================================================"
        )

        print(
            " P3-06 CONSOLIDATED REPORT CREATED"
        )

        print(
            "============================================================"
        )

        print()

        print(
            "Run:",
            run_name,
        )

        print(
            "Verdict:",
            verdict,
        )

        print()

        print(
            "Report:"
        )

        print(
            output_path
        )

        print()


    finally:

        conn.close()


if __name__ == "__main__":

    main()
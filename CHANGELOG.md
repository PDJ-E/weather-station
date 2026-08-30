# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/) once the project reaches 1.0.0. While the project is on a `0.x` version, minor releases (`0.X.0`) may include breaking changes as the architecture is still being established.

## [0.7.0] - 2026-08-30

### Added

- Modbus RTU Register Map v1.0 (`docs/Phase 2/p2_modbus_register_map_v1.md`), the canonical Modbus contract that replaces the P1 text/JSON UART interface: Coils for run/pause intent (`run_enable`) and a self-clearing one-shot acquisition trigger (`read_now_trigger`), a Holding Register for the sampling interval (`sample_interval_s`), and Input Registers for temperature, humidity, sensor/device status, uptime, sample counter, and the register-map version itself. Published as stable, validated by a 19/19-passing automated integration suite.
- `weather_modbus_client.py`, a reusable Modbus RTU client for the Pi (PyModbus) encapsulating the Register Map v1 offsets, with a configurable timeout/retry policy and automatic reconnection after transport errors, so a non-responding Pico or a Modbus exception response no longer kills the master process.
- Pico-side Modbus RTU server firmware implementing the full control contract (`run_enable`, `sample_interval_s`, `read_now_trigger`) against the DHT11 sensor, including idempotent writes and asynchronous, self-clearing `READ_NOW` handling.
- A compatibility patch for `micropython-modbus` 2.3.7's frame reader, which could otherwise consume several accumulated Modbus RTU requests as a single frame on the Pico.
- A second Pico-side patch that validates `sample_interval_s` (`2`–`3600`) before the FC06 response is sent, since the upstream library normally responds before running the value's own callback.
- Coil, full-map, and control-contract smoke tests for both the Pi and Pico sides, a Pi-side resilience test exercising timeouts/retries/reconnection, and raw-frame tests that isolate wire-level Modbus behavior from PyModbus's own retry logic.
- `test_modbus_register_map.py`, an automated integration suite validating the complete Register Map v1 contract end to end.
- `modbus_client_demo.py`, a manual demo of the high-level client reading and writing the Register Map v1.
- Investigation notes on a `micropython-modbus` 2.3.7 RTU framing bug (`docs/Phase 2/p2_micropython-modbus_2.3.7_RTU framing_bug.md`).

### Fixed

- Writes to `sample_interval_s` outside the `2`–`3600` valid range are now rejected with a Modbus `03 — Illegal Data Value` exception instead of being silently accepted.

### Removed

- The frozen draft `docs/Phase 2/p2_modbus_registry_map.md`, superseded by the published `p2_modbus_register_map_v1.md`.

Closes Phase 2 (M2): all milestone gates pass (consistent register reads, confirmed control writes, errors that don't freeze the link, the automated register-map suite, and this document's publication as the canonical v1.0 contract).

## [0.6.0] - 2026-08-24

### Added

- Full Phase 1 guide (`docs/Phase 1/p1_guide_phase1.md`) documenting wiring, the UART command protocol, the database schema, and execution steps for the complete DHT11 → Pico → UART → Pi → PostgreSQL pipeline.
- `.env.example` for the DHT11 experiment's PostgreSQL credentials, and an additional development SQL script for inspecting stored telemetry.

Closes Phase 1 (M1): validated with a 23h25m continuous run with zero data loss and a UART disconnect / Pico power-loss recovery test exercising the connection-liveness and state-reconciliation logic added in 0.5.0.

## [0.5.0] - 2026-08-24

### Added

- Connection-liveness tracking for the Pi UART client (P1-20 / G1-10): a `DESCONOCIDO → CONECTADO → DESCONECTADO` state machine, independent of the Pico's own `STOPPED/RUNNING/FAULT` state, driven by an active heartbeat (`PING`) and a liveness timeout.
- Automatic state reconciliation after a reconnection: on `DESCONECTADO → CONECTADO`, the client queries the Pico's real `STATUS` and only resends `SET_INTERVAL`/`START` if it doesn't match what the user last requested (e.g. after a power loss, where the Pico reboots into `STOPPED`); a mere UART blip that never interrupted sampling triggers no action.
- Backoff on the heartbeat ping rate while `DESCONECTADO`, to reduce UART traffic and log noise during extended outages.
- `quiet` / `verbose` local commands to toggle routine `PING`/`PONG` logging on and off without affecting the underlying liveness detection.

### Fixed

- UART writes from the heartbeat thread and the interactive command thread could interleave and corrupt commands on the wire; both now go through a single lock.
- A `SerialException` while transmitting an interactive command (not just the heartbeat's `PING`) could crash the process with an unhandled traceback; it's now caught and reflected in the connection state like any other transmission failure.

## [0.4.0] - 2026-08-09

### Added

- PostgreSQL persistence layer for raw DHT11 telemetry (`experiments/dht_11/db.py`), reading its credentials from a local, git-ignored `.env` file.
- `weather_measurement_raw` and `weather_measurement_derived` tables, with the derived table referencing the raw row it was computed from for full traceability.
- Derived-metrics service (`experiments/dht_11/derived_metrics.py`) that computes dew point (Magnus formula) for pending raw readings and keeps polling for new ones.
- Multi-threaded Pi-side UART client (`experiments/dht_11/pi_dht11_station.py`): a receive thread that validates incoming telemetry, and a dedicated DB-writer thread so a slow or unavailable database never blocks UART reads.
- Development SQL scripts for inspecting stored DHT11 data (`backend/database/`).
- `python-dotenv` dependency for local secret loading.

## [0.3.0] - 2026-08-09

### Added

- DHT11 sensor node firmware for the Raspberry Pi Pico, handling PING, STATUS, START, STOP, SET_INTERVAL, and READ_NOW commands and reporting telemetry as JSON over UART.
- `requirements.txt` with the project's initial Python dependencies.

## [0.2.0] - 2026-08-02

### Added

- UART command handling and LED control on the Pico side, extending the initial loopback experiment into a first request/response protocol.

## [0.1.0] - 2026-07-27

### Added

- Initial monorepo structure (`backend/`, `firmware/`, `hardware/`, `experiments/`, `docs/`, `infrastructure/`).
- Apache License 2.0.
- First Pi-to-Pico UART communication experiment.

<!--
No git tags exist yet, so version headings above are not linked to
comparison URLs. Once releases start being tagged (e.g. v0.1.0), add
compare links here, e.g.:
[0.4.0]: https://github.com/PDJ-E/weather-station/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/PDJ-E/weather-station/compare/v0.2.0...v0.3.0
-->

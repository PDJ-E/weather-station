# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/) once the project reaches 1.0.0. While the project is on a `0.x` version, minor releases (`0.X.0`) may include breaking changes as the architecture is still being established.

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

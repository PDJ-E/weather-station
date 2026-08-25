# Phase 1 Guide — Weather Station (P1-21)

> Purpose of this document: so someone else (or you, months later) can reproduce the DHT11 → Pico → UART → Pi → PostgreSQL prototype from scratch, without having to reconstruct decisions that are already made.
>
> Satisfies G1-12.

---

## 1. Wiring

Three independent circuits: Pico power, UART data, and the DHT11 sensor.

Each one uses its own ground reference pair — see "Why it's wired this way" at the end of the section.

### 1.1 Power (Pi 5 → Pico 2)

The Pico is powered from the Pi; it does not use a USB cable during normal operation.

| Pi 5 (physical pin) | Pico 2 (physical pin) | Function |
|---|---|---|
| 2 (5V) | 39 (VSYS) | Power |
| 14 (GND) | 38 (GND) | Power return |

### 1.2 UART (Pi 5 ↔ Pico 2)

UART0 on both ends, 115200 baud, 8N1. TX and RX crossed.

| Pi 5 | Pico 2 | Direction |
|---|---|---|
| GPIO14 / TX (physical pin 8) | GP1 / RX (physical pin 2) | Pi → Pico |
| GPIO15 / RX (physical pin 10) | GP0 / TX (physical pin 1) | Pico → Pi |
| Physical pin 6 (GND) | Physical pin 3 (GND) | Signal reference |

### 1.3 DHT11 Sensor (on the Pico 2)

| DHT11 | Pico 2 (physical pin) | Function |
|---|---|---|
| VCC | 36 (3V3 OUT) | Power — **3.3V, not 5V** |
| GND | 18 (GND) | Return |
| DATA | 20 (GP15) | Signal |

If the DHT11 module is the blue-board three-pin version, it already has a built-in pull-up resistor. If it's the bare four-pin sensor, add a 4.7k–10kΩ resistor between DATA and VCC.

### Why it's wired this way

- **Three separate GND pairs** (power, UART, and the sensor sharing the Pico's power ground) — the power supply current doesn't share a return path with the UART signal reference, so the Pico's or sensor's current draw doesn't inject noise into the logic levels UART relies on to interpret bits.
- **DHT11 powered at 3.3V, not 5V** — keeps the DATA signal within the range the Pico's GPIO tolerates, with no need for a voltage divider.
- **GP15 for the sensor** — deliberately far from GP0/GP1, already used by UART0, so the sensor signal doesn't run alongside the communication bus.

---

## 2. Commands

### 2.1 Pico firmware commands (sent over UART, one per line)

| Command | Success response | Notes |
|---|---|---|
| `PING` | `PONG` | Used as the active heartbeat probe from the Pi. |
| `STATUS` | `STATUS:{state},INTERVAL:{s},UPTIME_MS:{u},LAST_TEMP:{t},LAST_HUM:{h},LAST_ERROR:{e}` | `state` is `STOPPED`, `RUNNING`, or `FAULT`. |
| `START` | `ACK:START` | Sets state to `RUNNING`; resumes periodic sampling. Idempotent — safe to send while already `RUNNING`. |
| `STOP` | `ACK:STOP` | Sets state to `STOPPED`; sampling pauses. |
| `SET_INTERVAL <seconds>` | `ACK:INTERVAL:{s}` | Range 2–3600s. Below the minimum, above the maximum, missing, or non-integer values return an error. |
| `READ_NOW` | `ACK:READ_NOW` | Forces one immediate sample and emits it as telemetry, **even while `STOPPED`**. Resets the periodic scheduler so it doesn't fire again right away. |
| *(anything else)* | `ERR:UNKNOWN_COMMAND:{cmd}` | Malformed or unrecognized input never crashes the firmware. |

State machine: boots into `STOPPED`. `START` moves it to `RUNNING`. While `RUNNING`, each scheduled sample moves it to `RUNNING` (success) or `FAULT` (sensor read failed) — `FAULT` self-clears on the next successful sample. Only `STOP` returns it to `STOPPED`.

### 2.2 Telemetry (Pico → Pi, unsolicited while `RUNNING`, also on `READ_NOW`)

One JSON object per line:

```json
{"sequence": 42, "sensor": "DHT11", "temperature_c": 27, "humidity_pct": 68, "status": "OK", "uptime_ms": 153240}
```

- `status` is `"OK"` or `"ERR_SENSOR:<detail>"` (temperature/humidity are `null` in the error case).
- `sequence` and `uptime_ms` both reset on every Pico reboot — neither is stable across reboots, by design (see §3.1).

### 2.3 Pi-side local commands (`pi_station_client.py` prompt)

| Command | Effect |
|---|---|
| `help` | Prints this command list. |
| `last` | Shows last telemetry, last `STATUS` line, last raw response, current connection state, and desired state. |
| `quiet` | Hides the heartbeat's routine `PING`/`PONG` echo (transitions still always print). |
| `verbose` | Restores the routine echo (default at startup). |
| `exit` / `quit` | Clean shutdown — see §4.6. |
| *(anything else)* | Sent verbatim to the Pico over UART. |

---

## 3. Database Schema

### 3.1 Design decisions

- **`sensor_type` is free text, not an enum** — future sensors (e.g. BME688 in P3) plug in without a schema migration.
- **`id` uses `GENERATED ALWAYS AS IDENTITY`**, not the legacy `SERIAL` pseudo-type — the modern, standards-compliant equivalent.
- **`temperature_c` / `humidity_pct` are `DOUBLE PRECISION`, not `NUMERIC`** — these are continuous physical measurements, not exact decimal values; floating-point rounding at the 15th digit is irrelevant at this scale, and it's the idiomatic choice for sensor/time-series data.
- **No `UNIQUE(node_id, sequence)`** — the Pico resets `sequence` to 1 on every reboot, so that pair is not a stable identity over time. This table is an append-only log; deduplication, if ever needed, requires a separate boot/session identifier. That identifier is not implemented in P1.
- **`weather_measurement_derived` references the raw row it came from** (`raw_id`), for full traceability from a derived value back to the exact raw sample that produced it.

### 3.2 `weather_measurement_raw`

```sql
CREATE TABLE IF NOT EXISTS weather_measurement_raw (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),
    node_id             TEXT NOT NULL,
    sequence            BIGINT NOT NULL,
    uptime_ms           BIGINT NOT NULL,
    sensor_type         TEXT NOT NULL,
    temperature_c       DOUBLE PRECISION,
    humidity_pct        DOUBLE PRECISION,
    sensor_status       TEXT NOT NULL,
    raw_payload         JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_weather_measurement_raw_node_time
    ON weather_measurement_raw (node_id, received_at_utc);
```

Migration file: `backend/database/dev_create_raw_measurements.sql`.

### 3.3 `weather_measurement_derived`

```sql
CREATE TABLE IF NOT EXISTS weather_measurement_derived (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_id              BIGINT NOT NULL REFERENCES weather_measurement_raw(id),
    computed_at_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),
    dew_point_c         DOUBLE PRECISION
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_measurement_derived_raw_id
    ON weather_measurement_derived (raw_id);
```

Migration file: `backend/database/dev_create_weather_measurement_derived.sql`.

The unique index guarantees each raw row is processed at most once, even if the derived-metrics process is restarted or run twice by mistake.

### 3.4 PostgreSQL role and databases

- Role: `weather_station_app` (not superuser; password authentication over TCP to `localhost`).
- Databases: `weather_station_dev` (active development target) and `weather_station` (reserved, not yet in use).
- Password is **not** hardcoded: `db.py` loads it from a `.env` file (`DB_PASSWORD=...`) sitting next to it, via `python-dotenv`. `.env.example` (no real value) is committed; the real `.env` is git-ignored.

### 3.5 `db.py`

Lives at `experiments/dht_11/db.py`, next to `pi_station_client.py` — not yet promoted to `backend/`. It is a plain module (no `if __name__ == "__main__"`), imported as `experiments.dht_11.db`. It exposes `get_connection()` and `insert_telemetry(conn, telemetry, raw_payload)`, which fills in `node_id` from the constant `NODE_ID = "pico-01-dht11"` (the identifier for this single node today — future nodes need their own).

### 3.6 `derived_metrics.py`

A separate, standalone process — it only *reads* `weather_measurement_raw` and *writes* `weather_measurement_derived`; it never touches UART, and can be started or stopped independently of `pi_station_client.py`. On startup it processes every raw row that doesn't have a derived row yet (catch-up), then polls for new ones every 5 seconds.

Computes dew point with the classic Magnus formula (constants 17.27 / 237.7), deliberately not the more precise Alduchov–Eskridge variant: the DHT11 only reports whole-degree/whole-percent readings, so extra formula precision would be false precision.

---

## 4. Execution

### 4.1 Environment

```bash
sudo apt install -y python3-venv python3-full   # once, if not already present
cd ~/Projects/weather-station
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # pyserial, prompt_toolkit, psycopg[binary], python-dotenv
```

### 4.2 File layout (relevant to P1)

```text
experiments/dht_11/
├── pico_dht11_station.py       # Pico firmware (flashed via Thonny)
├── pi_station_client.py        # runs on the Pi
├── db.py                       # imported by pi_station_client.py and derived_metrics.py
├── derived_metrics.py          # separate process, dew point
├── .env.example                # committed template
└── .env                        # real DB_PASSWORD, git-ignored, created locally

backend/database/
├── dev_create_raw_measurements.sql
└── dev_create_weather_measurement_derived.sql

docs/
└── P1_phase1_guide.md          # this document
```

### 4.3 Flashing the Pico

Upload `pico_dht11_station.py` to the Pico as `main.py` (or run it directly from Thonny for bench testing). On boot it prints `Nodo DHT11 listo. Estado inicial: STOPPED` over its USB REPL and starts listening on UART0.

### 4.4 Running on the Pi

`pi_station_client.py` anchors the project root from `Path(__file__)` before importing `experiments.dht_11.db`, so execution does **not** depend on the current working directory. The following is the normal project workflow:

```bash
cd ~/Projects/weather-station
source .venv/bin/activate
python3 experiments/dht_11/pi_station_client.py
```

Running it from inside `experiments/dht_11/` also works:

```bash
cd ~/Projects/weather-station/experiments/dht_11
source ~/Projects/weather-station/.venv/bin/activate
python3 pi_station_client.py
```

On success you'll see:

```text
[INFO] Conectado a PostgreSQL: weather_station_dev
[INFO] Puerto abierto: /dev/ttyAMA0
[INFO] Baud rate: 115200
[INFO] Receptor UART iniciado.
[INFO] Heartbeat iniciado. Estado de conexión: DESCONOCIDO
```

Then send `START` at the prompt to begin sampling.

### 4.5 Architecture (threads)

| Thread | Responsibility |
|---|---|
| Main (prompt) | Interactive command entry (`prompt_toolkit`, safe against concurrent background output). |
| `_receive_loop` | Reads UART continuously, separates telemetry (JSON) from command responses (plain text), validates telemetry, enqueues valid samples. Never touches PostgreSQL. |
| `db_writer_loop` | Consumes the queue and writes to PostgreSQL. If the database is slow or down, only this thread stalls — UART reading is unaffected. |
| `_heartbeat_loop` | Sends `PING` every 5s (20s once `DESCONECTADO` — backoff); declares `DESCONECTADO` after 15s with no recognized traffic. |
| `_reconcile_state` (spawned on reconnect only) | Compares the Pico's real `STATUS` against the last state the Pi itself commanded, and restores it if they don't match (e.g. after a Pico power loss) — without forcing `RUNNING` if the last command was `STOP`. |

**Connection states** (independent of the Pico's own `STOPPED` / `RUNNING` / `FAULT`):

`DESCONOCIDO` (unknown at startup) → `CONECTADO` (recognized traffic received) → `DESCONECTADO` (15s+ of silence) → back to `CONECTADO` on the next recognized message.

#### Reconnect scope in P1

For the direct GPIO UART used in P1, physically disconnecting/reconnecting the Pico or rebooting it does not necessarily remove `/dev/ttyAMA0` from the Pi. The existing open serial device can therefore resume receiving traffic when the Pico returns, at which point liveness changes back to `CONECTADO` and state reconciliation runs.

This is **not** general serial-port reopen logic. If the Pi itself raises a real `serial.SerialException` in the receive loop, the current client marks the node disconnected, stops the receiver, and requires the script to be restarted. Full automatic recovery from that class of failure remains resilience work beyond the validated P1 path.

### 4.6 Clean shutdown

Type `exit` at the prompt — do **not** kill the process or the terminal abruptly.

The client stops the UART/heartbeat threads, places a sentinel behind already queued telemetry, and gives the database writer up to 5 seconds to finish pending writes before the PostgreSQL connection is closed. Under normal operation this allows queued samples to be flushed cleanly, but it is **not an absolute durability guarantee** if PostgreSQL is blocked or unavailable for longer than that timeout.

Killing the process abruptly can lose samples that are still in flight or waiting in memory.

### 4.7 Running unattended (survives closing the SSH/VS Code session)

```bash
tmux new -s weather

# inside the session:
cd ~/Projects/weather-station
source .venv/bin/activate
python3 experiments/dht_11/pi_station_client.py
# then START at the prompt
```

Detach without killing anything: `Ctrl+B`, then `D`. Reattach later with `tmux attach -t weather`, or check it's alive with `tmux ls`. To also run `derived_metrics.py`, open a second window in the same session (`Ctrl+B`, then `C`).

### 4.8 Verifying data is arriving

```sql
SELECT
    count(*) AS rows,
    min(received_at_utc) AS first,
    max(received_at_utc) AS last,
    max(sequence) AS last_sequence,
    max(uptime_ms) AS last_uptime_ms
FROM weather_measurement_raw
WHERE node_id = 'pico-01-dht11';
```

```sql
SELECT count(*)
FROM weather_measurement_derived;
```

The derived-row count should track the raw-row count closely and eventually catch up completely while `derived_metrics.py` is running.

### 4.9 Validation on record

- **G1-09** (extended continuous run): 23h25min, 15,981 rows, zero gaps in `sequence`, zero Pico reboots, zero sensor errors, 100% derived-metric coverage.
- **G1-10** (disconnection detection + state reconciliation): physical UART-disconnect and Pico power-loss tests both passed. The link was reported as `DESCONECTADO` / `CONECTADO` independently of the Pico's own state, and after a Pico reboot a mismatch between actual and desired state was restored automatically without manual intervention. This validation used the still-open direct GPIO UART path described in §4.5; it does not imply automatic reopen after a real `serial.SerialException`.

---

## 5. Known P1 limitations / resilience debt

These items do not invalidate P1, but they define the boundary of what was actually implemented and validated:

- PostgreSQL is required during startup; if the initial DB connection fails, the UART client does not start.
- Failed INSERTs are logged but are not retried with backoff.
- `telemetry_queue` is unbounded and can grow indefinitely during a prolonged PostgreSQL outage.
- A true `serial.SerialException` stops the receiver and requires restarting the client; automatic serial-port reopen is not implemented.
- Clean shutdown is best-effort with a 5-second DB-writer join timeout, not a formal guarantee that every queued sample is durable under DB failure.
- `desired_interval_s` is currently updated when a syntactically integer `SET_INTERVAL` command is sent, before the Pico's ACK is known. An out-of-range integer rejected by the Pico can therefore remain as the Pi's desired interval and later be retried during state reconciliation. A future fix should either validate the 2–3600 range locally or update desired state only after `ACK:INTERVAL`.
- A persistent boot/session identifier is not implemented; `sequence` and `uptime_ms` both reset on Pico reboot.


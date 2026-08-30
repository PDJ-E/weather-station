# Weather Station — Modbus Register Map v1.0

> **Status:** STABLE / FROZEN  
> **Published by:** P2-09  
> **Protocol:** Modbus RTU  
> **Register Map Version:** `0x0100`  
> **Semantic Version:** `v1.0`  
> **Client / master:** Raspberry Pi 5  
> **Server / slave:** Raspberry Pi Pico 2  
> **Unit ID:** `1`  
> **Serial:** `115200 8N1`  
> **Physical layer during P2:** Direct TTL UART  
> **Validation:** P2-08 automated integration suite — **19/19 tests PASS**  
>
> This document is the canonical Modbus contract for Weather Station Register
> Map v1.0. Pi-side clients and Pico-side firmware implementing this contract
> must expose and expect `register_map_version = 0x0100`.

---

## 1. Compatibility and versioning policy

Input Register `208` exposes the register-map version using the encoding:

```text
0xMMmm
```

where:

- `MM` = major version
- `mm` = minor version

Examples:

```text
0x0100 -> v1.0
0x0101 -> v1.1
0x0200 -> v2.0
```

For this contract:

```text
register_map_version = 0x0100
```

Compatibility rules:

- A client expecting v1.0 must verify Input Register `208` before assuming the
  semantics defined by this document.
- A change that modifies the meaning, encoding, access type, or semantics of an
  existing address is a **breaking change** and requires a new major version.
- Backward-compatible additions may use a new minor version.
- Existing v1.0 addresses must not silently change meaning in later
  backward-compatible revisions.

---

## 2. Design goals

The v1 map:

- Replaces the temporary P1 text/JSON interface with a deterministic Modbus contract.
- Keeps **commanded intent** separate from **actual device state**.
- Exposes the latest sensor snapshot as read-only telemetry.
- Rejects invalid writes with standard Modbus exception responses.
- Preserves enough diagnostic information to detect a new acquisition, a sensor
  failure, a device reboot, and a register-map compatibility mismatch.
- Defines 32-bit packing, word order, snapshot consistency, and scaling explicitly.
- Keeps Modbus transaction latency independent of variable sensor-I/O timing.
- Makes control/configuration writes idempotent where practical so client retries
  do not produce unintended state changes.

---

## 3. Addressing conventions

This document uses **zero-based Modbus offsets**, not legacy `3xxxx` / `4xxxx`
reference notation.

Examples:

- `Input Register offset 200` means the starting-address field transmitted in
  the Modbus request is decimal `200` (`0x00C8`).
- `Holding Register offset 101` means offset `101` in the Holding Register
  address space.

Modbus address spaces are independent. Therefore Coil 100, Holding Register 100,
and Input Register 100 would be distinct objects even if they shared the same
numeric offset.

---

## 4. Serial / Modbus contract

| Parameter | Value |
|---|---|
| Protocol | Modbus RTU |
| Client / master | Raspberry Pi 5 |
| Server / slave | Raspberry Pi Pico 2 |
| Unit ID | `1` |
| UART | UART0 |
| Pico TX | GP0 |
| Pico RX | GP1 |
| Pi serial device | `/dev/ttyAMA0` |
| Baud rate | `115200` |
| Data bits | `8` |
| Parity | None |
| Stop bits | `1` |
| Framing | `8N1` |
| Physical layer in P2 | Direct TTL UART |

The `8N1` choice is deliberate for this project's learning path. P2 validates
Modbus independently from the later physical-layer migration to RS-485.

### Supported function codes

| Function code | Operation |
|---:|---|
| FC01 | Read Coils |
| FC03 | Read Holding Registers |
| FC04 | Read Input Registers |
| FC05 | Write Single Coil |
| FC06 | Write Single Holding Register |

No custom function codes are defined in v1.0.

---

## 5. Register Map overview

### 5.1 Coils

| Offset | Name | Access | Boot | Purpose |
|---:|---|---|---:|---|
| 100 | `run_enable` | R/W | `0` | Persistent run/pause intent |
| 102 | `read_now_trigger` | R/W | `0` | Asynchronous one-shot acquisition trigger |

### 5.2 Holding Registers

| Offset | Name | Access | Boot | Purpose |
|---:|---|---|---:|---|
| 101 | `sample_interval_s` | R/W | `5` | Periodic sampling interval in seconds |

### 5.3 Input Registers

| Offset(s) | Name | Application type | Access | Boot | Purpose |
|---:|---|---|---|---:|---|
| 200 | `temperature_c` | `int16` | R | `0` | Latest valid temperature |
| 201 | `humidity_pct` | `uint16` | R | `0` | Latest valid relative humidity |
| 202 | `sensor_status` | `uint16 enum` | R | `2` | Status of last acquisition attempt |
| 203 | `device_state` | `uint16 enum` | R | `0` | Actual firmware state |
| 204–205 | `uptime_s` | `uint32` | R | `0` | Seconds since Pico boot |
| 206–207 | `sample_counter` | `uint32` | R | `0` | Acquisition attempts since boot |
| 208 | `register_map_version` | `uint16` | R | `0x0100` | Register-map contract version |

### 5.4 Reserved

| Offset | Space | Status |
|---:|---|---|
| 103 | Project-reserved offset | Unassigned in v1.0 |

---

## 6. Control interface

### 6.1 Coil 100 — `run_enable`

| Property | Value |
|---|---|
| Modbus type | Coil |
| Function codes | FC01 read / FC05 write |
| Access | R/W |
| Values | `0` = paused, `1` = periodic sampling enabled |
| Boot default | `0` |

Semantics:

- `1` replaces the P1 `START` command.
- `0` replaces the P1 `STOP` command.
- This field represents **client-commanded intent**, not necessarily the device's
  actual runtime condition.

A valid failure state is:

```text
run_enable   = 1
device_state = FAULT
```

#### Idempotence and scheduler behavior

- Writing `1` while already `1` is a **no-op**.
- Writing `0` while already `0` is a **no-op**.
- A `0 -> 1` transition schedules the first periodic acquisition one full
  `sample_interval_s` after the accepted write.
- A `1 -> 0` transition disables periodic sampling and cancels the pending
  periodic schedule.
- STOP does not erase the last committed telemetry snapshot.

After reboot, `run_enable` returns to `0`.

---

### 6.2 Holding Register 101 — `sample_interval_s`

| Property | Value |
|---|---|
| Modbus type | Holding Register |
| Function codes | FC03 read / FC06 write |
| Access | R/W |
| Unit | seconds |
| Valid range | `2–3600` |
| Boot default | `5` |

Semantics:

- Replaces the P1 `SET_INTERVAL <seconds>` command.
- Values below `2` or above `3600` must be rejected with
  `03 — Illegal Data Value`.
- Invalid writes must not change the current valid interval.
- The interval may be changed while `run_enable = 0`.
- When sampling is later enabled, the most recently accepted interval is used.
- While already running, writing the same active value is a no-op; writing a
  different valid value reschedules the next periodic acquisition for one full
  new interval after the accepted write.

This makes safe FC06 retries idempotent.

---

### 6.3 Coil 102 — `read_now_trigger`

| Property | Value |
|---|---|
| Modbus type | Coil |
| Function codes | FC01 read / FC05 write |
| Access | R/W |
| Values | `0` / `1` |
| Boot default | `0` |
| Behavior | Self-clearing, asynchronous one-shot trigger |

Semantics:

- Writing `1` requests exactly one immediate acquisition.
- The FC05 response acknowledges acceptance without waiting for sensor I/O.
- Sensor acquisition is performed later by the Pico cooperative main loop.
- While pending, the Coil may read back `1`.
- The client is not required to observe the pending `1`.
- Writing `1` again while already pending is a no-op.
- Writing `0` from the client does not cancel or clear a pending request.
- Only firmware performs the `1 -> 0` transition, after the requested
  acquisition attempt completes and its result has been committed.
- `READ_NOW` works while `run_enable = 0`.
- It is an event, not persistent intent, and is never replayed after reboot.

#### Completion semantics

```text
request pending
    ↓
sensor acquisition attempt
    ↓
build complete candidate snapshot
    ↓
commit temperature / humidity / sensor_status
    ↓
increment sample_counter exactly once
    ↓
reschedule next periodic sample if run_enable = 1
    ↓
firmware clears read_now_trigger to 0
```

Authoritative client confirmation:

```text
read_now_trigger == 0
AND
sample_counter > pre_trigger_sample_counter
```

If `run_enable = 1`, completion of the manual acquisition resets the periodic
schedule. The next periodic sample occurs one full `sample_interval_s` after the
manual acquisition finishes.

---

## 7. Telemetry and diagnostics

All fields in this section use **Input Registers** and FC04.

### 7.1 Input Register 200 — `temperature_c`

| Property | Value |
|---|---|
| Type | signed `int16` |
| Encoding | two's complement |
| Scale | physical °C = register value / 100 |
| Boot value | `0` |

Examples:

```text
27.43 °C  ->  2743
-12.34 °C -> -1234
```

Behavior:

- Holds the last known valid temperature.
- Failed acquisition attempts do not replace a prior valid value with a sentinel.
- `sensor_status` reports whether the latest acquisition attempt succeeded.

---

### 7.2 Input Register 201 — `humidity_pct`

| Property | Value |
|---|---|
| Type | unsigned `uint16` |
| Scale | physical %RH = register value / 100 |
| Boot value | `0` |

Example:

```text
68.25 %RH -> 6825
```

Behavior:

- Holds the last known valid humidity.
- Failed acquisition attempts do not overwrite the previous good reading.

---

### 7.3 Input Register 202 — `sensor_status`

| Code | Meaning |
|---:|---|
| 0 | `OK` — last acquisition succeeded |
| 1 | `ERR_SENSOR` — last acquisition attempt failed |
| 2 | `NO_DATA` — no acquisition completed since boot |

Boot default: `2` (`NO_DATA`).

---

### 7.4 Input Register 203 — `device_state`

| Code | Meaning |
|---:|---|
| 0 | `STOPPED` |
| 1 | `RUNNING` |
| 2 | `FAULT` |

Boot default: `0` (`STOPPED`).

`device_state` is actual firmware state. `run_enable` is commanded intent.

---

### 7.5 Input Registers 204–205 — `uptime_s`

| Property | Value |
|---|---|
| Type | unsigned `uint32` |
| Unit | seconds |
| Boot value | `0` |
| Word order | High word first |

Encoding:

```text
uint32 value = 0x12345678

Register 204 = 0x1234
Register 205 = 0x5678
```

Reconstruction:

```text
uptime_s = (reg204 << 16) | reg205
```

The two words must be latched from the same logical `uint32` value for a given
response. Clients should read `204–205` in one FC04 transaction.

---

### 7.6 Input Registers 206–207 — `sample_counter`

| Property | Value |
|---|---|
| Type | unsigned `uint32` |
| Boot value | `0` |
| Word order | High word first |

Semantics:

- Increments once for every acquisition attempt.
- Failed acquisition attempts also increment the counter.
- Resets to `0` on reboot.
- Provides a freshness signal even when physical readings do not change.

Both words must be read from the same latched value.

---

### 7.7 Input Register 208 — `register_map_version`

| Property | Value |
|---|---|
| Type | unsigned `uint16` |
| Access | Read-only |
| Value in v1.0 | `0x0100` |

The Pi-side client must use it as a compatibility check before assuming map
semantics.

---

## 8. Acquisition snapshot consistency

The following fields form the **acquisition snapshot group**:

```text
200       temperature_c
201       humidity_pct
202       sensor_status
206–207   sample_counter
```

These fields represent one committed acquisition epoch. The Pico must not expose
partially updated sensor data.

The following Input Registers may change independently:

```text
203       device_state
204–205   uptime_s
208       register_map_version
```

### Block-read consistency

The intended telemetry polling transaction is:

```text
FC04
start = 200
count = 9
```

which reads `200–208` in one request.

Multi-register values such as `uptime_s` and `sample_counter` must not tear
between high and low words.

---

## 9. Modbus exception policy

| Condition | Exception |
|---|---|
| Unsupported function code | `01 — Illegal Function` |
| Address not implemented in requested address space | `02 — Illegal Data Address` |
| Write value outside allowed domain | `03 — Illegal Data Value` |

Examples:

```text
FC06 Holding 101 = 1
-> Exception 03

FC03 Holding 999
-> Exception 02
```

Invalid requests must not crash or wedge either endpoint.

---

## 10. Communication policy

The validated Pi client policy for P2 is:

```text
timeout = 1.0 s
retries = 3
```

Transport failure policy:

```text
request
   ↓
PyModbus retries according to policy
   ↓
failure exhausted
   ↓
raise controlled WeatherModbusTransportError
   ↓
reset local serial transport
   ↓
next operation reconnects
```

Requirements:

- No infinite retry loop.
- A timeout must not crash the master process.
- A timeout must not freeze the Pico.
- The next valid request must be able to recover the link.
- Valid Modbus exception responses must not force a transport reset.

---

## 11. Reboot behavior

No v1.0 state is persisted in Pico non-volatile storage.

| Field | Boot value |
|---|---|
| `run_enable` | `0` |
| `sample_interval_s` | `5` |
| `read_now_trigger` | `0` |
| `temperature_c` | `0` |
| `humidity_pct` | `0` |
| `sensor_status` | `NO_DATA (2)` |
| `device_state` | `STOPPED (0)` |
| `uptime_s` | `0` |
| `sample_counter` | `0` |
| `register_map_version` | `0x0100` |

The client may retain desired configuration and reconcile it after detecting a
reboot. `read_now_trigger` is never reconciled because it is a one-shot event.

---

## 12. Polling model

```text
Pico:
  acquires sensor data independently according to sample_interval_s

Pi:
  polls the latest committed snapshot using Modbus
```

The Pico does not emit unsolicited JSON telemetry in P2.

Typical telemetry poll:

```text
Unit ID: 1
Function: FC04 Read Input Registers
Start: 200
Count: 9
```

---

## 13. Changes from the P1 JSON protocol

| P1 concept | Modbus v1.0 replacement |
|---|---|
| `START` / `STOP` | Coil 100 `run_enable` |
| `SET_INTERVAL` | Holding Register 101 `sample_interval_s` |
| `READ_NOW` | Coil 102 `read_now_trigger` |
| temperature | Input Register 200 |
| humidity | Input Register 201 |
| status | Input Register 202 |
| Pico state | Input Register 203 |
| uptime | Input Registers 204–205 |
| sequence-like freshness | Input Registers 206–207 `sample_counter` |

Deliberate changes:

- Free-text command strings removed.
- Unsolicited JSON telemetry removed.
- Free-text sensor error detail removed from transport.
- `sequence` replaced by explicit `sample_counter`.
- `uptime_ms` replaced by `uptime_s` as a `uint32`.
- `READ_NOW` changed from synchronous/blocking to asynchronous/self-clearing.
- Control/configuration writes have explicit idempotence semantics.
- `115200 8N1` retained intentionally for P2.

---

## 14. Validation evidence

### P2-04 — Modbus server and complete map transport

```text
FC01 Read Coils             PASS
FC03 Read Holding Registers PASS
FC04 Read Input Registers   PASS
FC05 Write Single Coil      PASS
FC06 Write Single Register  PASS

Coil 100                    PASS
Coil 102                    PASS
Holding 101                 PASS
Input 200–208               PASS
Scaling x100                PASS
uint32 high-word-first      PASS
Register Map v1.0           PASS
```

### P2-05 — Pi client abstraction

```text
Connect / close           PASS
Map version validation    PASS
Telemetry block decoding  PASS
Coil read/write           PASS
Holding read/write        PASS
Scaling x100              PASS
uint32 high-word-first    PASS
```

### P2-06 — Control semantics

```text
START                    PASS
STOP                     PASS
Periodic scheduler       PASS
sample_interval_s        PASS
READ_NOW async           PASS
READ_NOW while STOPPED   PASS
READ_NOW self-clear      PASS
sample_counter confirm   PASS
Real DHT11 telemetry     PASS
```

### P2-07 — Communication resilience

```text
Request timeout             PASS
Retries                     PASS
Transport error controlled  PASS
Master survives timeout     PASS
Automatic next reconnect    PASS
Pico survives timeout       PASS
Modbus Exception 02         PASS
Link survives exception     PASS
```

### P2-08 — Automated integration suite

```text
Ran 19 tests in 9.833s

OK
```

The suite covered defined Coils and Holding Registers, boundary and invalid
interval values, Exceptions 02 and 03, Input Registers `200–208`, scaling,
word order, full-block FC04 reads, scheduler behavior, and `READ_NOW`.

---

## 15. Milestone gates

```text
G2-01  Reading registers consistently        PASS
G2-02  Control writes confirmed              PASS
G2-03  Errors do not freeze the link         PASS
G2-04  Automated register-map suite          PASS
G2-05  Register Map v1 published             PASS
```

P2 / M2 is considered complete when this document is committed as the canonical
v1.0 contract.

---

## 16. Known implementation note — micropython-modbus 2.3.7

The Pico implementation currently uses project-owned compatibility patches for
behavior observed with `micropython-modbus 2.3.7` on Raspberry Pi Pico 2 / RP2.

One patch replaces the RTU frame reader used by the P2 function-code subset after
the upstream receive path was observed concatenating repeated requests into a
single buffer under retries.

A second local compatibility layer ensures invalid writes to Holding Register
`101` can return Exception `03` before the upstream library commits/responds.

See:

```text
docs/Phase 2/p2_micropython-modbus_2.3.7_RTU_framing_bug.md
```

These patches are implementation details and do not alter Register Map v1.0.
They must be reevaluated if the library, MicroPython, MCU platform, supported
function codes, or physical transport changes.

---

## 17. Known v1.0 limitations / deferred work

- No free-text sensor error detail is transported.
- No persistent configuration in Pico flash.
- No explicit boot/session UUID.
- Reboot detection relies on the `uptime_s` / `sample_counter` epoch.
- No multi-client arbitration; architecture assumes one Modbus client/master.
- No RS-485 physical layer yet.
- No custom Modbus function codes.
- No BME688-specific registers yet.
- Environmental-map expansion must preserve existing v1.0 meanings.

---

## 18. Canonical contract summary

```text
DEVICE
────────────────────────────────────────
Protocol        Modbus RTU
Version         v1.0 / 0x0100
Unit ID         1
UART            115200 8N1
Physical P2     direct TTL UART

FUNCTION CODES
────────────────────────────────────────
FC01  Read Coils
FC03  Read Holding Registers
FC04  Read Input Registers
FC05  Write Single Coil
FC06  Write Single Holding Register

COILS
────────────────────────────────────────
100  run_enable
102  read_now_trigger
     asynchronous
     firmware self-clear

HOLDING REGISTERS
────────────────────────────────────────
101  sample_interval_s
     uint16
     range 2–3600 s
     default 5 s

INPUT REGISTERS
────────────────────────────────────────
200  temperature_c           int16   ×0.01 °C
201  humidity_pct            uint16  ×0.01 %RH
202  sensor_status           enum
203  device_state            enum
204  uptime_s HIGH           uint32
205  uptime_s LOW
206  sample_counter HIGH     uint32
207  sample_counter LOW
208  register_map_version    uint16  0x0100

RESERVED
────────────────────────────────────────
103  unassigned in v1.0

EXCEPTIONS
────────────────────────────────────────
01  Illegal Function
02  Illegal Data Address
03  Illegal Data Value
```

---

## 19. Stability rule

This document is the published **stable Register Map v1.0 contract**.

Existing v1.0 addresses are frozen.

Any future implementation must preserve their:

- address space;
- offset;
- access mode;
- encoding;
- scale;
- word order;
- boot behavior;
- control semantics.

A future extension may add new addresses while keeping v1.0-compatible
semantics. A breaking semantic change requires a new major map version.

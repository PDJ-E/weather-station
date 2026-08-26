# P2 Modbus Register Map — v1 Implementation Freeze

> **Status:** implementation contract **frozen** for `P2-04` / `P2-05`.
> The semantic decisions in this document should not change during implementation
> unless a real contradiction or implementation blocker is discovered.
>
> `P2-08` will validate this frozen contract with the automated test suite, and
> `P2-09` will formally publish it as the stable `Register Map v1`.
>
> **Roles (`P2-01`):** Raspberry Pi 5 = Modbus client/master.  
> Pico 2 = Modbus server/slave, **Unit ID 1**.  
> Transport for P2: the same UART0 link already validated in P1, **115200 baud, 8N1**.  
> No RS-485 transceivers are introduced yet; P2 validates Modbus independently
> from the future physical-layer migration.
>
> **Deliberate serial choice:** `8N1` is intentionally retained from P1. This
> project is using Modbus RTU as a learning and transition protocol, not as a
> strict interoperability target with arbitrary industrial Modbus equipment.
> The framing choice is therefore part of this project's contract.

---

## 1. Design goals

The v1 map should:

- Replace the temporary P1 text/JSON interface with a deterministic Modbus contract.
- Keep **commanded intent** separate from **actual device state**.
- Expose the latest sensor snapshot as read-only telemetry.
- Make invalid writes rejectable with standard Modbus exception responses.
- Preserve enough diagnostic information to detect:
  - a new acquisition,
  - a sensor failure,
  - a device reboot,
  - a register-map compatibility mismatch.
- Keep the data model simple enough for P2 while introducing multi-register
  values so 32-bit packing, latching, and word order are defined explicitly.
- Keep Modbus transaction latency independent of variable-timing sensor I/O.
- Make control/configuration writes idempotent where practical so client retries
  do not produce unintended state changes.

---

## 2. Addressing conventions

This document uses **zero-based Modbus offsets**, not legacy `3xxxx` / `4xxxx`
reference notation.

Examples:

- `Input Register offset 200` means the starting-address field transmitted in
  the Modbus request is decimal `200` (`0x00C8`).
- `Holding Register offset 101` means offset `101` in the Holding Register
  address space.

The Modbus data model contains **independent address spaces**. Therefore these
are distinct objects even if they used the same numeric offset:

- Coil 100
- Holding Register 100
- Input Register 100

This v1 map intentionally uses different numerical ranges for readability, but
the numbers themselves do not have special meaning in the Modbus standard.

---

## 3. Serial / Modbus contract

| Parameter | Value |
|---|---|
| Protocol | Modbus RTU |
| Client / master | Raspberry Pi 5 |
| Server / slave | Raspberry Pi Pico 2 |
| Unit ID | `1` |
| UART | UART0 |
| Baud rate | `115200` |
| Data bits | `8` |
| Parity | None |
| Stop bits | `1` |
| Framing | `8N1` |
| Physical layer in P2 | TTL UART direct connection |

Standard Modbus function-code meanings are preserved. v1 uses:

| Function code | Operation |
|---:|---|
| FC01 | Read Coils |
| FC03 | Read Holding Registers |
| FC04 | Read Input Registers |
| FC05 | Write Single Coil |
| FC06 | Write Single Holding Register |

No custom/user-defined function codes are required in v1.

---

## 4. Map overview

### 4.1 Coils

| Offset | Name | Access | Purpose |
|---:|---|---|---|
| 100 | `run_enable` | R/W | Persistent run/pause intent |
| 102 | `read_now_trigger` | R/W | One-shot, asynchronous immediate-acquisition trigger |

### 4.2 Holding Registers

| Offset | Name | Access | Purpose |
|---:|---|---|---|
| 101 | `sample_interval_s` | R/W | Periodic sampling interval in seconds |

### 4.3 Input Registers

| Offset(s) | Name | Application type | Access | Purpose |
|---:|---|---|---|---|
| 200 | `temperature_c` | `int16` | R | Latest valid temperature |
| 201 | `humidity_pct` | `uint16` | R | Latest valid relative humidity |
| 202 | `sensor_status` | `uint16 enum` | R | Status of last acquisition attempt |
| 203 | `device_state` | `uint16 enum` | R | Actual firmware state |
| 204–205 | `uptime_s` | `uint32` | R | Seconds since Pico boot |
| 206–207 | `sample_counter` | `uint32` | R | Number of acquisition attempts since boot |
| 208 | `register_map_version` | `uint16` | R | Register-map contract version |

### 4.4 Reserved

| Offset | Space | Status |
|---:|---|---|
| 103 | Project-reserved offset | Unassigned in v1 |

---

## 5. Control interface

### 5.1 Coil 100 — `run_enable`

| Property | Value |
|---|---|
| Modbus type | Coil |
| Function codes | FC01 read / FC05 write |
| Access | R/W |
| Values | `0` = paused, `1` = periodic sampling enabled |
| Boot default | `0` |

Semantics:

- `1` replaces the old P1 `START` command.
- `0` replaces the old P1 `STOP` command.
- This represents the **client's commanded intent**, not the Pico's actual
  runtime state.
- A sensor failure may therefore produce:

```text
run_enable   = 1
device_state = FAULT
```

This is valid: the client still wants sampling to continue, but the device is
currently faulted.

#### Idempotence and scheduler behavior

- Writing `1` while `run_enable` is already `1` is a **no-op**. It must not
  restart or shift the periodic scheduler.
- Writing `0` while `run_enable` is already `0` is a **no-op**.
- A transition `0 → 1` schedules the **first periodic acquisition one full
  `sample_interval_s` after the accepted write**.
- A transition `1 → 0` disables periodic sampling and cancels the pending
  periodic schedule. It does not erase the last committed telemetry snapshot.

After a Pico reboot this Coil returns to `0`, because the firmware boots into
`STOPPED`. If the client remembers that the desired state was running, the
client may reconcile that mismatch by writing `1` again.

---

### 5.2 Holding Register 101 — `sample_interval_s`

| Property | Value |
|---|---|
| Modbus type | Holding Register |
| Function codes | FC03 read / FC06 write |
| Access | R/W |
| Unit | seconds |
| Valid range | `2–3600` |
| Boot default | `5` |

Semantics:

- Replaces the old P1 `SET_INTERVAL <seconds>` command.
- A write below `2` or above `3600` **must be rejected** with Modbus exception
  `03 — Illegal Data Value`.
- The Pico must not silently clamp or ignore an invalid interval.
- The register may be changed while `run_enable = 0`.
- When sampling is later enabled, the most recently accepted interval is used.
- When sampling is already enabled:
  - writing the **same** currently active interval is a **no-op** and does not
    move the next scheduled acquisition;
  - writing a **different valid** interval updates the configuration and
    reschedules the next periodic acquisition for one full new interval after
    the accepted write.

This makes retries of the same FC06 write idempotent.

---

### 5.3 Coil 102 — `read_now_trigger`

| Property | Value |
|---|---|
| Modbus type | Coil |
| Function codes | FC01 read / FC05 write |
| Access | R/W |
| Values | `0` / `1` |
| Boot default | `0` |
| Behavior | Self-clearing, **asynchronous** one-shot trigger |

Semantics:

- Writing `1` requests exactly one immediate acquisition and is acknowledged
  immediately by the Modbus write response.
- The acquisition itself is **not** performed inside the Modbus write handler.
- The write handler sets a pending flag. The actual `sensor.measure()` call
  happens on the next pass of the Pico's cooperative main loop.
- This keeps Modbus response latency independent of variable sensor-I/O timing.
- While the request is pending, the Coil **may** read back `1`.
- The client is **not required to observe** the pending `1` state; the Pico may
  process the request before the next FC01 read arrives.
- Writing `1` while a request is already pending is a **no-op** and does not
  queue another acquisition.
- **Writing `0` is always a no-op from the client's perspective.** It does not
  clear, cancel, or acknowledge a pending request.
- Only the firmware may perform the `1 → 0` transition, and only after the
  requested acquisition attempt has completed and its result has been committed.
- The trigger works while `run_enable = 0`.
- This is an event/trigger, not persistent intent, and is never reconciled
  after reboot.

#### Completion semantics

For a `read_now_trigger` acquisition, the firmware performs this logical order:

```text
request pending
    ↓
sensor acquisition attempt
    ↓
build complete candidate acquisition snapshot
    ↓
commit temperature / humidity / sensor_status
    ↓
increment sample_counter exactly once
    ↓
reschedule next periodic sample if run_enable = 1
    ↓
firmware clears read_now_trigger to 0
```

The commit of the acquisition fields, the `sample_counter` increment, and the
self-clear of `read_now_trigger` must be exposed consistently to the Modbus
client; the client must not observe the trigger cleared while the old
`sample_counter` is still visible.

#### Interaction with periodic sampling

- If `run_enable = 1`, completing the manual acquisition resets the periodic
  schedule. The next periodic acquisition occurs one full
  `sample_interval_s` **after the manual acquisition finishes**.
- This deliberately preserves the P1 protection against a redundant periodic
  read immediately after `READ_NOW`.
- If `run_enable = 0`, exactly the one manual acquisition occurs and no
  periodic acquisition is scheduled afterward.

#### Authoritative client confirmation

The client should capture `sample_counter` before issuing the trigger.

Completion is confirmed when:

```text
read_now_trigger == 0
AND
sample_counter > pre_trigger_sample_counter
```

The client does not rely on observing `read_now_trigger == 1`.

For deterministic testing of `READ_NOW`, `run_enable` should be set to `0`
before the test so no periodic acquisition can independently increment the
counter during the verification window.

---

## 6. Telemetry and diagnostics

All registers in this section are **Input Registers** and are read-only from the
Modbus client using FC04.

### 6.1 Input Register 200 — `temperature_c`

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

- Holds the **last known valid temperature**.
- A failed acquisition does not overwrite the last good value with a sentinel.
- Clients must read `sensor_status` to determine whether the latest acquisition
  attempt succeeded.

The current DHT11 only reports whole-degree values, but the ×100 scale is
intentionally preserved so future sensors such as the BME688 can expose decimal
precision without changing the map.

---

### 6.2 Input Register 201 — `humidity_pct`

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

- Holds the **last known valid humidity**.
- A failed acquisition does not overwrite the previous good reading.
- `sensor_status` defines whether the last acquisition was valid.

---

### 6.3 Input Register 202 — `sensor_status`

| Code | Meaning |
|---:|---|
| 0 | `OK` — last acquisition succeeded |
| 1 | `ERR_SENSOR` — last acquisition attempt failed |
| 2 | `NO_DATA` — no acquisition has completed since boot |

Boot default: `2` (`NO_DATA`).

The old JSON transport carried a free-text error detail such as
`ERR_SENSOR:<detail>`. v1 intentionally reduces this to an enum. The original
DHT11 exception text is not transported over Modbus v1.

---

### 6.4 Input Register 203 — `device_state`

| Code | Meaning |
|---:|---|
| 0 | `STOPPED` |
| 1 | `RUNNING` |
| 2 | `FAULT` |

Boot default: `0` (`STOPPED`).

This maps directly to the existing Pico state machine.

`device_state` is **actual state**. It must not be confused with `run_enable`,
which is commanded intent.

Example:

```text
run_enable   = 1
device_state = 2 (FAULT)
```

means the client still commands periodic sampling while the most recent
acquisition failed.

---

### 6.5 Input Registers 204–205 — `uptime_s`

| Property | Value |
|---|---|
| Type | unsigned `uint32` |
| Unit | seconds |
| Boot value | `0` |
| Word order | High word first |

Encoding:

```text
uint32 value = 0x12345678

Input Register 204 = 0x1234   # high word
Input Register 205 = 0x5678   # low word
```

Reconstruction:

```text
uptime_s = (reg204 << 16) | reg205
```

Why seconds instead of the original P1 `uptime_ms`:

- Milliseconds do not fit meaningfully in one 16-bit register.
- Minutes would lose too much reboot-detection resolution.
- A `uint32` in seconds provides approximately 136 years before wraparound
  while retaining 1-second resolution.

Primary use:

- detect probable Pico reboot when the observed value decreases sharply between
  polls.

#### Multi-register atomicity

Both words of `uptime_s` must be encoded from the **same latched uint32 value**
for a given Modbus response.

The server must not read the high word from one logical uptime value and the
low word from a later value.

Clients should read both registers (`204–205`) in the **same FC04 request**.

---

### 6.6 Input Registers 206–207 — `sample_counter`

| Property | Value |
|---|---|
| Type | unsigned `uint32` |
| Boot value | `0` |
| Word order | High word first |

Encoding uses the same high-word-first convention as `uptime_s`.

Semantics:

- Increments once for **every acquisition attempt**, including failed attempts.
- Resets to `0` on Pico reboot.
- Allows the client to distinguish a genuinely new acquisition from an unchanged
  physical value.
- Increments only after the Pico's main loop completes a scheduled or
  `read_now_trigger` acquisition attempt.

Example:

```text
poll A:
  sample_counter = 183
  temperature_c  = 2700

poll B:
  sample_counter = 184
  temperature_c  = 2700
```

The temperature did not change, but a new acquisition occurred.

#### Multi-register atomicity

Both words of `sample_counter` must be encoded from the **same latched uint32
value** for a given Modbus response.

Clients should read both registers (`206–207`) in the same FC04 request.

---

### 6.7 Input Register 208 — `register_map_version`

| Property | Value |
|---|---|
| Type | unsigned `uint16` |
| Boot value | `0x0100` |

Version encoding:

```text
0x0100 -> v1.0
0x0101 -> v1.1
0x0200 -> v2.0
```

Purpose:

- Lets the Pi determine which register-map contract the Pico implements.
- Provides an explicit compatibility check as the station evolves.
- The value is read-only and constant for a given firmware build.

For this implementation freeze:

```text
register_map_version = 0x0100
```

---

## 7. Acquisition snapshot consistency

The following fields form the **acquisition snapshot group**:

```text
200  temperature_c
201  humidity_pct
202  sensor_status
206–207  sample_counter
```

These fields must represent one committed acquisition epoch.

The Pico must not expose a partially updated acquisition snapshot while a sensor
read is being processed.

Conceptually:

```text
sensor acquisition attempt
    ↓
build complete candidate snapshot
    ↓
commit temperature / humidity / sensor_status / sample_counter together
    ↓
new acquisition snapshot becomes visible through Modbus
```

A client must not observe temperature from one acquisition combined with
humidity, status, or counter state from another partially committed acquisition.

For `read_now_trigger`, its firmware-controlled self-clear to `0` must become
observable consistently with the new committed acquisition snapshot.

The following Input Registers are **not** members of the acquisition snapshot
group and may change independently:

```text
203      device_state
204–205  uptime_s
208      register_map_version
```

### Block-read consistency

When responding to a contiguous FC04 read, the server should build the response
from values latched for that response so multi-register values cannot tear
between words.

The intended client polling operation remains:

```text
FC04
start = 200
count = 9
```

which reads offsets `200–208` in one transaction.

---

## 8. Modbus exception policy

The Pico must use standard Modbus exception responses where applicable.

| Condition | Exception |
|---|---|
| Unsupported function code | `01 — Illegal Function` |
| Address not implemented in the requested address space | `02 — Illegal Data Address` |
| Write value outside the allowed domain | `03 — Illegal Data Value` |

Examples:

- FC06 write `sample_interval_s = 1` → exception `03`.
- Read of unimplemented Input Register 999 → exception `02`.
- Unsupported operation against this v1 server → exception `01` where
  applicable.

Invalid requests must not crash or wedge the Pico firmware.

---

## 9. Reboot behavior

No v1 register state is persisted in Pico non-volatile storage.

After a Pico reboot:

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

The client may retain its own desired configuration and reconcile it after
detecting a reboot, but this is client behavior rather than persistence inside
the Pico.

A typical reconciliation sequence is:

```text
1. Detect device available again.
2. Read register_map_version.
3. Read run_enable / sample_interval_s / device_state.
4. Compare against client-side desired configuration.
5. Rewrite only values that must be restored.
```

Because `run_enable` and `sample_interval_s` writes are idempotent, safe client
retries do not restart scheduling when the requested value is already active.

`read_now_trigger` is never reconciled because it is a one-shot event.

---

## 10. What changed from the P1 JSON protocol

### Preserved conceptually

| P1 concept | Modbus v1 replacement |
|---|---|
| `START` / `STOP` | Coil 100 `run_enable` |
| `SET_INTERVAL` | Holding Register 101 `sample_interval_s` |
| `READ_NOW` | Coil 102 `read_now_trigger` (asynchronous) |
| temperature | Input Register 200 |
| humidity | Input Register 201 |
| status | Input Register 202 |
| Pico state | Input Register 203 |
| uptime | Input Registers 204–205 |
| sequence-like freshness | Input Registers 206–207 `sample_counter` |

### Deliberately changed

- Free-text command strings are removed.
- Unsolicited JSON telemetry is removed; the Pi now polls the Pico.
- Free-text sensor error detail is removed from the transport.
- The old P1 `sequence` concept is retained in the more explicit
  `sample_counter`.
- `uptime_ms` becomes `uptime_s` as a `uint32` across two registers.
- `READ_NOW` changes from a synchronous, blocking command to an asynchronous
  trigger.
- Control/configuration writes now have explicit idempotence rules to support
  retries safely.
- `8N1` is intentionally retained as the project's serial framing for P2.

---

## 11. Polling model

The intended P2 model is:

```text
Pico:
  acquires sensor data independently according to sample_interval_s

Pi:
  polls the latest committed snapshot using Modbus
```

The Pi does **not** rely on unsolicited telemetry from the Pico.

A typical client loop may read the contiguous Input Register block beginning at
200, decode it into a telemetry object, then feed the existing Pi-side queue /
persistence pipeline.

Example conceptual poll:

```text
Unit ID: 1
Function: FC04 Read Input Registers
Start: 200
Count: 9   # 200 through 208
```

The exact polling interval is a client implementation decision and does not
have to equal the Pico's sampling interval.

---

## 12. Testing requirements for P2-08

The automated suite should cover at least:

### Read path

- Read every implemented Coil.
- Read every implemented Holding Register.
- Read the complete Input Register block.
- Verify signed temperature decoding.
- Verify humidity scaling.
- Verify `uint32` reconstruction for `uptime_s`.
- Verify `uint32` reconstruction for `sample_counter`.
- Verify high-word-first packing.
- Verify `register_map_version == 0x0100`.

### Write path

- `run_enable = 1` from stopped enables periodic sampling.
- The first periodic sample after `0 → 1` occurs one full
  `sample_interval_s` later.
- Writing `run_enable = 1` while already enabled does not restart the scheduler.
- `run_enable = 0` disables periodic sampling.
- Writing `run_enable = 0` while already stopped is a no-op.
- `sample_interval_s` accepts boundary values `2` and `3600`.
- `sample_interval_s` rejects `1` and `3601` with exception `03`.
- Writing the current `sample_interval_s` value again is a no-op and does not
  move the next scheduled acquisition.
- Writing a different valid interval while running reschedules the next sample
  relative to the accepted write time.

### `read_now_trigger`

For deterministic tests, set `run_enable = 0` first.

- Capture `sample_counter` before the trigger.
- `read_now_trigger = 1` is accepted without waiting for sensor I/O.
- While pending, the Coil **may** read `1`, but observing this state is not
  required for the test to pass.
- Completion requires:
  - `read_now_trigger == 0`, and
  - `sample_counter > pre_trigger_sample_counter`.
- Writing `1` repeatedly while already pending produces only one acquisition.
- Writing `0` does not cancel or clear a pending request.
- The trigger works while `run_enable = 0`.
- With `run_enable = 1`, completion of `READ_NOW` moves the next periodic sample
  to one full `sample_interval_s` after the manual acquisition finishes.
- With `run_enable = 0`, no periodic acquisition follows the manual one.

### State / diagnostic behavior

- On boot: `sensor_status = NO_DATA` and `device_state = STOPPED`.
- After first successful acquisition: `sensor_status = OK`.
- On sensor read failure: `sensor_status = ERR_SENSOR` and the last valid
  measurement remains readable.
- After successful recovery: `sensor_status` returns to `OK`.
- `run_enable = 1` can coexist with `device_state = FAULT`.
- `sample_counter` advances exactly once per acquisition attempt, including
  failed attempts.
- `uptime_s` increases monotonically until reboot.
- Multi-register values do not tear within a single FC04 response.
- Acquisition snapshot fields are not exposed partially updated.

### Exception behavior

- Unsupported address → exception `02`.
- Out-of-range interval → exception `03`.
- Unsupported function → exception `01` where applicable.
- Invalid requests do not freeze the Pico or client.

### Reboot / reconciliation

- Pico reboot resets all documented volatile fields to their boot defaults.
- Client detects the new uptime/counter epoch.
- Client can restore remembered run/interval intent.
- Replaying the same already-active configuration is idempotent.
- Client never replays `read_now_trigger`.

---

## 13. Known v1 limitations / deferred work

- No free-text sensor error detail is transported.
- No persistent configuration in Pico flash.
- No explicit boot/session UUID; reboot detection relies on `uptime_s` /
  `sample_counter` epoch reset.
- No multi-client arbitration; architecture assumes one Modbus client/master.
- No RS-485 physical layer yet; P2 remains on direct UART TTL.
- Serial framing is deliberately `115200 8N1` for this project's learning path,
  not strict interoperability with arbitrary industrial Modbus devices.
- No custom Modbus function codes.
- No BME688-specific registers yet; those belong to a later phase and should
  extend the map without changing existing v1 meanings.

---

## 14. v1 implementation contract summary

```text
DEVICE
────────────────────────────────────────
Protocol        Modbus RTU
Unit ID         1
UART            115200 8N1
Physical P2     direct TTL UART

COILS
────────────────────────────────────────
100  run_enable
102  read_now_trigger   asynchronous, self-clearing by firmware

HOLDING REGISTERS
────────────────────────────────────────
101  sample_interval_s

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
208  register_map_version    uint16  (0x0100 = v1.0)

RESERVED
────────────────────────────────────────
103  unassigned in v1
```

## 15. Freeze rule

This document is now the **P2 implementation freeze** for the Modbus Register
Map v1.

During `P2-04` and `P2-05`, implementation should conform to this contract
rather than redesigning it opportunistically.

A semantic change is allowed only if implementation reveals:

1. an internal contradiction,
2. a behavior that cannot be implemented safely,
3. or a requirement that prevents `P2-08` from testing the contract
   deterministically.

Any such change must be documented before modifying both client and server.

If implementation and `P2-08` pass without such a blocker, `P2-09` publishes
this same contract as stable **Register Map v1.0**.

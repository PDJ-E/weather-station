# Register Map v1.2 (`0x0102`) — delta from v1.1

> **Status:** DRAFT — implemented in `firmware/pico/main.py` (Pico 2 socket firmware), **not yet validated on hardware**.
> **Base:** v1.0 contract in [`docs/phase_2/p2_modbus_register_map_v1.md`](../phase_2/p2_modbus_register_map_v1.md), extended by v1.1 (`0x0101`, BME688 registers 209–216).

## Why

The Pico 2 socket in the outdoor box has no DHT11 connector: the box air temperature will be measured by the Grove Temperature Sensor v1.2 on GP28 instead. The DHT11 is therefore **not installed** on this node. Under v1.1, a missing DHT11 can only be reported as `ERR_SENSOR`, which also forces `device_state = FAULT` on every acquisition. v1.2 adds a status that says the sensor is absent by design.

## Changes

| Item | v1.1 | v1.2 |
|---|---|---|
| Input Register 208 `register_map_version` | `0x0101` | `0x0102` |
| Input Register 202 `sensor_status` (DHT11) codes | `0 OK`, `1 ERR_SENSOR`, `2 NO_DATA` | adds **`3 NOT_INSTALLED`** |

Nothing else changes: register addresses, sizes, encodings, coils, the holding register, and the P2 semantics (idempotent START/STOP, asynchronous READ_NOW, `sample_counter` once per acquisition cycle) are identical.

## Behaviour when the DHT11 is `NOT_INSTALLED`

- Registers 200 and 201 are `0` and never change. Clients must ignore them while 202 reads `3`.
- Register 202 reads `3` from boot onward (instead of the `NO_DATA` boot default).
- The DHT11 is never read and its pin (GP15) is not configured.
- The DHT11 does **not** affect `device_state`: an acquisition is `RUNNING`/`STOPPED` when the BME688 succeeds, and `FAULT` only when the BME688 fails.

## Client impact

Pi-side scripts written for v1.1 reject `0x0102` (they check `version == 0x0101`), and some treat any DHT11 status other than `0` as an error. They are left unchanged as P3/P4 evidence. The socket bench test is [`experiments/rs485/p4_socket_environmental_test_pi.py`](../../experiments/rs485/p4_socket_environmental_test_pi.py). The long-run runner needs a v1.2-aware version before a long run on the socket firmware.

## Next version

Publishing the Grove box temperature will need new input registers and therefore another map version. It is not part of v1.2.

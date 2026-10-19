# Next Steps

Deferred work that has been identified, evaluated enough to be worth tracking, and intentionally postponed. Each entry records **why** it was deferred, the **evidence** behind it, and **what would trigger** picking it up again.

This file complements the project tracker: the tracker owns scheduling; this file owns the technical context so the reasoning is not lost.

---

## NS-01 — Kernel-controlled RS-485 direction on the Raspberry Pi 5

**Phase:** P4 (RS-485 field bus)
**Status:** Deferred — 2026-10-18
**Origin:** P4-08 bench validation (SparkFun RS-485 breakouts, Pi 5 ↔ Pico 2)

### Problem

The Pi currently drives the RS-485 transceiver direction (RTS on GPIO17) from userspace Python. Any delay between the end of the request and RTS going low keeps the Pi's receiver disabled; if the Pico answers during that window, the whole response is lost.

### Evidence (`experiments/rs485/rs485_turnaround_diag_*`, 200 requests per step)

| Pi method | RTS release, typical (hw) | Worst case, idle (hw) | Worst case, load (hw) |
|---|---|---|---|
| `flush` (`write` + `tcdrain`) | 4.3–6.8 ms | 19.9 ms | 11.7 ms |
| `computed` (busy-wait on frame time) | ~150–200 µs | 232 µs | **5.0 ms** |

- `flush` latencies cluster in ~4 ms steps, consistent with `tcdrain()` polling the transmitter in kernel ticks.
- `computed` is 30–40× better in the typical case, but under load (`stress-ng --cpu 4 --io 2`) the scheduler occasionally preempts the busy-wait, producing a 3–5 ms tail.
- Load: `stress-ng --cpu 4 --io 2`. Hardware timing measured on the Pico via an RTS sense wire (Pi GPIO17 → Pico GP7).

### Current mitigation (adopted in P4-08)

- Pi: `computed` transmit method (no `flush()`).
- Pico: `RS485_TURNAROUND_MS = 10` before enabling its driver (~2× the worst measured tail).
- Pi master: bounded Modbus retries (see NS-02).

Adequate for the bench and low-traffic sensor reads (DHT11 + BME688). Not a guarantee: the tail is bounded only by what was observed.

### Proposed solution

Let the serial driver toggle RTS from the TX-empty interrupt (Linux `TIOCSRS485` / RS-485 mode), with microsecond latency and no dependence on userspace scheduling. GPIO17 is already wired as RTS, which is the usual UART0 RTS pin on Raspberry Pi boards.

### To verify before implementing

- [ ] Correct Pi 5 device-tree overlay to expose UART0 with RTS on GPIO17.
- [ ] Whether the RP1 UART driver (PL011-compatible) supports RS-485 mode on the installed kernel.
- [ ] Re-run `rs485_turnaround_diag_pi.py` (idle + load) with kernel RS-485 mode and compare against the table above.
- [ ] If the tail drops to microseconds, reduce `RS485_TURNAROUND_MS` on the Pico (target 1–2 ms).

### Trigger to pick it up

Before multi-node polling on the bus (P4 distributed scheduling), or earlier if retries start appearing regularly in normal operation.

---

## NS-02 — Retry policy for the production RS-485 Modbus master

**Phase:** P4
**Status:** Partially addressed — 2026-10-18

The turnaround diagnostic also showed ~0.05% of requests not reaching the Pico as a valid frame, with **either** Pi method and with no RTS collision (`late_releases = 0`). These losses are independent of the turnaround problem, so retries are mandatory regardless of NS-01.

- Done: `rs485_environmental_test_pi.py` retries transport failures up to 3 times (same policy as the P2/P3 PyModbus client, `retries=3`). Modbus exception responses are not retried.
- Pending: carry the same policy into the production master that replaces the experiment scripts, with retry counters exposed for observability (README principle: a station must explain its own state).

---

## NS-03 — Turnaround soak test

**Phase:** P4
**Status:** Deferred — 2026-10-18

The chosen configuration (`computed` + 10 ms) is backed by 1,600 samples per condition. A longer run would bound the tail with more confidence:

```bash
python3 experiments/rs485/rs485_turnaround_diag_pi.py \
    --methods computed --turnarounds 10 --n 10000 --label soak
```

Run it under realistic load (PostgreSQL ingestion running) before fixing the turnaround for the outdoor deployment.

---

## NS-04 — Bus biasing and termination for the outdoor run

**Phase:** P6 (outdoor hardware)
**Status:** Deferred — 2026-10-18

On the bench, an idle bus produced junk bytes on the Pi receiver (e.g. `50 50 81 ff`) whenever no node was driving it: the SparkFun breakouts have no failsafe bias resistors, and the SP3485 failsafe only covers open inputs, not a terminated idle bus. Software now tolerates this (header resync on the Pi, frame resync on the Pico), but the long outdoor cable will need proper biasing (pull-up on A, pull-down on B) sized together with the termination.

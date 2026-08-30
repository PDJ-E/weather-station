# micropython-modbus 2.3.7 — RTU framing issue observed on Raspberry Pi Pico 2 (RP2)

> **Project:** Weather Station  
> **Phase:** P2 — Modbus RTU baseline  
> **Component:** Raspberry Pi Pico 2 running MicroPython  
> **Library:** `micropython-modbus 2.3.7` (`umodbus`)  
> **Transport under test:** UART0, GP0/GP1, `115200 8N1`, direct TTL UART  
> **Peer:** Raspberry Pi 5 using PyModbus `3.15.0` on `/dev/ttyAMA0`  
> **Status:** Reproduced, isolated, and worked around locally by monkey-patching `_uart_read_frame()`  
> **Upstream status:** Not yet reported upstream. A future issue / pull request may be appropriate after preparing a generic fix and a minimal reproducible test.

---

## 1. Executive summary

During P2 implementation, `micropython-modbus 2.3.7` failed to respond reliably to Modbus RTU requests on a Raspberry Pi Pico 2 even though the UART link itself was already known to work.

The failure was isolated to the library's RTU frame-receive path, specifically `Serial._uart_read_frame()`.

With normal PyModbus retries enabled, the library's internal frame reader returned **multiple repeated RTU requests concatenated into a single byte buffer**. A request that should have been exactly 8 bytes was observed as 32 bytes:

```text
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
```

The final two bytes (`BC 15`) are the CRC of the last individual 8-byte request, not of the full 32-byte buffer, so the library rejected the concatenated buffer as an invalid Modbus RTU frame.

With retries disabled (`retries=0`), the internal frame reader returned the correct 8-byte frame and the CRC validated successfully.

A local monkey patch that replaces `_uart_read_frame()` with a reader that consumes **exactly one 8-byte request at a time** for the Weather Station P2 function-code subset (`FC01`, `FC03`, `FC04`, `FC05`, `FC06`) resolved the issue completely.

After applying the patch, the full round-trip smoke test succeeded:

```text
FC01 Read Coils        : OK
FC05 Write Single Coil : OK
Unit ID 1              : OK
Coil 100               : OK
UART 115200 8N1        : OK

False -> True -> False
ROUND-TRIP MODBUS OK
```

This document records the investigation, evidence, current workaround, limitations, and what would be needed for a future upstream-quality fix.

---

## 2. Environment

### Raspberry Pi Pico 2

```text
Board        Raspberry Pi Pico 2
Platform     RP2 / RP2350
Runtime      MicroPython
UART         UART0
TX           GP0
RX           GP1
Baud         115200
Data bits    8
Parity       None
Stop bits    1
Framing      8N1
Unit ID      1
```

### Raspberry Pi 5

```text
Host         weather-core
Serial       /dev/ttyAMA0
Library      PyModbus 3.15.0
Mode         Modbus RTU client/master
Baud         115200
Framing      8N1
```

### Initial Modbus object under test

The first smoke test intentionally used only one real object from the frozen P2 Register Map:

```text
Coil 100 = run_enable
```

The Pi performed:

```text
FC01 -> read Coil 100
FC05 -> write Coil 100 = True
FC01 -> verify True
FC05 -> write Coil 100 = False
FC01 -> verify False
```

---

## 3. Initial failure

The Pi successfully opened `/dev/ttyAMA0`, but PyModbus received no response:

```text
No response received after 3 retries
```

and ultimately raised:

```text
pymodbus.exceptions.ModbusIOException:
Modbus Error: [Input/Output]
No response received after 3 retries, continue with next request
```

At this point, the physical UART path was already strongly suspected to be healthy because the same wiring had successfully passed the previous P1 UART tests, including:

```text
PING -> PONG
telemetry Pico -> Pi
```

---

## 4. First decisive observation: raw UART receive was correct

A raw UART sniffer was run on the Pico while PyModbus sent the FC01 request.

The Pico received:

```text
RX: 32 bytes

01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
```

Each individual 8-byte frame is a valid Modbus RTU request:

```text
01       Unit ID = 1
01       FC01 Read Coils
00 64    Start address = 100
00 01    Quantity = 1
BC 15    CRC16
```

The four copies corresponded to the original attempt plus PyModbus retries.

This proved:

```text
PyModbus request generation   OK
Pi UART TX                    OK
Physical wiring               OK
Pico UART RX                  OK
115200 8N1                    OK
Request bytes                 OK
Request CRC                   OK
```

The problem was therefore above raw UART reception.

---

## 5. Internal `umodbus` frame-reader observation

The next test bypassed `server.process()` and called the library's internal RTU reader directly:

```python
frame = server._itf._uart_read_frame(...)
```

With normal retries enabled, `micropython-modbus 2.3.7` returned:

```text
FRAME recibido por umodbus:
LEN: 32

HEX:
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15
01 01 00 64 00 01 BC 15

CRC recibido:  BC 15
CRC calculado: 4C 04
CRC OK: False
```

This is the central reproduction of the issue.

`get_request()` assumes the byte buffer returned by `_uart_read_frame()` contains exactly one Modbus RTU frame.

Therefore it performs:

```python
req_crc = req[-2:]
req_no_crc = req[:-2]
expected_crc = self._calculate_crc16(req_no_crc)
```

For a 32-byte concatenated buffer, the two CRC bytes at the end belong only to the fourth 8-byte request. They cannot validate the preceding 30 bytes.

The library therefore discards the buffer and returns no valid `Request`.

---

## 6. Control experiment: `retries=0`

PyModbus was temporarily configured with:

```python
retries=0
```

The same internal library reader then produced:

```text
FRAME recibido por umodbus:
LEN: 8

HEX:
01 01 00 64 00 01 BC 15

CRC recibido:  BC 15
CRC calculado: BC 15
CRC OK: True
```

This confirmed that:

1. The individual frame is valid.
2. The CRC implementation is correct for the individual request.
3. The baud rate is not the problem.
4. The framing failure appears when multiple requests accumulate and are returned as one buffer.

---

## 7. Hypothesis tested and rejected: `UART.read()` returning `b''`

One initial hypothesis was that the library might repeatedly reset its silence timer because:

```python
r = self._uart.read()

if r is not None:
    last_byte_ts = time.ticks_us()
```

would also treat `b''` as "data".

This was tested directly.

Observed behavior on this Pico 2:

```text
SIN DATOS: None
is None: True
bool: False
```

Therefore, in this environment, `UART.read()` with no data returns `None`, not `b''`.

That specific hypothesis was rejected.

This is important because the patch should not be documented as a fix for an unproven `b''` behavior.

---

## 8. `server.process()` did recognize the request

With the original server restored and `retries=0`, the Pico printed:

```text
>>> REQUEST MODBUS PROCESADO
```

This demonstrated that a single request could travel through:

```text
_uart_read_frame()
    ->
get_request()
    ->
Request(...)
    ->
process()
```

without being rejected.

However, the Pi still received no response.

---

## 9. Response path inspection

The library was instrumented around `_send()`.

For a `False` value in Coil 100, the Pico generated exactly the expected response:

```text
PDU: 01 01 00

ADU:
01 01 01 00 51 88

LEN: 6
_has_uart_flush: True
_t1char: 95

uart.write() retorno: 6
flush terminado
```

Expected response meaning:

```text
01       Unit ID = 1
01       FC01
01       Byte count
00       Coil value = False
51 88    CRC16
```

Despite that, the Pi still received zero bytes from this code path.

---

## 10. Raw bidirectional UART control test

To rule out hardware, MicroPython UART TX, and the wiring, the library was bypassed completely.

The Pico used a simple raw responder:

```text
receive request
    ->
uart.write(known valid response)
```

Result:

### Pico

```text
RX: 01 01 00 64 00 01 BC 15
TX: 01 01 01 00 51 88
bytes escritos: 6
```

### Pi

```text
TX: 01 01 00 64 00 01 BC 15
RX bytes: 6
RX: 01 01 01 00 51 88
```

This proved definitively:

```text
Pico UART RX               OK
Pico UART TX               OK
Pi UART TX                 OK
Pi UART RX                 OK
115200 8N1                 OK
Physical connection        OK
Response bytes             OK
```

---

## 11. Important follow-up: using `_uart_read_frame()` before raw TX

A test was then run using:

```text
umodbus _uart_read_frame()
    ->
direct uart.write(response)
```

without:

```text
get_request()
Request
process()
send_response()
```

The Pico reported:

```text
RX: 01 01 00 64 00 01 BC 15
TX: 01 01 01 00 51 88
write retorno: 6
```

but the Pi received:

```text
RX bytes: 0
```

This was the point where further diagnostic branching was stopped and a practical workaround was chosen.

The experiment strongly implicates the original RTU frame-reader path in the observed behavior on this setup.

However, the **exact lower-level mechanism by which the upstream `_uart_read_frame()` affects the subsequent TX path has not yet been proven**.

Therefore the technically careful wording is:

> `micropython-modbus 2.3.7` exhibits an RTU frame-receive / framing problem in this Raspberry Pi Pico 2 (RP2) environment. The original `_uart_read_frame()` was observed concatenating multiple requests into one buffer under retries, and using that receive path correlated with failed response delivery. Replacing that frame reader with a deterministic one-request-at-a-time reader resolved the problem.

It would be premature to claim a fully identified upstream root cause until a generic reproduction is prepared outside the Weather Station firmware.

---

## 12. Relevant upstream implementation

The problematic function in `umodbus/serial.py` is:

```python
def _uart_read_frame(self, timeout=None):
    received_bytes = bytearray()

    if timeout == 0 or timeout is None:
        timeout = 2 * self._inter_frame_delay

    start_us = time.ticks_us()

    while time.ticks_diff(time.ticks_us(), start_us) <= timeout:
        if self._uart.any():
            last_byte_ts = time.ticks_us()

            while (
                time.ticks_diff(time.ticks_us(), last_byte_ts)
                <= self._inter_frame_delay
            ):
                r = self._uart.read()

                if r is not None:
                    received_bytes.extend(r)
                    last_byte_ts = time.ticks_us()

        if len(received_bytes) > 0:
            return received_bytes

    return received_bytes
```

At `115200 baud`, the library configured:

```text
inter-frame delay = 1750 us
```

The implementation uses UART availability and silence timing to determine when a frame is complete.

The key observable failure is not that silence-based framing is inherently wrong; Modbus RTU itself is historically framed using silent intervals.

The issue is that, in this tested environment, the function did not reliably provide **one Modbus request per returned buffer**.

---

## 13. Local P2 workaround

The Weather Station P2 Register Map v1 intentionally supports only:

```text
FC01  Read Coils
FC03  Read Holding Registers
FC04  Read Input Registers
FC05  Write Single Coil
FC06  Write Single Holding Register
```

All **requests** for these five function codes have a fixed RTU request length of 8 bytes:

```text
Unit ID         1 byte
Function Code   1 byte
Address         2 bytes
Quantity/value  2 bytes
CRC             2 bytes
-----------------------
Total           8 bytes
```

Important:

> This does **not** limit Modbus responses to 8 bytes.

For example:

```text
FC04
start = 200
count = 9
```

still uses an 8-byte request, while the Pico may return a 23-byte response containing nine 16-bit Input Registers.

### Current compatibility patch

The project monkey-patches only the instantiated `Serial._uart_read_frame()` method.

The upstream library files remain untouched.

```python
import time

P2_MODBUS_REQUEST_LENGTH = 8
P2_FRAME_TIMEOUT_US = 10_000

def apply_umodbus_p2_patch(server):
    interface = server._itf

    def patched_uart_read_frame(timeout=None):
        received = bytearray()

        if not interface._uart.any():
            return received

        start_us = time.ticks_us()

        while len(received) < P2_MODBUS_REQUEST_LENGTH:
            available = interface._uart.any()

            if available:
                remaining = P2_MODBUS_REQUEST_LENGTH - len(received)

                bytes_to_read = min(
                    available,
                    remaining,
                )

                chunk = interface._uart.read(bytes_to_read)

                if chunk:
                    received.extend(chunk)

                    if len(received) == P2_MODBUS_REQUEST_LENGTH:
                        return received

            elapsed_us = time.ticks_diff(
                time.ticks_us(),
                start_us,
            )

            if elapsed_us > P2_FRAME_TIMEOUT_US:
                break

            time.sleep_us(50)

        return received

    interface._uart_read_frame = patched_uart_read_frame
```

Usage:

```python
server = ModbusRTU(...)

apply_umodbus_p2_patch(server)

server.setup_registers(...)

while True:
    server.process()
```

---

## 14. Verification after applying the patch

With normal PyModbus retries restored, the complete Coil smoke test passed:

```text
========================================
 Pi 5 Modbus RTU smoke test
========================================

Puerto    : /dev/ttyAMA0
Unit ID   : 1
Serial    : 115200 8N1
Coil      : 100

[1] Abriendo puerto serial...
    Puerto abierto.

[2] Leyendo Coil 100...
    Valor inicial: False

[3] Escribiendo Coil 100 = True...
    Escritura aceptada.

[4] Releyendo Coil 100...
    Valor leído: True
    OK.

[5] Escribiendo Coil 100 = False...
    Escritura aceptada.

[6] Releyendo Coil 100...
    Valor leído: False
    OK.

========================================
 ROUND-TRIP MODBUS OK
========================================

FC01 Read Coils       : OK
FC05 Write Single Coil: OK
Unit ID 1             : OK
Coil 100              : OK
UART 115200 8N1       : OK

Coil 100 restaurado a False.
Puerto serial cerrado.
```

This is the strongest current evidence that replacing the upstream frame reader resolved the practical failure.

---

## 15. Why the workaround is acceptable for P2

P2 is intentionally a learning / baseline Modbus implementation before the Weather Station eventually moves to its own protocol.

The fixed-request-length workaround is acceptable because the frozen v1 contract only requires:

```text
FC01
FC03
FC04
FC05
FC06
```

All five requests are 8 bytes.

The patch does not modify:

```text
CRC generation
CRC validation
register map
response generation
telemetry scaling
32-bit register packing
Modbus exception semantics
UART baud/framing
```

It only changes how the server isolates one incoming request from the UART stream.

---

## 16. Limitations of the workaround

The current patch is **project-specific**, not a general Modbus RTU fix.

It must be revisited if any variable-length request is introduced, especially:

```text
FC15  Write Multiple Coils
FC16  Write Multiple Registers
```

Those requests contain a byte count and payload, so their total request length is not fixed at 8 bytes.

The patch must also be reevaluated when:

```text
micropython-modbus is upgraded
MicroPython firmware is upgraded
the target MCU changes
the transport moves from direct TTL UART to RS-485
new Modbus function codes are added
```

---

## 17. Why not edit `/lib/umodbus/serial.py` directly

The workaround is applied through monkey-patching from Weather Station firmware instead of modifying the installed package.

Reasons:

1. The original dependency remains untouched.
2. The workaround is version-controlled with the project.
3. The reason for the patch is visible to future maintainers.
4. Updating/reinstalling `micropython-modbus` does not silently destroy a local source edit.
5. The patch can be removed cleanly when no longer needed.
6. A future upstream fix can be tested by simply disabling the compatibility layer.

Current experimental location:

```text
experiments/
└── modbus/
    ├── modbus_coil_test_pi.py
    ├── modbus_coil_test_pico.py
    └── pico_modbus_patch.py
```

If retained for actual P2 firmware, it should later move to an explicit compatibility location such as:

```text
firmware/
└── pico/
    └── compat/
        └── umodbus_2_3_7.py
```

---

## 18. Potential upstream issue / pull request

The current Weather Station patch should **not** be submitted upstream as-is because it assumes every supported request is exactly 8 bytes.

A proper upstream fix should be generic.

A future upstream contribution should ideally include:

### Minimal reproduction

A Pico 2 server that:

```text
UART0
115200 8N1
Unit ID 1
one Coil
```

and a host client that repeatedly performs:

```text
FC01 address 100 count 1
```

with retries enabled.

The reproduction should log:

```text
timestamp
uart.any()
length returned by UART.read()
frame length returned by _uart_read_frame()
raw frame hex
CRC status
```

### Automated regression expectation

The test should prove that:

```text
request A
silence
request A retry
silence
request A retry
```

is returned by the RTU reader as:

```text
frame 1 = request A
frame 2 = request A
frame 3 = request A
```

and never as:

```text
frame = request A + request A + request A
```

### Generic framing approach

A general implementation could use function-code-aware expected lengths.

For fixed-length requests:

```text
FC01 -> 8 bytes
FC02 -> 8 bytes
FC03 -> 8 bytes
FC04 -> 8 bytes
FC05 -> 8 bytes
FC06 -> 8 bytes
```

For variable-length requests such as FC15/FC16:

```text
read fixed header
    ->
read byte-count field
    ->
calculate expected ADU length
    ->
read exactly remaining bytes
    ->
validate CRC
```

Silence timing can remain part of RTU framing / timeout handling, but the parser should avoid consuming bytes belonging to a later complete request when the expected length of the current request is already known.

### Important caution

The investigation demonstrated the failure and an effective workaround, but it did **not yet prove the exact internal RP2 / MicroPython low-level cause**.

An upstream report should therefore initially say:

> "Observed on Raspberry Pi Pico 2 / RP2 with MicroPython and `micropython-modbus 2.3.7`"

rather than:

> "All RP2 targets are broken."

That distinction matters for a credible bug report.

---

## 19. Suggested future upstream issue title

```text
RTU _uart_read_frame may concatenate retried requests on Raspberry Pi Pico 2 (RP2)
```

Alternative:

```text
Serial._uart_read_frame framing issue on RP2: multiple RTU requests returned as one buffer
```

---

## 20. Suggested upstream issue summary

```text
Environment:
- Raspberry Pi Pico 2 (RP2350 / RP2)
- MicroPython
- micropython-modbus 2.3.7
- UART0 GP0/GP1
- 115200 8N1
- Modbus RTU Unit ID 1
- PyModbus 3.15.0 master

Observed:
With retries enabled, Serial._uart_read_frame() returned four repeated
8-byte FC01 requests as one 32-byte buffer. get_request() then evaluated
the CRC over the combined buffer and discarded it.

With retries=0, the same reader returned one 8-byte request and its CRC
validated correctly.

A local reader that consumes one expected request at a time resolves the
problem and restores FC01/FC05 round-trip communication.

A generic fix should not assume 8-byte requests globally because functions
such as FC15/FC16 are variable-length.
```

---

## 21. Evidence matrix

| Test | Result |
|---|---|
| P1 raw UART Pi -> Pico | PASS |
| P1 raw UART Pico -> Pi | PASS |
| PyModbus FC01 raw bytes arrive at Pico | PASS |
| Individual request CRC | PASS |
| `umodbus` reader with retries | FAIL — 32-byte concatenated buffer |
| `umodbus` reader with `retries=0` | PASS — 8 bytes / CRC valid |
| `server.process()` recognizes single request | PASS |
| Raw Pico responder sends valid Modbus response | PASS |
| Pi receives raw Pico response | PASS |
| Local `_uart_read_frame()` monkey patch | PASS |
| FC01 read after patch | PASS |
| FC05 write `False -> True` after patch | PASS |
| FC05 write `True -> False` after patch | PASS |
| Normal PyModbus round-trip after patch | PASS |

---

## 22. Final project decision

For Weather Station P2:

```text
micropython-modbus 2.3.7
        +
project-owned monkey patch
        ->
accepted temporarily
```

The upstream package remains unmodified.

The compatibility patch is considered part of the P2 implementation environment and should remain explicitly documented until one of the following occurs:

```text
1. an upstream release fixes the behavior;
2. a generic project-local RTU reader replaces it;
3. Weather Station leaves Modbus for its own protocol.
```

The successful smoke test establishes that this workaround is sufficient to continue P2-04 without further UART/framing investigation at this time.

---

## 23. Short technical conclusion

The issue observed during P2 was not caused by:

```text
baudrate
8N1 framing choice
physical UART wiring
Pi UART
Pico UART hardware
PyModbus request encoding
CRC generation
Unit ID
Coil addressing
```

The reproducible problem occurred in the RTU request-framing path of
`micropython-modbus 2.3.7` on the tested Pico 2 environment.

The upstream `_uart_read_frame()` returned multiple retried requests as one
buffer, which caused CRC validation failure. Replacing that receive path with
a deterministic one-request-at-a-time reader for the P2 v1 fixed-length
function-code subset restored successful Modbus RTU communication.

The project can now continue P2-04 while preserving this investigation for a
possible future upstream issue or pull request.
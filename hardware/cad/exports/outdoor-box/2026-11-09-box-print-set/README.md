# Outdoor box print set — 2026-11-09

Printable STL exports of the functional parts that mount the electronics inside the outdoor IP66/IP67 enclosure: the Raspberry Pi Pico 2 plug-in socket, the board holders, and the PWR-DIST spacers.

> **Status:** released for printing on 2026-11-09. These are manufacturing exports, not qualification evidence. Fit inside the enclosure, the complete two-level assembly, and outdoor exposure have **not** been validated by this release.

## Parts

All dimensions are the STL bounding box (X × Y × Z, mm) in print orientation. Every file is already laid flat with its bed face at Z = 0. Print each part once unless the quantity says otherwise.

| # | File | Part | Qty | Size (mm) | Fasteners / hardware |
|---|---|---|---|---|---|
| 01 | `01-pico-socket-body-v4.stl` | Pico 2 socket body (Dupont → JST, 8 JST receptacles) | 1 | 38.1 × 69.5 × 18.8 | 2 × M3x12 from the top through the corner lugs into M3x5x5 heat-set inserts in the level-2 deck; CA drop on each JST receptacle |
| 02 | `02-pico-socket-bottom-cover-v4.stl` | Pico 2 socket bottom cover | 1 | 38.1 × 69.5 × 1.5 | Clamped by the same 2 × M3x12 |
| 03 | `03-pico-socket-retention-frame-v4.stl` | Hinged retention frame | 1 | 20.0 × 62.5 × 6.3 | 2 × 1.75 mm filament pins, ~22 mm (one per end: hinge and latch) |
| 04a | `04a-pico-socket-post-block-v4-hinge.stl` | Post block, hinge end | 1 | 14.4 × 27.2 × 5.0 | 3 × 1.75 mm filament pegs, 11 mm, into the body |
| 04b | `04b-pico-socket-post-block-v4-latch.stl` | Post block, latch end | 1 | 14.4 × 27.2 × 5.0 | 3 × 1.75 mm filament pegs, 11 mm, into the body |
| 05b | `05b-pico-socket-bus-ladder-tray-v2.stl` | 3V3 / GND bus ladder tray (two stacked rails) | 1 | 12.4 × 31.2 × 4.2 | 2 × 1.75 mm filament pegs, 8 mm, into the body |
| 10 | `10-buck-a-holder-v3.stl` | Holder for buck A (Pololu D24V7F5) | 1 | 17.4 × 24.3 × 5.0 | 2 × M2x8 + nut through the enclosure grid |
| 11 | `11-buck-b-holder-v2.stl` | Holder for buck B (Pololu D24V22F5) | 1 | 17.8 × 17.8 × 5.0 | Board: M2x4 self-tapping into the post. **Parked:** buck B is not owned yet |
| 12 | `12-grove-holder-v2.stl` | Holder for the Grove Temperature Sensor v1.2 (box air temperature) | 1 | 25.0 × 6.0 × 7.0 | Board: M2x4 self-tapping into the post |
| 13 | `13-rs485-holder-v3.stl` | Holder for a SparkFun RS-485 (SP3485) breakout | **2** | 23.8 × 28.9 × 6.8 | Board: M2x4 self-tapping; holder → deck: M2 into M2x3x3.5 heat-set inserts |
| 14 | `14-pwr-dist-spacer-v2.stl` | PWR-DIST Rev A standoff, Ø7 × 5 mm, Ø3.4 bore | **4** | 7.0 × 7.0 × 5.0 | M3x12 from the top → PCB → spacer → grid → M3 nut; the 4 grid holes are drilled out to Ø3.4 |

The source export contained identical position copies of parts 13 (×2) and 14 (×4); only one file per geometry is kept here, with its quantity in the table. The two post blocks (04a/04b) are different files and are both kept.

### Superseded, not included

- **Bus ladder tray v1:** its 0.4 mm separator printed weak and stringy. Replaced by v2 (0.6 mm separator, 0.3 × 45° entry chamfers; same envelope and peg holes, so it fits the v4 body and cover).

### Not part of this set

- Level-2 deck and its pillars (still being modeled: cable notches and pillar clips).
- Buck C cradle (stop design not decided).
- RJ45 coupler nut (printed separately as a fit test).

## Printing

- **Printer:** Elegoo Centauri Carbon 2, single material per print (no multi-color unit).
- **Material:** ASA (Polymaker PolyLite ASA) for all parts in the box.
- **Layer height:** 0.20 mm for the set; the bus ladder tray v2 is better printed alone at 0.12 mm (5 layers in the separator), 100 % infill, with bridge settings slowed down.
- **Walls:** 4 walls on the Pico socket parts (as noted in the original export names).
- **Supports:** none needed.
- Filament pins and pegs are cut from 1.75 mm filament; they are not printed.

## Source CAD

The parts were designed in Fusion 360. The editable source is **not yet published** in [`hardware/cad/source/`](../../../source/):

| Fusion document | Role |
|---|---|
| Box Inc v2 | Master assembly of the box (grid, PWR-DIST, holders, level-2 support) |
| Small Electronic Parts (Box) | Print sheet the holder and spacer STLs were exported from |
| Pico Socket V2 / Pico Socket Parts | Pico socket assembly and its print layout |
| Box Grid | Parametric model of the enclosure's mounting plate (135 × 185 × 3.05 mm, 5 mm pitch) |

The copies of the ladder tray inside "Pico Socket V2" and "Box Inc v2" are still v1 (same envelope); this does not affect the exported v2 STL.

## Checksums (SHA-256, first 12 characters)

| File | SHA-256 |
|---|---|
| `01-pico-socket-body-v4.stl` | `a986e8c487cd` |
| `02-pico-socket-bottom-cover-v4.stl` | `16d054f0c92c` |
| `03-pico-socket-retention-frame-v4.stl` | `7ff581e26fae` |
| `04a-pico-socket-post-block-v4-hinge.stl` | `dfda6b5d629c` |
| `04b-pico-socket-post-block-v4-latch.stl` | `a3c2480645ed` |
| `05b-pico-socket-bus-ladder-tray-v2.stl` | `42198789aecc` |
| `10-buck-a-holder-v3.stl` | `34ad75a0e98e` |
| `11-buck-b-holder-v2.stl` | `fa26357ba789` |
| `12-grove-holder-v2.stl` | `1aca1863c788` |
| `13-rs485-holder-v3.stl` | `b1f39f039459` |
| `14-pwr-dist-spacer-v2.stl` | `be10c59f1e43` |

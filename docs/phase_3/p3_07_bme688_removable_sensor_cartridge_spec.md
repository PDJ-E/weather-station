# Weather Station — P3-07 BME688 Removable Sensor Cartridge Specification

> **Status:** STABLE / FROZEN  
> **Published by:** P3-07  
> **Phase:** P3 — BME688 environmental sensor integration  
> **Sensor board:** Pimoroni BME688 breakout  
> **Purpose:** Mechanical requirements for a removable environmental-sensor cartridge  
> **Units:** millimetres unless otherwise stated  
> **Validation basis:** Direct caliper measurements of the physical breakout and CAD reference model created in Fusion  
>
> This document defines the mechanical reference geometry and functional requirements
> for the removable BME688 sensor cartridge. It intentionally specifies **what the
> cartridge must accomplish**, not the final detailed geometry of the cartridge itself.
> The later CAD implementation may evolve as long as these requirements are preserved.

---

## 1. Scope

P3 established the BME688 as the primary environmental sensor for temperature,
humidity, pressure, and raw gas resistance. P3-07 closes the phase by freezing the
mechanical requirements that allow the sensor to be integrated into the future outdoor
node without compromising measurement quality or maintainability.

The cartridge shall provide:

- direct exposure of the BME688 sensing package to ambient air,
- repeatable mechanical positioning,
- physical separation from meaningful heat sources,
- protection from direct rain or droplets without sealing the sensor from ambient air,
- removable electrical and mechanical interfaces,
- straightforward inspection, cleaning, replacement, and future redesign.

This specification does **not** require that the final cartridge already be modeled,
printed, or field validated. Those activities belong to a later mechanical-design and
integration task.

---

## 2. Reference coordinate system

For the breakout mechanical reference, use the PCB top view with the origin at the
**lower-left corner of the nominal 19.00 mm × 19.00 mm PCB envelope**:

```text
Y
^
|
|       left hole        BME688        right hole
|          o                []              o
|
|
|
+-------------------------------------------------> X
origin
```

The positive X-axis runs from left to right and the positive Y-axis runs from bottom to
top.

The BME688 package center, rather than the geometric center of the PCB, is the preferred
reference point for positioning the sensing element inside the ventilated measurement
volume.

---

## 3. Pimoroni BME688 breakout — measured mechanical reference

### 3.1 PCB envelope

| Parameter | Value |
|---|---:|
| Nominal PCB width | 19.00 mm |
| Nominal PCB height | 19.00 mm |
| PCB thickness | 1.60 mm |

The breakout has two lower side recesses which reduce the width of the lower central
section.

| Lower geometry parameter | Value |
|---|---:|
| Left recess width | 1.90 mm |
| Right recess width | 1.90 mm |
| Recess height | 5.10 mm |
| Lower central section width | 15.20 mm |

These features are mechanically relevant because future retention features must not
assume a full 19.00 mm-wide rectangular PCB along the entire board height.

### 3.2 Mounting holes

The board contains two mounting holes near the upper edge.

| Parameter | Value |
|---|---:|
| Quantity | 2 |
| Hole diameter | 2.50 mm |
| Hole radius | 1.25 mm |
| Hole-perimeter clearance from top PCB edge | 1.50 mm |
| Hole-perimeter clearance from corresponding side edge | 1.50 mm |
| Horizontal center-to-center spacing | 13.50 mm |

Because each Ø2.50 mm hole is tangent to construction lines located 1.50 mm from the
adjacent PCB edges, each hole center lies 2.75 mm from those edges:

```text
1.50 mm edge clearance + 1.25 mm radius = 2.75 mm center offset
```

Therefore the measured nominal hole-center coordinates are:

| Mounting hole | X | Y |
|---|---:|---:|
| Left | 2.75 mm | 16.25 mm |
| Right | 16.25 mm | 16.25 mm |

The mounting-hole midpoint lies at X = 9.50 mm, aligned with the horizontal center of
the BME688 package.

### 3.3 BME688 sensing package

| Parameter | Value |
|---|---:|
| Package footprint | 3.00 mm × 3.00 mm |
| Package height above PCB | 0.93 mm |
| Package center X | 9.50 mm |
| Package center Y | 16.00 mm |
| Package top-edge clearance from PCB top edge | 1.50 mm |

The package is centered horizontally on the PCB and lies very close to the upper PCB
edge. Its center is only 0.25 mm below the mounting-hole centerline:

```text
16.25 mm - 16.00 mm = 0.25 mm
```

This geometry is favorable for a cartridge design in which the sensing package projects
into a ventilated measurement region while the majority of the PCB remains supported
below it.

### 3.4 Qw/ST connector keep-out

The Pimoroni breakout includes a lateral Qw/ST connector.

| Parameter | Measured / modeled value |
|---|---:|
| Approximate connector footprint | 4.30 mm × 6.00 mm |
| Connector height above PCB | 2.50 mm |

The connector therefore produces a local vertical envelope of approximately:

```text
PCB thickness                  1.60 mm
connector height above PCB   + 2.50 mm
                              --------
local total envelope            4.10 mm
```

For comparison, the BME688 package reaches approximately 2.53 mm above the PCB bottom
surface:

```text
PCB thickness          1.60 mm
BME688 package height +0.93 mm
                       -------
                       2.53 mm
```

The connector is therefore expected to govern the largest local vertical clearance of
the simplified mechanical reference model.

### 3.5 Simplified CAD representation

The reference component in Fusion does not need to reproduce every resistor, capacitor,
or IC on the breakout. The minimum useful mechanical representation consists of:

1. PCB outline and thickness.
2. Both Ø2.50 mm mounting holes.
3. BME688 package volume and exact sensing-package center.
4. Qw/ST connector keep-out volume.
5. Any additional simplified component keep-out volume needed to prevent mechanical
   interference.

The CAD model is a **mechanical reference**, not an electrical or manufacturing model
of the Pimoroni PCB.

---

## 4. Functional mechanical requirements

### P3-07-MEC-01 — Removable cartridge

The BME688 breakout shall be installed on an independent removable cartridge or sensor
carrier rather than being permanently bonded into the station enclosure.

### P3-07-MEC-02 — Non-destructive serviceability

The breakout shall be removable and replaceable without destroying printed components
or requiring destructive disassembly of the outdoor node.

### P3-07-MEC-03 — Mechanical retention

The breakout shall use a reversible mechanical retention method. Adhesive encapsulation
or permanent bonding is not acceptable as the primary retention method.

The two Ø2.50 mm mounting holes are the preferred mounting references. The final design
may use screws, posts, pins, spacers, or another mechanically reversible feature.

### P3-07-MEC-04 — Sensing package exposed to ambient air

The BME688 sensing package shall have direct communication with the ambient-air volume
being measured.

No solid wall shall be located immediately in front of the sensing package in a way that
creates a stagnant sealed pocket.

### P3-07-MEC-05 — Sensor-centered measurement volume

The preferred center of the ventilated measurement region shall be the BME688 package
center:

```text
X = 9.50 mm
Y = 16.00 mm
```

The complete PCB does not have to be geometrically centered inside the cartridge if an
offset places the sensing package in a better measurement position.

### P3-07-MEC-06 — Airflow clearance

The cartridge shall preserve free space around the sensing package and around the nearby
PCB region so that ambient air can circulate naturally.

The PCB shall not be clamped directly against a solid wall in the sensing region.

### P3-07-MEC-07 — Rain and droplet protection

The cartridge shall prevent direct rain, splashes, or retained droplets from reaching or
resting on the BME688 breakout while remaining ventilated.

The cartridge shall **not** be airtight.

The final assembly is expected to work in conjunction with an external radiation shield
or equivalent weather-protection geometry.

### P3-07-MEC-08 — Drainage and orientation

The mounting orientation and surrounding geometry shall avoid creating locations where
water can collect directly on the PCB or BME688 package.

Any ingress path that can admit droplets should provide a corresponding drainage or
escape path rather than a closed water trap.

### P3-07-MEC-09 — Separation from heat sources

The BME688 sensing region shall be physically separated from meaningful internal heat
sources, including where applicable:

- Raspberry Pi Pico,
- DC/DC converters,
- voltage regulators,
- RS-485 transceiver electronics,
- Raspberry Pi Zero or camera electronics,
- motors or motor drivers,
- other components with meaningful self-heating.

The sensor cartridge should be located in a thermally distinct ventilated region rather
than sharing a small stagnant enclosure volume with these components.

### P3-07-MEC-10 — Minimize conductive thermal coupling

The breakout should be supported through discrete mechanical contact points rather than
having its entire PCB surface pressed against a printed wall.

Standoffs, spacers, or equivalent point-support geometry are preferred around the
mounting holes.

### P3-07-MEC-11 — Removable electrical connection

The sensor electrical interface shall be disconnectable so that the cartridge can be
removed without desoldering wires.

The current electrical signals are:

```text
VCC
GND
SDA
SCL
```

The final connector technology is **not frozen by P3-07**. Qw/ST, JST, or another
appropriate removable connector may be selected during detailed node design.

### P3-07-MEC-12 — Short I2C interconnect

The I2C connection between the BME688 breakout and its controller shall be kept short and
routed as a local sensor connection rather than as a long external field bus.

### P3-07-MEC-13 — Connector access

The final cartridge geometry shall preserve enough access to insert and remove the chosen
sensor connector without first removing the complete outdoor node or damaging adjacent
wiring.

### P3-07-MEC-14 — Inspection and cleaning

The breakout and sensing region shall be accessible for visual inspection and reasonable
cleaning once the cartridge is removed.

### P3-07-MEC-15 — Printable and tolerant geometry

The cartridge shall be designed for practical FDM printing and shall avoid relying on
unnecessarily tight dimensional fits.

Critical retention dimensions should use explicit manufacturing clearances appropriate
to the printer/material combination instead of assuming CAD nominal dimensions will be
reproduced exactly.

---

## 5. Recommended mechanical architecture

The preferred concept is a removable carrier or sled in which the Pimoroni breakout is
held on two discrete supports associated with the upper mounting holes.

Conceptually:

```text
             ambient airflow
          v       v       v

              BME688
                 []
          o               o
          |               |
      standoff        standoff
          |               |
          +---------------+
             cartridge
```

This arrangement has several advantages:

- the sensing package can remain exposed to the ventilated volume,
- the PCB can be separated from the cartridge base,
- the board can be removed using its mounting references,
- conduction from the surrounding printed structure is reduced,
- the bulk of the PCB may remain outside the most sensitive airflow region,
- future cartridge redesigns do not require redesigning the BME688 reference component.

A single retaining screw, captive feature, sliding guide, or comparable reversible
mechanism may be used to retain the complete cartridge in the station structure. The
exact mechanism is intentionally deferred.

---

## 6. CAD design rules for later implementation

When the cartridge is modeled, the Pimoroni breakout should be inserted as a reference
component rather than redrawn independently inside the cartridge design.

The following references should drive the design:

```text
sensor_center_x = 9.50 mm
sensor_center_y = 16.00 mm

mount_hole_diameter = 2.50 mm
mount_hole_spacing_x = 13.50 mm

pcb_width = 19.00 mm
pcb_height = 19.00 mm
pcb_thickness = 1.60 mm

bme688_package_width = 3.00 mm
bme688_package_length = 3.00 mm
bme688_package_height = 0.93 mm
```

Recommended Fusion user parameters may use these names or equivalent project naming.

The mounting-hole location is preferably constrained from the edge-clearance geometry:

```text
hole_center_offset = edge_clearance + hole_radius
                   = 1.50 + 1.25
                   = 2.75 mm
```

This preserves the actual measured geometric relationship if later measurements refine
the nominal hole diameter.

---

## 7. Environmental measurement considerations

### 7.1 Temperature and humidity

P3-06 demonstrated that the BME688 temperature channel remained stable over the long-run
test and tracked the colocated DHT11 closely. Because temperature and relative humidity
can be biased by local self-heating, the cartridge must not introduce a new stagnant
thermal environment around the sensing package.

### 7.2 Pressure

The pressure channel does not require a directional opening, but the sensor still needs
communication with ambient air. A hermetically sealed cartridge would defeat this
requirement.

### 7.3 Gas resistance

The BME688 gas channel depends on its internal heater and ambient gas exposure. The
sensing package therefore requires free air exchange. The cartridge shall not apply a
coating, membrane, adhesive, conformal layer, or printed feature directly over the gas
sensor package unless that material has been deliberately selected and validated for gas
sensor use.

The gas warm-up logic implemented by P3 is a software/data-quality concern and does not
remove the need for appropriate physical airflow around the package.

---

## 8. Requirements intentionally deferred

The following decisions are **not frozen by P3-07**:

- final cartridge outer dimensions,
- exact radiation-shield geometry,
- exact cartridge retention mechanism,
- exact screw size or insert selection,
- final material selection for the outdoor printed part,
- Qw/ST versus JST versus another removable electrical connector,
- final cable strain-relief implementation,
- exact airflow opening dimensions,
- final rain labyrinth or drainage geometry,
- manufacturing tolerances of the eventual printed cartridge.

These are detailed-design decisions and may be selected later without reopening P3-07 as
long as the functional requirements in this document continue to be satisfied.

---

## 9. P3-07 acceptance criteria

P3-07 is considered complete when all of the following are true:

- [x] The physical Pimoroni breakout has been measured sufficiently to create a useful
      mechanical CAD reference.
- [x] PCB envelope and thickness are documented.
- [x] Both mounting holes are dimensioned and positioned.
- [x] BME688 package size, height, and package-center location are documented.
- [x] Major connector keep-out geometry is represented.
- [x] The BME688 package is defined as the sensing-volume positioning reference.
- [x] Direct ambient-air exposure is required.
- [x] Removable mechanical installation is required.
- [x] Removable electrical connection is required.
- [x] Separation from meaningful heat sources is required.
- [x] Direct rain/droplet protection without airtight encapsulation is required.
- [x] Inspection, cleaning, and replacement requirements are defined.

**P3-07 result: PASS / DONE.**

---

## 10. Phase P3 closure

With P3-07 complete, Phase P3 has established both the electrical/software integration
and the mechanical integration requirements for the BME688 environmental sensor.

P3 delivered, at minimum:

- working BME688 communication on the Pico,
- common environmental-sensor abstraction alongside the DHT11,
- Modbus Register Map v1.1 environmental telemetry,
- Pi-side decoding and PostgreSQL persistence,
- short-duration validation of ranges and behavior,
- a successful 24-hour long-duration acquisition run,
- gas-heater warm-up / `gas_ready` handling,
- a measured mechanical CAD reference for the Pimoroni breakout,
- this removable-cartridge mechanical specification.

The next mechanical work should treat this specification as an input to detailed CAD,
printing, assembly, and physical environmental validation rather than as an open P3
requirement.

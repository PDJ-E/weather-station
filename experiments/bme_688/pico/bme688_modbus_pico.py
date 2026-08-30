import time

from pymodbus.client import ModbusSerialClient


# ================================================================
# Configuration
# ================================================================

PORT = "/dev/ttyAMA0"
UNIT_ID = 1
BAUDRATE = 115200

EXPECTED_MAP_VERSION = 0x0101

COIL_RUN_ENABLE = 100
COIL_READ_NOW = 102

IREG_START = 200
IREG_COUNT = 17


# ================================================================
# Helpers
# ================================================================

def decode_int16(value):
    value &= 0xFFFF

    if value & 0x8000:
        value -= 0x10000

    return value


def join_u32(high, low):
    return (
        ((high & 0xFFFF) << 16)
        | (low & 0xFFFF)
    )


def ensure_ok(response, operation):
    if response is None:
        raise RuntimeError(
            "{}: no response".format(operation)
        )

    if response.isError():
        raise RuntimeError(
            "{}: {}".format(
                operation,
                response,
            )
        )

    return response


# ================================================================
# Client
# ================================================================

client = ModbusSerialClient(
    port=PORT,
    baudrate=BAUDRATE,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=1.0,
    retries=3,
)


print()
print("========================================")
print(" P3-04 Environmental Modbus smoke test")
print("========================================")
print()


# ================================================================
# Connect
# ================================================================

print(
    "[1/5] Opening",
    PORT,
    "..."
)

if not client.connect():
    raise RuntimeError(
        "No se pudo abrir {}".format(PORT)
    )

print("      Serial port OK")


try:

    # ============================================================
    # Register Map version
    # ============================================================

    print()
    print("[2/5] Reading Register Map version...")

    response = client.read_input_registers(
        address=208,
        count=1,
        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read register 208",
    )

    version = response.registers[0]

    print(
        "      Version: 0x{:04X}".format(
            version
        )
    )

    if version != EXPECTED_MAP_VERSION:
        raise RuntimeError(
            "Expected Register Map 0x{:04X}, got 0x{:04X}".format(
                EXPECTED_MAP_VERSION,
                version,
            )
        )


    # ============================================================
    # Current sample counter
    # ============================================================

    print()
    print("[3/5] Reading current sample counter...")

    response = client.read_input_registers(
        address=206,
        count=2,
        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read sample_counter",
    )

    counter_before = join_u32(
        response.registers[0],
        response.registers[1],
    )

    print(
        "      Before:",
        counter_before,
    )


    # ============================================================
    # READ_NOW
    # ============================================================

    print()
    print("[4/5] Triggering READ_NOW...")

    response = client.write_coil(
        address=COIL_READ_NOW,
        value=True,
        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Write READ_NOW",
    )

    print(
        "      Trigger accepted."
    )


    # ------------------------------------------------------------
    # Wait for:
    #
    #     Coil 102 == False
    #     AND
    #     sample_counter > counter_before
    # ------------------------------------------------------------

    deadline = time.monotonic() + 10.0

    counter_after = counter_before

    while time.monotonic() < deadline:

        coil_response = client.read_coils(
            address=COIL_READ_NOW,
            count=1,
            device_id=UNIT_ID,
        )

        ensure_ok(
            coil_response,
            "Read READ_NOW coil",
        )

        read_now_pending = bool(
            coil_response.bits[0]
        )


        counter_response = client.read_input_registers(
            address=206,
            count=2,
            device_id=UNIT_ID,
        )

        ensure_ok(
            counter_response,
            "Read sample_counter",
        )

        counter_after = join_u32(
            counter_response.registers[0],
            counter_response.registers[1],
        )


        if (
            not read_now_pending
            and counter_after > counter_before
        ):
            break


        time.sleep(0.1)

    else:
        raise RuntimeError(
            "READ_NOW no terminó dentro de 10 segundos"
        )


    print(
        "      Completed."
    )

    print(
        "      Counter:",
        counter_before,
        "->",
        counter_after,
    )


    # ============================================================
    # Read complete environmental snapshot
    # ============================================================

    print()
    print("[5/5] Reading Input Registers 200-216...")

    response = client.read_input_registers(
        address=IREG_START,
        count=IREG_COUNT,
        device_id=UNIT_ID,
    )

    ensure_ok(
        response,
        "Read environmental snapshot",
    )

    regs = response.registers


    if len(regs) != IREG_COUNT:
        raise RuntimeError(
            "Expected {} registers, received {}".format(
                IREG_COUNT,
                len(regs),
            )
        )


    # ============================================================
    # Decode v1.0 core
    # ================================================================

    dht_temperature_c = (
        decode_int16(regs[0])
        / 100.0
    )

    dht_humidity_pct = (
        regs[1]
        / 100.0
    )

    dht_status = regs[2]

    device_state = regs[3]

    uptime_s = join_u32(
        regs[4],
        regs[5],
    )

    sample_counter = join_u32(
        regs[6],
        regs[7],
    )

    map_version = regs[8]


    # ============================================================
    # Decode v1.1 BME688 extension
    # ================================================================

    bme_temperature_c = (
        decode_int16(regs[9])
        / 100.0
    )

    bme_humidity_pct = (
        regs[10]
        / 100.0
    )

    bme_pressure_pa = join_u32(
        regs[11],
        regs[12],
    )

    bme_pressure_hpa = (
        bme_pressure_pa
        / 100.0
    )

    bme_gas_ohm = join_u32(
        regs[13],
        regs[14],
    )

    bme_flags = regs[15]

    bme_status = regs[16]


    gas_valid = bool(
        bme_flags & 0x0001
    )

    heater_stable = bool(
        bme_flags & 0x0002
    )


    # ============================================================
    # Results
    # ================================================================

    print()
    print("========================================")
    print(" P3-04 RESULT")
    print("========================================")

    print()
    print("SYSTEM")
    print(
        "  Register Map : 0x{:04X}".format(
            map_version
        )
    )
    print(
        "  Uptime       : {} s".format(
            uptime_s
        )
    )
    print(
        "  Sample count : {}".format(
            sample_counter
        )
    )
    print(
        "  Device state : {}".format(
            device_state
        )
    )

    print()
    print("DHT11")
    print(
        "  Temperature  : {:.2f} C".format(
            dht_temperature_c
        )
    )
    print(
        "  Humidity     : {:.2f} %".format(
            dht_humidity_pct
        )
    )
    print(
        "  Status       : {}".format(
            dht_status
        )
    )

    print()
    print("BME688")
    print(
        "  Temperature  : {:.2f} C".format(
            bme_temperature_c
        )
    )
    print(
        "  Humidity     : {:.2f} %".format(
            bme_humidity_pct
        )
    )
    print(
        "  Pressure     : {:.2f} hPa".format(
            bme_pressure_hpa
        )
    )
    print(
        "  Gas          : {} ohm".format(
            bme_gas_ohm
        )
    )
    print(
        "  Gas valid    : {}".format(
            gas_valid
        )
    )
    print(
        "  Heater stable: {}".format(
            heater_stable
        )
    )
    print(
        "  Status       : {}".format(
            bme_status
        )
    )

    print()
    print(
        "Raw 200-216:",
        regs,
    )

    print()
    print("P3-04 MODBUS TRANSPORT: PASS")


finally:

    client.close()
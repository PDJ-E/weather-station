from pymodbus.client import ModbusSerialClient


PORT = "/dev/ttyAMA0"
UNIT_ID = 1

BAUDRATE = 115200


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
print(" P3-04 BME688 Modbus smoke test")
print("========================================")
print()


if not client.connect():
    raise RuntimeError(
        "No se pudo abrir {}".format(PORT)
    )


try:
    version_response = client.read_input_registers(
        address=208,
        count=1,
        device_id=UNIT_ID,
    )

    if version_response.isError():
        raise RuntimeError(
            "Error leyendo Register Map version: {}".format(
                version_response
            )
        )

    version = version_response.registers[0]

    print(
        "Register Map Version: 0x{:04X}".format(
            version
        )
    )

    response = client.read_input_registers(
        address=209,
        count=7,
        device_id=UNIT_ID,
    )

    if response.isError():
        raise RuntimeError(
            "Error leyendo BME688: {}".format(
                response
            )
        )

    regs = response.registers

    temperature_raw = decode_int16(
        regs[0]
    )

    humidity_raw = regs[1]

    pressure_pa = join_u32(
        regs[2],
        regs[3],
    )

    gas_ohm = join_u32(
        regs[4],
        regs[5],
    )

    flags = regs[6]

    temperature_c = (
        temperature_raw / 100.0
    )

    humidity_pct = (
        humidity_raw / 100.0
    )

    pressure_hpa = (
        pressure_pa / 100.0
    )

    gas_valid = bool(
        flags & 0x0001
    )

    heater_stable = bool(
        flags & 0x0002
    )

    print()
    print(
        "Temperature : {:.2f} C".format(
            temperature_c
        )
    )

    print(
        "Humidity    : {:.2f} %".format(
            humidity_pct
        )
    )

    print(
        "Pressure    : {:.2f} hPa".format(
            pressure_hpa
        )
    )

    print(
        "Gas         : {} ohm".format(
            gas_ohm
        )
    )

    print(
        "Gas valid   : {}".format(
            gas_valid
        )
    )

    print(
        "Heater stable: {}".format(
            heater_stable
        )
    )

    print()
    print(
        "Raw registers 209-215:",
        regs,
    )

finally:
    client.close()
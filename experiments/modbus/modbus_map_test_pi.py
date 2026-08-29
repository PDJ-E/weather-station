"""
modbus_map_test_pi.py

P2-04 — Full Modbus Register Map smoke test.

Corre en Raspberry Pi 5.

Valida contra la Pico:

FC01 - Read Coils
FC03 - Read Holding Registers
FC04 - Read Input Registers
FC05 - Write Single Coil
FC06 - Write Single Register

PyModbus:
    3.15.0

Puerto:
    /dev/ttyAMA0

Unit ID:
    1

Serial:
    115200 8N1
"""

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException


# ================================================================
# Configuración
# ================================================================

SERIAL_PORT = "/dev/ttyAMA0"

BAUD_RATE = 115200

UNIT_ID = 1


# ================================================================
# Register Map
# ================================================================

COIL_RUN_ENABLE = 100
COIL_READ_NOW = 102

HREG_SAMPLE_INTERVAL = 101

IREG_START = 200
IREG_COUNT = 9

REGISTER_MAP_VERSION = 0x0100


# ================================================================
# Valores esperados del servidor estático
# ================================================================

EXPECTED_TELEMETRY = [
    2743,       # 200 temperature
    6825,       # 201 humidity
    0,          # 202 sensor_status
    0,          # 203 device_state
    0x0001,     # 204 uptime HIGH
    0x2345,     # 205 uptime LOW
    0x0000,     # 206 sample_counter HIGH
    42,         # 207 sample_counter LOW
    0x0100,     # 208 map version
]


# ================================================================
# Helpers
# ================================================================

def require_ok(result, operation):
    """
    Valida una respuesta PyModbus.

    Lanza RuntimeError si el dispositivo respondió
    con una excepción Modbus.
    """

    if result.isError():
        raise RuntimeError(
            "{} fallo: {}".format(
                operation,
                result,
            )
        )

    return result


def decode_int16(value):
    """
    Convierte uint16 Modbus a int16 two's complement.
    """

    if value & 0x8000:
        return value - 0x10000

    return value


def decode_uint32(high_word, low_word):
    """
    P2 word order:
    HIGH word first.
    """

    return (
        (high_word << 16)
        | low_word
    )


# ================================================================
# Main
# ================================================================

def main():

    print()
    print("========================================")
    print(" P2-04 Pi 5 full-map smoke test")
    print("========================================")
    print("Puerto  :", SERIAL_PORT)
    print("Unit ID :", UNIT_ID)
    print("Serial  : 115200 8N1")
    print()

    client = ModbusSerialClient(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        parity="N",
        stopbits=1,
        bytesize=8,
        timeout=1,
        retries=3,
    )

    print("[1] Abriendo puerto serial...")

    if not client.connect():
        print(
            "FAIL: no se pudo abrir",
            SERIAL_PORT,
        )
        return

    print("    OK.")
    print()

    try:

        # ========================================================
        # FC01 — Read Coils
        # ========================================================

        print(
            "[2] FC01 - Read Coil 100 "
            "(run_enable)..."
        )

        result = require_ok(
            client.read_coils(
                address=COIL_RUN_ENABLE,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC01 Coil 100",
        )

        run_enable = bool(
            result.bits[0]
        )

        print(
            "    run_enable:",
            run_enable,
        )

        if run_enable is not False:
            raise RuntimeError(
                "Coil 100 debería iniciar False"
            )

        print("    OK.")
        print()


        print(
            "[3] FC01 - Read Coil 102 "
            "(read_now_trigger)..."
        )

        result = require_ok(
            client.read_coils(
                address=COIL_READ_NOW,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC01 Coil 102",
        )

        read_now = bool(
            result.bits[0]
        )

        print(
            "    read_now_trigger:",
            read_now,
        )

        if read_now is not False:
            raise RuntimeError(
                "Coil 102 debería iniciar False"
            )

        print("    OK.")
        print()


        # ========================================================
        # FC05 — Write Single Coil
        # ========================================================

        print(
            "[4] FC05 - Write Coil 100 = True..."
        )

        require_ok(
            client.write_coil(
                address=COIL_RUN_ENABLE,
                value=True,
                device_id=UNIT_ID,
            ),
            "FC05 Coil 100=True",
        )

        result = require_ok(
            client.read_coils(
                address=COIL_RUN_ENABLE,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC01 verify Coil 100",
        )

        if bool(result.bits[0]) is not True:
            raise RuntimeError(
                "Coil 100 no cambió a True"
            )

        print("    True confirmado.")
        print()


        print(
            "[5] FC05 - Restaurando Coil 100 = False..."
        )

        require_ok(
            client.write_coil(
                address=COIL_RUN_ENABLE,
                value=False,
                device_id=UNIT_ID,
            ),
            "FC05 Coil 100=False",
        )

        result = require_ok(
            client.read_coils(
                address=COIL_RUN_ENABLE,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC01 verify Coil 100=False",
        )

        if bool(result.bits[0]) is not False:
            raise RuntimeError(
                "Coil 100 no volvió a False"
            )

        print("    False confirmado.")
        print()


        # ========================================================
        # FC03 — Read Holding Registers
        # ========================================================

        print(
            "[6] FC03 - Read Holding 101 "
            "(sample_interval_s)..."
        )

        result = require_ok(
            client.read_holding_registers(
                address=HREG_SAMPLE_INTERVAL,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC03 Holding 101",
        )

        interval = result.registers[0]

        print(
            "    sample_interval_s:",
            interval,
        )

        if interval != 5:
            raise RuntimeError(
                "Holding 101 debería iniciar en 5"
            )

        print("    OK.")
        print()


        # ========================================================
        # FC06 — Write Single Register
        # ========================================================

        print(
            "[7] FC06 - Write Holding 101 = 10..."
        )

        require_ok(
            client.write_register(
                address=HREG_SAMPLE_INTERVAL,
                value=10,
                device_id=UNIT_ID,
            ),
            "FC06 Holding 101=10",
        )

        result = require_ok(
            client.read_holding_registers(
                address=HREG_SAMPLE_INTERVAL,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC03 verify Holding 101",
        )

        interval = result.registers[0]

        print(
            "    Valor leído:",
            interval,
        )

        if interval != 10:
            raise RuntimeError(
                "Holding 101 no cambió a 10"
            )

        print("    OK.")
        print()


        print(
            "[8] FC06 - Restaurando Holding 101 = 5..."
        )

        require_ok(
            client.write_register(
                address=HREG_SAMPLE_INTERVAL,
                value=5,
                device_id=UNIT_ID,
            ),
            "FC06 Holding 101=5",
        )

        result = require_ok(
            client.read_holding_registers(
                address=HREG_SAMPLE_INTERVAL,
                count=1,
                device_id=UNIT_ID,
            ),
            "FC03 verify Holding 101=5",
        )

        if result.registers[0] != 5:
            raise RuntimeError(
                "Holding 101 no volvió a 5"
            )

        print("    Restaurado.")
        print()


        # ========================================================
        # FC04 — Read Input Registers
        # ========================================================

        print(
            "[9] FC04 - Read Input Registers 200-208..."
        )

        result = require_ok(
            client.read_input_registers(
                address=IREG_START,
                count=IREG_COUNT,
                device_id=UNIT_ID,
            ),
            "FC04 Input 200-208",
        )

        registers = result.registers

        print(
            "    RAW:",
            registers,
        )

        if registers != EXPECTED_TELEMETRY:
            raise RuntimeError(
                "Bloque 200-208 no coincide "
                "con los valores esperados"
            )

        print("    Bloque completo recibido.")
        print()


        # ========================================================
        # Decodificación
        # ========================================================

        temperature_raw = decode_int16(
            registers[0]
        )

        temperature_c = (
            temperature_raw / 100.0
        )

        humidity_pct = (
            registers[1] / 100.0
        )

        sensor_status = registers[2]
        device_state = registers[3]

        uptime_s = decode_uint32(
            registers[4],
            registers[5],
        )

        sample_counter = decode_uint32(
            registers[6],
            registers[7],
        )

        map_version = registers[8]


        print("[10] Decodificación del mapa")
        print()
        print(
            "    temperature_c      :",
            temperature_c,
        )
        print(
            "    humidity_pct       :",
            humidity_pct,
        )
        print(
            "    sensor_status      :",
            sensor_status,
        )
        print(
            "    device_state       :",
            device_state,
        )
        print(
            "    uptime_s           :",
            uptime_s,
        )
        print(
            "    sample_counter     :",
            sample_counter,
        )
        print(
            "    register_map_version:",
            "0x{:04X}".format(
                map_version
            ),
        )
        print()


        # ========================================================
        # Validaciones semánticas básicas
        # ========================================================

        if temperature_c != 27.43:
            raise RuntimeError(
                "Scaling de temperatura incorrecto"
            )

        if humidity_pct != 68.25:
            raise RuntimeError(
                "Scaling de humedad incorrecto"
            )

        if uptime_s != 0x00012345:
            raise RuntimeError(
                "Word order de uptime incorrecto"
            )

        if sample_counter != 42:
            raise RuntimeError(
                "Word order de sample_counter incorrecto"
            )

        if map_version != REGISTER_MAP_VERSION:
            raise RuntimeError(
                "register_map_version incorrecto"
            )


        # ========================================================
        # PASS
        # ========================================================

        print(
            "========================================"
        )
        print(
            " P2-04 FULL MODBUS MAP: PASS"
        )
        print(
            "========================================"
        )
        print()
        print(
            "FC01 Read Coils             : OK"
        )
        print(
            "FC03 Read Holding Registers : OK"
        )
        print(
            "FC04 Read Input Registers   : OK"
        )
        print(
            "FC05 Write Single Coil      : OK"
        )
        print(
            "FC06 Write Single Register  : OK"
        )
        print()
        print(
            "Coil 100                    : OK"
        )
        print(
            "Coil 102                    : OK"
        )
        print(
            "Holding 101                 : OK"
        )
        print(
            "Input 200-208               : OK"
        )
        print(
            "Scaling x100                : OK"
        )
        print(
            "uint32 high-word-first      : OK"
        )
        print(
            "Register Map v1.0           : OK"
        )
        print()
        print(
            "P2-04 puede marcarse DONE."
        )


    except (
        ModbusException,
        RuntimeError,
        OSError,
    ) as exc:

        print()
        print(
            "========================================"
        )
        print(
            " P2-04: FAIL"
        )
        print(
            "========================================"
        )
        print()
        print(
            repr(exc)
        )


    finally:

        client.close()

        print()
        print(
            "Puerto serial cerrado."
        )


if __name__ == "__main__":
    main()
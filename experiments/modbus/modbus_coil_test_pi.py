"""
modbus_coil_test_pi.py

Prueba TEMPORAL de Modbus RTU desde Raspberry Pi 5.

Objetivo:
- Pi actúa como cliente/master.
- Pico actúa como servidor/slave Unit ID 1.
- Usa /dev/ttyAMA0.
- 115200 baud, 8N1.

Prueba:
1. Leer Coil 100.
2. Escribir True.
3. Leer y verificar True.
4. Escribir False.
5. Leer y verificar False.
6. Dejar el Coil nuevamente en su estado inicial.

Requiere:
    pip install "pymodbus[serial]"

Diseñado para PyModbus 3.15.0.
"""

from pymodbus.client import ModbusSerialClient


# ---------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------

SERIAL_PORT = "/dev/ttyAMA0"

BAUD_RATE = 115200
UNIT_ID = 1

COIL_ADDRESS = 100


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def read_run_enable(client):
    """
    Lee Coil 100 usando FC01.
    Retorna True/False si la lectura fue válida.
    Retorna None en caso de error Modbus.
    """

    result = client.read_coils(
        address=COIL_ADDRESS,
        count=1,
        device_id=UNIT_ID,
    )

    if result.isError():
        print("ERROR leyendo Coil 100:")
        print(result)
        return None

    return bool(result.bits[0])


def write_run_enable(client, value):
    """
    Escribe Coil 100 usando FC05.
    Retorna True si Modbus aceptó la escritura.
    """

    result = client.write_coil(
        address=COIL_ADDRESS,
        value=value,
        device_id=UNIT_ID,
    )

    if result.isError():
        print(
            "ERROR escribiendo Coil 100 =",
            value,
        )
        print(result)
        return False

    return True


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print()
    print("========================================")
    print(" Pi 5 Modbus RTU smoke test")
    print("========================================")
    print("Puerto    :", SERIAL_PORT)
    print("Unit ID   :", UNIT_ID)
    print("Serial    : 115200 8N1")
    print("Coil      :", COIL_ADDRESS)
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

    # -------------------------------------------------------------
    # Abrir UART
    # -------------------------------------------------------------

    print("[1] Abriendo puerto serial...")

    if not client.connect():
        print("ERROR: no se pudo abrir", SERIAL_PORT)
        return

    print("    Puerto abierto.")
    print()

    try:
        # ---------------------------------------------------------
        # Leer estado inicial
        # ---------------------------------------------------------

        print("[2] Leyendo Coil 100...")

        initial_value = read_run_enable(client)

        if initial_value is None:
            print()
            print("FAIL: la Pico no respondió correctamente.")
            return

        print("    Valor inicial:", initial_value)
        print()

        # ---------------------------------------------------------
        # Escribir True
        # ---------------------------------------------------------

        print("[3] Escribiendo Coil 100 = True...")

        if not write_run_enable(client, True):
            print()
            print("FAIL.")
            return

        print("    Escritura aceptada.")
        print()

        # ---------------------------------------------------------
        # Verificar True
        # ---------------------------------------------------------

        print("[4] Releyendo Coil 100...")

        value = read_run_enable(client)

        if value is None:
            print()
            print("FAIL.")
            return

        print("    Valor leído:", value)

        if value is not True:
            print()
            print("FAIL: se esperaba True.")
            return

        print("    OK.")
        print()

        # ---------------------------------------------------------
        # Restaurar False
        # ---------------------------------------------------------

        print("[5] Escribiendo Coil 100 = False...")

        if not write_run_enable(client, False):
            print()
            print("FAIL.")
            return

        print("    Escritura aceptada.")
        print()

        # ---------------------------------------------------------
        # Verificar False
        # ---------------------------------------------------------

        print("[6] Releyendo Coil 100...")

        value = read_run_enable(client)

        if value is None:
            print()
            print("FAIL.")
            return

        print("    Valor leído:", value)

        if value is not False:
            print()
            print("FAIL: se esperaba False.")
            return

        print("    OK.")
        print()

        # ---------------------------------------------------------
        # Resultado
        # ---------------------------------------------------------

        print("========================================")
        print(" ROUND-TRIP MODBUS OK")
        print("========================================")
        print()
        print("FC01 Read Coils       : OK")
        print("FC05 Write Single Coil: OK")
        print("Unit ID 1             : OK")
        print("Coil 100              : OK")
        print("UART 115200 8N1       : OK")
        print()
        print("Coil 100 restaurado a False.")

    finally:
        client.close()
        print()
        print("Puerto serial cerrado.")


if __name__ == "__main__":
    main()
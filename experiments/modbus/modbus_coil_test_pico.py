"""
modbus_coil_test_pico.py

Prueba TEMPORAL de Modbus RTU en Raspberry Pi Pico 2.

Objetivo:
- Pico = servidor/slave Modbus RTU.
- Unit ID = 1.
- UART0 GP0 TX / GP1 RX.
- 115200 8N1.
- Coil 100 = run_enable.
- FC01 para lectura.
- FC05 para escritura.

Utiliza pico_modbus_patch.py para sustituir temporalmente
el frame reader de micropython-modbus 2.3.7.
"""

from machine import Pin  # type: ignore
from umodbus.serial import ModbusRTU  # type: ignore

from pico_modbus_patch import apply_umodbus_p2_patch # type: ignore


# ================================================================
# Configuración
# ================================================================

UNIT_ID = 1

UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1

BAUD_RATE = 115200


# ================================================================
# Crear servidor Modbus RTU
# ================================================================

server = ModbusRTU(
    addr=UNIT_ID,
    pins=(
        Pin(UART_TX_PIN),
        Pin(UART_RX_PIN),
    ),
    baudrate=BAUD_RATE,
    data_bits=8,
    stop_bits=1,
    parity=None,
    uart_id=UART_ID,
)


# ================================================================
# Aplicar compatibility patch
# ================================================================

apply_umodbus_p2_patch(server)


# ================================================================
# Register Map mínimo
# ================================================================

register_definitions = {
    "COILS": {
        "run_enable": {
            "register": 100,
            "len": 1,
            "val": 0,
        },
    },
}


server.setup_registers(
    registers=register_definitions
)


# ================================================================
# Información de arranque
# ================================================================

print()
print("========================================")
print(" Pico Modbus RTU smoke test")
print("========================================")
print("Unit ID   :", UNIT_ID)
print("UART      : UART0")
print("TX        : GP0")
print("RX        : GP1")
print("Serial    : 115200 8N1")
print("Coil 100  : run_enable")
print("Inicial   : False")
print("P2 patch  : ENABLED")
print()
print("Esperando solicitudes Modbus...")
print()


# ================================================================
# Main loop
# ================================================================

while True:

    try:

        processed = server.process()

        if processed:
            print(
                ">>> REQUEST MODBUS PROCESADO"
            )

    except KeyboardInterrupt:

        print()
        print("Servidor detenido.")

        break

    except Exception as exc:

        print(
            "ERROR:",
            repr(exc),
        )
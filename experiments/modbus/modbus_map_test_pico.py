"""
modbus_map_test_pico.py

P2-04 — Full Modbus Register Map smoke test.

Objetivo:
- Validar que la Raspberry Pi Pico 2 funciona como servidor Modbus RTU.
- Unit ID = 1.
- UART0 GP0/GP1.
- 115200 8N1.
- Validar FC01, FC03, FC04, FC05 y FC06.
- Exponer todas las direcciones del Register Map v1.

IMPORTANTE:
Los valores de telemetría son ESTATICOS para P2-04.

Este script valida el servidor Modbus y el mapa.
NO implementa todavía:
- scheduler real;
- DHT11;
- READ_NOW real;
- sample_counter dinámico;
- uptime dinámico;
- lógica de estados.

Eso se conecta después de demostrar que todo el mapa
se puede transportar correctamente por Modbus.
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
# Valores estáticos de prueba
# ================================================================

# Input Register 200
# 27.43 °C * 100
TEST_TEMPERATURE = 2743

# Input Register 201
# 68.25 %RH * 100
TEST_HUMIDITY = 6825

# Input Register 202
# 0 = OK
TEST_SENSOR_STATUS = 0

# Input Register 203
# 0 = STOPPED
TEST_DEVICE_STATE = 0

# 204-205 uptime_s
#
# 0x00012345 = 74565 segundos
TEST_UPTIME = 0x00012345

TEST_UPTIME_HIGH = (
    TEST_UPTIME >> 16
) & 0xFFFF

TEST_UPTIME_LOW = (
    TEST_UPTIME
    & 0xFFFF
)

# 206-207 sample_counter
TEST_SAMPLE_COUNTER = 42

TEST_COUNTER_HIGH = (
    TEST_SAMPLE_COUNTER >> 16
) & 0xFFFF

TEST_COUNTER_LOW = (
    TEST_SAMPLE_COUNTER
    & 0xFFFF
)

# 208
REGISTER_MAP_VERSION = 0x0100


# ================================================================
# Crear servidor Modbus
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
# Compatibility patch
# ================================================================

apply_umodbus_p2_patch(server)


# ================================================================
# Register Map P2 v1
# ================================================================

register_definitions = {

    # ------------------------------------------------------------
    # COILS
    # ------------------------------------------------------------

    "COILS": {

        # Coil 100
        "run_enable": {
            "register": 100,
            "len": 1,
            "val": 0,
        },

        # Coil 102
        #
        # En P2-04 todavía se comporta como un Coil almacenado.
        # La semántica async/self-clearing se conectará después.
        "read_now_trigger": {
            "register": 102,
            "len": 1,
            "val": 0,
        },
    },

    # ------------------------------------------------------------
    # HOLDING REGISTERS
    # ------------------------------------------------------------

    "HREGS": {

        # Holding Register 101
        "sample_interval_s": {
            "register": 101,
            "len": 1,
            "val": 5,
        },
    },

    # ------------------------------------------------------------
    # INPUT REGISTERS
    #
    # Bloque contiguo 200–208.
    #
    # Esto es intencional: queremos que FC04 pueda solicitar los
    # nueve registros en UNA sola transacción.
    # ------------------------------------------------------------

    "IREGS": {

        "telemetry_block": {
            "register": 200,
            "len": 9,

            "val": [
                TEST_TEMPERATURE,       # 200
                TEST_HUMIDITY,          # 201
                TEST_SENSOR_STATUS,     # 202
                TEST_DEVICE_STATE,      # 203
                TEST_UPTIME_HIGH,       # 204
                TEST_UPTIME_LOW,        # 205
                TEST_COUNTER_HIGH,      # 206
                TEST_COUNTER_LOW,       # 207
                REGISTER_MAP_VERSION,   # 208
            ],
        },
    },
}


# ================================================================
# Registrar mapa
# ================================================================

server.setup_registers(
    registers=register_definitions
)


# ================================================================
# Información de arranque
# ================================================================

print()
print("========================================")
print(" P2-04 Pico Modbus RTU full-map test")
print("========================================")
print("Unit ID    :", UNIT_ID)
print("UART       : UART0")
print("TX         : GP0")
print("RX         : GP1")
print("Serial     : 115200 8N1")
print("Patch      : ENABLED")
print()
print("COILS")
print("  100 run_enable")
print("  102 read_now_trigger")
print()
print("HREGS")
print("  101 sample_interval_s = 5")
print()
print("IREGS")
print("  200-208 telemetry block")
print()
print("Map version: 0x0100")
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
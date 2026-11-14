"""
bme688_modbus_pico.py

P3-04 — Weather Station environmental Modbus firmware.

Raspberry Pi Pico 2
Pimoroni MicroPython 1.29.0

Hardware:
    UART0:
        TX = GP0
        RX = GP1
        115200 8N1

    BME688:
        I2C1
        SDA = GP2
        SCL = GP3
        Address = 0x76

    DHT11:
        DATA = GP15

Register Map v1.1:

COILS
    100 run_enable
    102 read_now_trigger

HOLDING REGISTERS
    101 sample_interval_s

INPUT REGISTERS
    200 DHT11 temperature_c x100       int16
    201 DHT11 humidity_pct x100        uint16
    202 DHT11 sensor_status            enum
    203 device_state                   enum
    204 uptime_s HIGH
    205 uptime_s LOW
    206 sample_counter HIGH
    207 sample_counter LOW
    208 register_map_version           0x0101

    209 BME688 temperature_c x100      int16
    210 BME688 humidity_pct x100       uint16
    211 BME688 pressure_pa HIGH
    212 BME688 pressure_pa LOW
    213 BME688 gas_resistance HIGH
    214 BME688 gas_resistance LOW
    215 BME688 flags
            bit 0 = gas_valid
            bit 1 = heater_stable
    216 BME688 sensor_status           enum

P2 semantics are preserved:
    - START/STOP idempotent
    - interval 2..3600 s
    - READ_NOW asynchronous
    - READ_NOW works while STOPPED
    - sample_counter increments once per combined acquisition attempt
    - READ_NOW resets periodic schedule
    - last valid values survive sensor errors
"""

from machine import Pin  # type: ignore
import time

from umodbus.serial import ModbusRTU  # type: ignore

from pico_modbus_patch import (  # type: ignore
    apply_umodbus_p2_patch,
    apply_sample_interval_validation_patch,
)

from dht11_sensor import DHT11Sensor  # type: ignore
from bme688_sensor import BME688Sensor  # type: ignore

from environmental_reading import (  # type: ignore
    SENSOR_STATUS_OK,
    SENSOR_STATUS_ERR_SENSOR,
    SENSOR_STATUS_NO_DATA,
)


# ================================================================
# Hardware
# ================================================================

DHT_PIN = 15

BME688_SDA_PIN = 2
BME688_SCL_PIN = 3
BME688_ADDRESS = 0x76

UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1

BAUD_RATE = 115200
UNIT_ID = 1


# ================================================================
# Register Map v1.1
# ================================================================

COIL_RUN_ENABLE = 100
HREG_SAMPLE_INTERVAL = 101
COIL_READ_NOW = 102

IREG_START = 200
IREG_COUNT = 17

REGISTER_MAP_VERSION = 0x0101


# ------------------------------------------------
# Existing v1.0 registers
# ------------------------------------------------

IREG_DHT_TEMP = 200
IREG_DHT_HUMIDITY = 201
IREG_DHT_STATUS = 202
IREG_DEVICE_STATE = 203

IREG_UPTIME_HIGH = 204
IREG_UPTIME_LOW = 205

IREG_COUNTER_HIGH = 206
IREG_COUNTER_LOW = 207

IREG_MAP_VERSION = 208


# ------------------------------------------------
# v1.1 BME688 extension
# ------------------------------------------------

IREG_BME_TEMP = 209
IREG_BME_HUMIDITY = 210

IREG_BME_PRESSURE_HIGH = 211
IREG_BME_PRESSURE_LOW = 212

IREG_BME_GAS_HIGH = 213
IREG_BME_GAS_LOW = 214

IREG_BME_FLAGS = 215
IREG_BME_STATUS = 216


# ================================================================
# BME688 flags
# ================================================================

BME_FLAG_GAS_VALID = 0x0001
BME_FLAG_HEATER_STABLE = 0x0002


# ================================================================
# Device states
# ================================================================

STATE_STOPPED = 0
STATE_RUNNING = 1
STATE_FAULT = 2


# ================================================================
# Estado inicial
# ================================================================

run_enabled = False

sample_interval_s = 5
next_sample_ms = None

read_now_pending = False


# ------------------------------------------------
# DHT11 snapshot
# ------------------------------------------------

dht_temperature_scaled = 0
dht_humidity_scaled = 0
dht_sensor_status = SENSOR_STATUS_NO_DATA


# ------------------------------------------------
# BME688 snapshot
# ------------------------------------------------

bme_temperature_scaled = 0
bme_humidity_scaled = 0

bme_pressure_pa = 0
bme_gas_ohm = 0

bme_flags = 0
bme_sensor_status = SENSOR_STATUS_NO_DATA


# ------------------------------------------------
# Device diagnostics
# ------------------------------------------------

device_state = STATE_STOPPED
sample_counter = 0


# ================================================================
# Uptime monotónico
# ================================================================

last_tick_ms = time.ticks_ms()  # type: ignore
uptime_accumulated_ms = 0


def update_uptime_clock():
    global last_tick_ms
    global uptime_accumulated_ms

    now = time.ticks_ms()  # type: ignore

    delta = time.ticks_diff(  # type: ignore
        now,
        last_tick_ms,
    )

    if delta > 0:
        uptime_accumulated_ms += delta

    last_tick_ms = now


def get_uptime_s():
    update_uptime_clock()

    return (
        uptime_accumulated_ms // 1000
    ) & 0xFFFFFFFF


# ================================================================
# Encoding helpers
# ================================================================

def encode_int16(value):
    """
    Signed int16 -> Modbus uint16 word.
    """

    value = int(value)

    if value < -32768 or value > 32767:
        raise ValueError(
            "int16 fuera de rango: {}".format(value)
        )

    return value & 0xFFFF


def split_uint32(value):
    """
    uint32 -> HIGH word, LOW word.
    """

    value = int(value) & 0xFFFFFFFF

    high = (
        value >> 16
    ) & 0xFFFF

    low = (
        value
    ) & 0xFFFF

    return high, low


# ================================================================
# Sensores
# ================================================================

dht11 = DHT11Sensor(
    pin=DHT_PIN,
)


bme688 = BME688Sensor(
    sda_pin=BME688_SDA_PIN,
    scl_pin=BME688_SCL_PIN,
    address=BME688_ADDRESS,
)


# ================================================================
# Modbus server
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
# micropython-modbus compatibility patches
# ================================================================

apply_umodbus_p2_patch(
    server
)

apply_sample_interval_validation_patch(
    server
)


# ================================================================
# Callback helper
# ================================================================

def callback_scalar(val):
    """
    micropython-modbus entrega writes individuales
    normalmente como listas.

    Ejemplos:
        [True]
        [5]

    Esto normaliza a un valor escalar.
    """

    if isinstance(
        val,
        (list, tuple),
    ):
        return val[0]

    return val


# ================================================================
# Publicación del snapshot Modbus completo
# ================================================================

def publish_snapshot():
    """
    Publica Input Registers 200-216.

    200-208 preservan Register Map v1.0.
    209-216 corresponden a la extensión BME688 v1.1.
    """

    uptime_s = get_uptime_s()

    uptime_high, uptime_low = split_uint32(
        uptime_s
    )

    counter_high, counter_low = split_uint32(
        sample_counter
    )

    pressure_high, pressure_low = split_uint32(
        bme_pressure_pa
    )

    gas_high, gas_low = split_uint32(
        bme_gas_ohm
    )

    values = [

        # --------------------------------------------------------
        # Register Map v1.0
        # --------------------------------------------------------

        encode_int16(
            dht_temperature_scaled
        ),                                  # 200

        dht_humidity_scaled & 0xFFFF,       # 201

        dht_sensor_status,                  # 202

        device_state,                       # 203

        uptime_high,                        # 204
        uptime_low,                         # 205

        counter_high,                       # 206
        counter_low,                        # 207

        REGISTER_MAP_VERSION,               # 208


        # --------------------------------------------------------
        # Register Map v1.1 — BME688
        # --------------------------------------------------------

        encode_int16(
            bme_temperature_scaled
        ),                                  # 209

        bme_humidity_scaled & 0xFFFF,       # 210

        pressure_high,                      # 211
        pressure_low,                       # 212

        gas_high,                           # 213
        gas_low,                            # 214

        bme_flags & 0xFFFF,                 # 215

        bme_sensor_status,                  # 216
    ]

    server.set_ireg(
        address=IREG_START,
        value=values,
    )


# ================================================================
# DHT11 acquisition
# ================================================================

def acquire_dht11():
    """
    Ejecuta una lectura DHT11.

    Si falla:
        - conserva última T/RH válida
        - cambia solamente su status
    """

    global dht_temperature_scaled
    global dht_humidity_scaled
    global dht_sensor_status

    reading = dht11.read()

    if (
        reading.sensor_status
        != SENSOR_STATUS_OK
    ):
        dht_sensor_status = (
            SENSOR_STATUS_ERR_SENSOR
        )

        print(
            "[DHT11] acquisition error"
        )

        return False

    try:

        new_temperature_scaled = int(
            round(
                reading.temperature_c # type: ignore
                * 100.0
            )
        )

        new_humidity_scaled = int(
            round(
                reading.humidity_pct # type: ignore
                * 100.0
            )
        )

        # Commit solamente cuando toda
        # la lectura es válida.

        dht_temperature_scaled = (
            new_temperature_scaled
        )

        dht_humidity_scaled = (
            new_humidity_scaled
        )

        dht_sensor_status = (
            SENSOR_STATUS_OK
        )

        return True

    except Exception as exc:

        dht_sensor_status = (
            SENSOR_STATUS_ERR_SENSOR
        )

        print(
            "[DHT11] encoding error:",
            repr(exc),
        )

        return False


# ================================================================
# BME688 acquisition
# ================================================================

def acquire_bme688():
    """
    Ejecuta una lectura BME688.

    Si falla:
        - conserva última T/RH/P/Gas válida
        - status = ERR_SENSOR
        - flags = 0

    pressure se transporta en Pa.
    """

    global bme_temperature_scaled
    global bme_humidity_scaled

    global bme_pressure_pa
    global bme_gas_ohm

    global bme_flags
    global bme_sensor_status


    reading = bme688.read()


    if (
        reading.sensor_status
        != SENSOR_STATUS_OK
    ):

        bme_sensor_status = (
            SENSOR_STATUS_ERR_SENSOR
        )

        bme_flags = 0

        print(
            "[BME688] acquisition error"
        )

        return False


    try:

        new_temperature_scaled = int(
            round(
                reading.temperature_c # type: ignore
                * 100.0
            )
        )

        new_humidity_scaled = int(
            round(
                reading.humidity_pct # type: ignore
                * 100.0
            )
        )

        # EnvironmentalReading usa hPa.
        # El Register Map transporta Pa.

        new_pressure_pa = int(
            round(
                reading.pressure_hpa # type: ignore
                * 100.0
            )
        )

        new_gas_ohm = int(
            round(
                reading.gas_resistance_ohm # type: ignore
            )
        )


        new_flags = 0


        if reading.gas_valid:
            new_flags |= (
                BME_FLAG_GAS_VALID
            )


        if reading.heater_stable:
            new_flags |= (
                BME_FLAG_HEATER_STABLE
            )


        # --------------------------------------------------------
        # Commit completo
        # --------------------------------------------------------

        bme_temperature_scaled = (
            new_temperature_scaled
        )

        bme_humidity_scaled = (
            new_humidity_scaled
        )

        bme_pressure_pa = (
            new_pressure_pa
        )

        bme_gas_ohm = (
            new_gas_ohm
        )

        bme_flags = (
            new_flags
        )

        bme_sensor_status = (
            SENSOR_STATUS_OK
        )

        return True


    except Exception as exc:

        bme_sensor_status = (
            SENSOR_STATUS_ERR_SENSOR
        )

        bme_flags = 0

        print(
            "[BME688] encoding error:",
            repr(exc),
        )

        return False


# ================================================================
# Adquisición ambiental combinada
# ================================================================

def perform_acquisition(source):
    """
    Ejecuta exactamente un ciclo de adquisición ambiental:

        DHT11
        +
        BME688

    sample_counter incrementa UNA vez por ciclo combinado,
    no una vez por sensor.

    Si cualquiera de los sensores falla:
        device_state = FAULT

    Los valores válidos anteriores se conservan de manera
    independiente para cada sensor.
    """

    global sample_counter
    global device_state


    dht_ok = acquire_dht11()

    bme_ok = acquire_bme688()


    # ------------------------------------------------------------
    # Estado global del dispositivo
    # ------------------------------------------------------------

    if dht_ok and bme_ok:

        if run_enabled:
            device_state = STATE_RUNNING
        else:
            device_state = STATE_STOPPED

    else:

        device_state = STATE_FAULT


    # ------------------------------------------------------------
    # Un intento ambiental completo
    # ------------------------------------------------------------

    sample_counter = (
        sample_counter + 1
    ) & 0xFFFFFFFF


    # ------------------------------------------------------------
    # Commit Modbus
    # ------------------------------------------------------------

    publish_snapshot()


    # ------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------

    print()
    print(
        "[SAMPLE]",
        source,
        "counter=",
        sample_counter,
    )


    print(
        "  DHT11:",
        "status=",
        dht_sensor_status,
        "T=",
        dht_temperature_scaled / 100.0,
        "RH=",
        dht_humidity_scaled / 100.0,
    )


    print(
        "  BME688:",
        "status=",
        bme_sensor_status,
        "T=",
        bme_temperature_scaled / 100.0,
        "RH=",
        bme_humidity_scaled / 100.0,
        "P=",
        bme_pressure_pa,
        "Pa",
        "Gas=",
        bme_gas_ohm,
        "ohm",
        "flags=0x{:04X}".format(
            bme_flags
        ),
    )


    return (
        dht_ok
        and bme_ok
    )


# ================================================================
# Modbus callbacks
# ================================================================

def on_run_enable_set(
    reg_type,
    address,
    val,
):
    """
    Coil 100.

    False -> STOP
    True  -> START

    Es idempotente.
    """

    global run_enabled
    global next_sample_ms
    global device_state


    requested = bool(
        callback_scalar(val)
    )


    # ------------------------------------------------------------
    # Same value = no-op
    # ------------------------------------------------------------

    if requested == run_enabled:
        return


    run_enabled = requested


    # ------------------------------------------------------------
    # START
    # ------------------------------------------------------------

    if run_enabled:

        device_state = STATE_RUNNING

        next_sample_ms = time.ticks_add(  # type: ignore
            time.ticks_ms(),              # type: ignore
            sample_interval_s * 1000,
        )

        print(
            "[CONTROL] START"
        )


    # ------------------------------------------------------------
    # STOP
    # ------------------------------------------------------------

    else:

        next_sample_ms = None

        device_state = STATE_STOPPED

        print(
            "[CONTROL] STOP"
        )


    publish_snapshot()


def on_sample_interval_set(
    reg_type,
    address,
    val,
):
    """
    Holding Register 101.

    Valid:
        2..3600 seconds.

    Exception 03 es aplicada por
    apply_sample_interval_validation_patch().
    """

    global sample_interval_s
    global next_sample_ms


    requested = int(
        callback_scalar(val)
    )


    # ------------------------------------------------------------
    # Protección adicional local
    # ------------------------------------------------------------

    if not (
        2
        <= requested
        <= 3600
    ):

        server.set_hreg(
            HREG_SAMPLE_INTERVAL,
            sample_interval_s,
        )

        print(
            "[CONTROL] invalid interval:",
            requested,
        )

        return


    # ------------------------------------------------------------
    # Idempotencia
    # ------------------------------------------------------------

    if requested == sample_interval_s:
        return


    sample_interval_s = requested


    print(
        "[CONTROL] interval =",
        sample_interval_s,
        "s",
    )


    # ------------------------------------------------------------
    # Si está RUNNING se reinicia scheduler desde ahora
    # ------------------------------------------------------------

    if run_enabled:

        next_sample_ms = time.ticks_add(  # type: ignore
            time.ticks_ms(),              # type: ignore
            sample_interval_s * 1000,
        )


def on_read_now_set(
    reg_type,
    address,
    val,
):
    """
    Coil 102.

    Write 1:
        agenda un ciclo ambiental.

    Write 1 mientras ya está pending:
        no-op.

    Write 0:
        no cancela.

    Solo firmware hace 1 -> 0.
    """

    global read_now_pending


    requested = bool(
        callback_scalar(val)
    )


    # ------------------------------------------------------------
    # Cliente escribe 0
    # ------------------------------------------------------------

    if not requested:

        if read_now_pending:

            server.set_coil(
                COIL_READ_NOW,
                True,
            )

        return


    # ------------------------------------------------------------
    # Ya había uno pendiente
    # ------------------------------------------------------------

    if read_now_pending:
        return


    # ------------------------------------------------------------
    # Nuevo trigger
    # ------------------------------------------------------------

    read_now_pending = True


    print(
        "[CONTROL] READ_NOW pending"
    )


# ================================================================
# FC04 callback
# ================================================================

def on_telemetry_get(
    reg_type,
    address,
    val,
):
    """
    Refresca uptime antes de responder una lectura FC04.
    """

    publish_snapshot()


# ================================================================
# Register definitions
# ================================================================

register_definitions = {

    "COILS": {

        "run_enable": {

            "register": COIL_RUN_ENABLE,
            "len": 1,
            "val": 0,

            "on_set_cb": (
                on_run_enable_set
            ),
        },


        "read_now_trigger": {

            "register": COIL_READ_NOW,
            "len": 1,
            "val": 0,

            "on_set_cb": (
                on_read_now_set
            ),
        },
    },


    "HREGS": {

        "sample_interval_s": {

            "register": HREG_SAMPLE_INTERVAL,
            "len": 1,

            "val": sample_interval_s,

            "on_set_cb": (
                on_sample_interval_set
            ),
        },
    },


    "IREGS": {

        "environmental_telemetry": {

            "register": IREG_START,

            "len": IREG_COUNT,

            "val": [

                # v1.0
                0,                          # 200
                0,                          # 201
                SENSOR_STATUS_NO_DATA,      # 202
                STATE_STOPPED,              # 203

                0,                          # 204
                0,                          # 205

                0,                          # 206
                0,                          # 207

                REGISTER_MAP_VERSION,       # 208


                # v1.1 BME688
                0,                          # 209
                0,                          # 210

                0,                          # 211
                0,                          # 212

                0,                          # 213
                0,                          # 214

                0,                          # 215
                SENSOR_STATUS_NO_DATA,      # 216
            ],

            "on_get_cb": (
                on_telemetry_get
            ),
        },
    },
}


server.setup_registers(
    registers=register_definitions
)


publish_snapshot()


# ================================================================
# READ_NOW scheduler
# ================================================================

def process_read_now():
    """
    Ejecuta adquisición manual fuera del handler Modbus.
    """

    global read_now_pending
    global next_sample_ms


    if not read_now_pending:
        return


    perform_acquisition(
        "READ_NOW"
    )


    # ------------------------------------------------------------
    # Si está RUNNING, periodic vuelve a contar
    # desde el final de READ_NOW.
    # ------------------------------------------------------------

    if run_enabled:

        next_sample_ms = time.ticks_add(  # type: ignore
            time.ticks_ms(),              # type: ignore
            sample_interval_s * 1000,
        )


    # ------------------------------------------------------------
    # El snapshot ya está publicado.
    # Ahora se limpia el trigger.
    # ------------------------------------------------------------

    read_now_pending = False


    server.set_coil(
        COIL_READ_NOW,
        False,
    )


    print(
        "[CONTROL] READ_NOW complete"
    )


# ================================================================
# Periodic scheduler
# ================================================================

def update_periodic_sampling():
    """
    Ejecuta sampling periódico cuando corresponde.
    """

    global next_sample_ms


    if not run_enabled:
        return


    if next_sample_ms is None:
        return


    now = time.ticks_ms()  # type: ignore


    if time.ticks_diff(  # type: ignore
        now,
        next_sample_ms,
    ) < 0:

        return


    perform_acquisition(
        "PERIODIC"
    )


    # ------------------------------------------------------------
    # Programar desde el final de la adquisición
    # ------------------------------------------------------------

    next_sample_ms = time.ticks_add(  # type: ignore
        time.ticks_ms(),              # type: ignore
        sample_interval_s * 1000,
    )


# ================================================================
# Boot diagnostics
# ================================================================

print()
print("========================================")
print(" P3-04 Environmental Modbus Firmware")
print("========================================")
print("Unit ID        :", UNIT_ID)
print("UART           : UART0 GP0/GP1")
print("Serial         : 115200 8N1")
print("DHT11          : GP15")
print("BME688         : I2C GP2/GP3 @ 0x76")
print("run_enable     : False")
print("interval       :", sample_interval_s)
print("device_state   : STOPPED")
print("DHT status     : NO_DATA")
print("BME status     : NO_DATA")
print("Map version    : 0x0101")
print("P2 patches     : ENABLED")
print()
print("Input Registers:")
print("  200-208      : P2 core")
print("  209-216      : BME688 extension")
print()
print("Esperando control Modbus...")
print()


# ================================================================
# Main cooperative loop
# ================================================================

while True:

    try:

        update_uptime_clock()


        # --------------------------------------------------------
        # Procesa como máximo la actividad Modbus disponible.
        # --------------------------------------------------------

        server.process()


        # --------------------------------------------------------
        # READ_NOW tiene prioridad sobre periodic.
        # --------------------------------------------------------

        process_read_now()


        # --------------------------------------------------------
        # Muestreo periódico.
        # --------------------------------------------------------

        update_periodic_sampling()


        time.sleep_ms(2)  # type: ignore


    except KeyboardInterrupt:

        print()
        print(
            "Firmware detenido."
        )

        break


    except Exception as exc:

        print(
            "[LOOP] unexpected error:",
            repr(exc),
        )

        # No matar el nodo por un error aislado.
        time.sleep_ms(100)  # type: ignore

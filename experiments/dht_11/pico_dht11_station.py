"""pico_dht11_station.py — Fase 1 (P1): nodo DHT11 con maquina de estados
y comandos por UART.

Implementa el contrato ya documentado en la hoja "Fase 1 - DHT11" de
weather_station_referencia_tecnica.xlsx:

  Comandos:    PING, STATUS, START, STOP, SET_INTERVAL <s>, READ_NOW
  Estados:     STOPPED, RUNNING, FAULT
  Telemetria:  una linea JSON por muestra -> sequence, sensor,
               temperature_c, humidity_pct, status, uptime_ms

Sigue el mismo patron de scheduler cooperativo (time.ticks_ms, sin sleeps
largos) que experiments/uart_pi_pico/pico_commands_with_state.py.

Cubre en la Pico: P1-03, P1-04, P1-05, P1-06, P1-07, P1-08, P1-09, P1-10,
P1-11, P1-12 del Backlog. La ingesta en la Pi (P1-13 en adelante) queda
para el siguiente paso.
"""

from machine import UART, Pin  # type:ignore
import dht  # type:ignore
import time
import json


# --- UART: mismo puerto y parametros que los experimentos anteriores ---
uart = UART(
    0,
    baudrate=115200,
    bits=8,
    parity=None,
    stop=1,
    tx=Pin(0),
    rx=Pin(1),
    timeout=1000,
)

# --- Sensor ---
# GP15 (pin fisico 20) para no compartir bus ni cercania con UART0 (GP0/GP1).
# Si el modulo DHT11 no trae pull-up integrado en la placa, agregar una
# resistencia de 4.7k-10k entre DATA y 3V3.
SENSOR_PIN = 15
sensor = dht.DHT11(Pin(SENSOR_PIN))

# --- Limites de configuracion ---
# El DHT11 no es fiable por debajo de ~1s entre lecturas (datasheet);
# se deja margen de seguridad en el minimo.
MIN_INTERVAL_S = 2
MAX_INTERVAL_S = 3600
DEFAULT_INTERVAL_S = 5

# --- Estado global ---
state = "STOPPED"          # STOPPED | RUNNING | FAULT
interval_s = DEFAULT_INTERVAL_S
sequence = 0
last_error = None
last_reading = None        # (temperature_c, humidity_pct) o None

boot_ms = time.ticks_ms()  # type:ignore
next_sample_ms = time.ticks_add(boot_ms, interval_s * 1000)  # type:ignore


def uptime_ms() -> int:
    return time.ticks_diff(time.ticks_ms(), boot_ms)  # type:ignore 


def read_sensor():
    """Lee el DHT11. Devuelve (temp, hum, status).

    La llamada bloquea solo lo que tarda la lectura del sensor (unos
    pocos ms), nunca el intervalo completo de muestreo.
    """
    try:
        sensor.measure()
        return sensor.temperature(), sensor.humidity(), "OK"
    except OSError as exc:
        return None, None, "ERR_SENSOR:{}".format(exc)


def emit_telemetry(temp, hum, status) -> dict:
    global sequence
    sequence += 1
    payload = {
        "sequence": sequence,
        "sensor": "DHT11",
        "temperature_c": temp,
        "humidity_pct": hum,
        "status": status,
        "uptime_ms": uptime_ms(),
    }
    line = json.dumps(payload)
    uart.write(line + "\n")
    print(line)
    return payload


def do_sample() -> str:
    """Lee y emite una muestra. No decide el estado por si sola."""
    global last_error, last_reading

    temp, hum, status = read_sensor()

    if status == "OK":
        last_reading = (temp, hum)
        last_error = None
    else:
        last_error = status

    emit_telemetry(temp, hum, status)
    return status


def update_sampling() -> None:
    """Scheduler cooperativo: dispara una lectura cuando vence el
    intervalo, solo si el nodo no esta STOPPED."""
    global next_sample_ms, state

    if state == "STOPPED":
        return

    now = time.ticks_ms()  # type:ignore

    if time.ticks_diff(now, next_sample_ms) < 0:  # type:ignore
        return

    status = do_sample()
    state = "RUNNING" if status == "OK" else "FAULT"

    next_sample_ms = time.ticks_add(time.ticks_ms(), interval_s * 1000) # type:ignore


def handle_command(raw: str) -> str:
    global state, interval_s, next_sample_ms

    parts = raw.strip().split()
    if not parts:
        return "ERR:EMPTY"

    cmd = parts[0].upper()

    if cmd == "PING":
        return "PONG"

    if cmd == "STATUS":
        temp = last_reading[0] if last_reading else None
        hum = last_reading[1] if last_reading else None
        return "STATUS:{},INTERVAL:{},UPTIME_MS:{},LAST_TEMP:{},LAST_HUM:{},LAST_ERROR:{}".format(
            state, interval_s, uptime_ms(), temp, hum, last_error
        )

    if cmd == "START":
        state = "RUNNING"
        next_sample_ms = time.ticks_add(time.ticks_ms(), interval_s * 1000)  # type:ignore
        return "ACK:START"

    if cmd == "STOP":
        state = "STOPPED"
        return "ACK:STOP"

    if cmd == "SET_INTERVAL":
        if len(parts) < 2:
            return "ERR:RANGE:MISSING_VALUE"
        try:
            value = int(parts[1])
        except ValueError:
            return "ERR:RANGE:NOT_INTEGER"
        if not (MIN_INTERVAL_S <= value <= MAX_INTERVAL_S):
            return "ERR:RANGE:{}-{}".format(MIN_INTERVAL_S, MAX_INTERVAL_S)
        interval_s = value
        next_sample_ms = time.ticks_add(time.ticks_ms(), interval_s * 1000)  # type:ignore
        return "ACK:INTERVAL:{}".format(interval_s)

    if cmd == "READ_NOW":
        status = do_sample()

        next_sample_ms = time.ticks_add(time.ticks_ms(),interval_s * 1000) # type:ignore

        if status == "OK":
            return "ACK:READ_NOW"
        return "ERR:SENSOR:{}".format(status)

    return "ERR:UNKNOWN_COMMAND:{}".format(cmd)


def process_uart() -> None:
    if not uart.any():
        return

    raw = uart.readline()
    if raw is None:
        return

    try:
        command = raw.decode("utf-8")
    except UnicodeError:
        uart.write("ERR:INVALID_ENCODING\n")
        return

    response = handle_command(command)
    uart.write(response + "\n")
    print("CMD:", command.strip(), "->", response)


print("Nodo DHT11 listo. Estado inicial:", state)

while True:
    process_uart()
    update_sampling()
    time.sleep_ms(10)  # type:ignore
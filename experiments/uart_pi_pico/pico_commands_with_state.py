from machine import UART, Pin #type:ignore
import time


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

led = Pin("LED", Pin.OUT)


# Estado del parpadeo
blink_active = False
blink_interval_ms = 300
blink_transitions_remaining = 0
blink_next_change_ms = 0


def start_blink(times: int = 3, interval_ms: int = 300) -> None:
    global blink_active
    global blink_interval_ms
    global blink_transitions_remaining
    global blink_next_change_ms

    blink_active = True
    blink_interval_ms = interval_ms
    blink_transitions_remaining = times * 2

    led.on()
    blink_next_change_ms = time.ticks_add( #type:ignore
        time.ticks_ms(), #type:ignore
        blink_interval_ms,
    )


def update_blink() -> None:
    global blink_active
    global blink_transitions_remaining
    global blink_next_change_ms

    if not blink_active:
        return

    now = time.ticks_ms() #type:ignore

    if time.ticks_diff(now, blink_next_change_ms) < 0: #type:ignore
        return

    led.toggle()
    blink_transitions_remaining -= 1

    if blink_transitions_remaining <= 0:
        blink_active = False
        led.off()
        return

    blink_next_change_ms = time.ticks_add( #type:ignore
        blink_next_change_ms,
        blink_interval_ms,
    )


def handle_command(command: str) -> str:
    global blink_active

    command = command.strip().upper()

    if command == "PING":
        return "PONG"

    if command == "GET_STATUS":
        led_state = "ON" if led.value() else "OFF"
        blink_state = "ACTIVE" if blink_active else "IDLE"

        return (
            f"STATUS:OK,"
            f"LED:{led_state},"
            f"BLINK:{blink_state}"
        )

    if command == "LED_ON":
        blink_active = False
        led.on()
        return "ACK:LED_ON"

    if command == "LED_OFF":
        blink_active = False
        led.off()
        return "ACK:LED_OFF"

    if command == "LED_BLINK":
        start_blink(times=3, interval_ms=300)
        return "ACK:LED_BLINK_STARTED"

    return f"ERROR:UNKNOWN_COMMAND:{command}"


def process_uart() -> None:
    if not uart.any():
        return

    raw_message = uart.readline()

    if raw_message is None:
        return

    try:
        command = raw_message.decode("utf-8").strip()
    except UnicodeError:
        uart.write("ERROR:INVALID_ENCODING\n")
        return

    print("Comando recibido:", command)

    response = handle_command(command)

    print("Respuesta:", response)
    uart.write(f"{response}\n")


print("Pico iniciada. Esperando comandos por UART...")

while True:
    process_uart()
    update_blink()

    time.sleep_ms(5) #type:ignore
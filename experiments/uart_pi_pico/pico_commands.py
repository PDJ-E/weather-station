from machine import UART, Pin # type:ignore
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


def blink_led(times: int = 3, interval_ms: int = 300) -> None:
    for _ in range(times):
        led.on()
        time.sleep_ms(interval_ms) # type:ignore

        led.off()
        time.sleep_ms(interval_ms) # type:ignore


def handle_command(command: str) -> str:
    command = command.strip().upper()

    if command == "PING":
        return "PONG"

    if command == "GET_STATUS":
        led_state = "ON" if led.value() else "OFF"
        return f"STATUS:OK,LED:{led_state}"

    if command == "LED_ON":
        led.on()
        return "ACK:LED_ON"

    if command == "LED_OFF":
        led.off()
        return "ACK:LED_OFF"

    if command == "LED_BLINK":
        blink_led()
        return "ACK:LED_BLINK"

    return f"ERROR:UNKNOWN_COMMAND:{command}"


print("Pico iniciada. Esperando comandos por UART...")

while True:
    if uart.any():
        raw_message = uart.readline()

        if raw_message is None:
            continue

        try:
            command = raw_message.decode("utf-8").strip()
        except UnicodeError:
            uart.write("ERROR:INVALID_ENCODING\n")
            continue

        print("Comando recibido:", command)

        response = handle_command(command)

        print("Respuesta:", response)
        uart.write(f"{response}\n")

    time.sleep_ms(10) # type:ignore
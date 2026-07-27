from machine import UART, Pin #type:ignore
import time

# UART0:
# TX = GP0
# RX = GP1
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

print("Pico iniciada. Esperando mensajes por UART...")

while True:
    if uart.any():
        raw_message = uart.readline()

        if raw_message is not None:
            try:
                message = raw_message.decode("utf-8").strip()
            except UnicodeError:
                uart.write("ERROR: mensaje no valido\n")
                continue

            print("Recibido desde Pi:", message)

            response = f"PICO_OK:{message}\n"
            uart.write(response)

    time.sleep_ms(10) # type:ignore

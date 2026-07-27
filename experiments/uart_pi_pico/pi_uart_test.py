
"""pi_uart_test.py — simple UART interactive test for Raspberry Pi Pico

Prompts the user to send text messages to a connected Pico over UART
and prints any responses. Intended for quick experiments in this folder.
"""

import time
import serial

# Raspberry Pi 5:
# /dev/ttyAMA0 = UART0 del RP1 en GPIO14/GPIO15
# No usar /dev/serial0: en este equipo apunta a /dev/ttyAMA10
SERIAL_PORT = "/dev/ttyAMA0"
BAUD_RATE = 115200
READ_TIMEOUT_SECONDS = 2


def main() -> None:
    try:
        with serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT_SECONDS,
        ) as uart:
            print(f"Puerto abierto: {SERIAL_PORT}")
            time.sleep(2)

            while True:
                message = input("Mensaje para la Pico: ").strip()

                if not message:
                    continue

                if message.lower() in {"salir", "exit", "quit"}:
                    break

                uart.write(f"{message}\n".encode("utf-8"))
                uart.flush()

                response = uart.readline()

                if response:
                    decoded = response.decode("utf-8").strip()
                    print(f"Respuesta de la Pico: {decoded}")
                else:
                    print("No se recibió respuesta dentro del timeout.")

    except serial.SerialException as exc:
        print(f"Error abriendo o usando UART: {exc}")
    except KeyboardInterrupt:
        print("\nPrograma detenido.")


if __name__ == "__main__":
    main()
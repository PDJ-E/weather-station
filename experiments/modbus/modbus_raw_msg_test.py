import serial
import time


PORT = "/dev/ttyAMA0"
BAUD = 115200

# Unit 1
# FC01 Read Coils
# Address 100
# Quantity 1
# CRC = BC 15
REQUEST = bytes.fromhex(
    "01 01 00 64 00 01 BC 15"
)


with serial.Serial(
    port=PORT,
    baudrate=BAUD,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=1,
) as ser:

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print("TX :", REQUEST.hex(" ").upper())

    ser.write(REQUEST)
    ser.flush()

    # Dar tiempo de sobra a la Pico.
    time.sleep(0.05)

    response = ser.read(64)

    print("RX bytes:", len(response))

    if response:
        print(
            "RX :",
            response.hex(" ").upper()
        )
    else:
        print("RX : <nada>")
import serial
import time

REQUEST = bytes.fromhex(
    "01 01 00 64 00 01 BC 15"
)

with serial.Serial(
    "/dev/ttyAMA0",
    baudrate=115200,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=1,
) as ser:

    ser.reset_input_buffer()

    print("TX:", REQUEST.hex(" ").upper())

    ser.write(REQUEST)
    ser.flush()

    response = ser.read(6)

    print("RX bytes:", len(response))
    print("RX:", response.hex(" ").upper())
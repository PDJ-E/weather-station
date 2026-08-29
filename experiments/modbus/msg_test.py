"""single_shot_test.py — manda UN pedido Modbus crudo, una sola vez,
sin pymodbus de por medio. Aisla si la Pico responde al primer intento
limpio, separado del comportamiento de reintentos de pymodbus.
"""

import serial
import time

PORT = "/dev/ttyAMA0"
BAUD = 115200

# Frame ya validado por el sniffer: Unit 1, FC01, addr 100, count 1, CRC correcto
request = bytes.fromhex("0101006400 01BC15".replace(" ", ""))

ser = serial.Serial(PORT, BAUD, bytesize=8, parity="N", stopbits=1, timeout=2)
ser.reset_input_buffer()

print("Enviando UN pedido crudo (sin pymodbus, sin reintentos):", request.hex(" "))
ser.write(request)

# Tiempo generoso para la respuesta, sin ningun mecanismo de reintento
# de por medio que pueda ensuciar la medicion.
time.sleep(0.5)

waiting = ser.in_waiting
response = ser.read(waiting) if waiting > 0 else b""

if response:
    print("Respuesta recibida:", response.hex(" "), "({} bytes)".format(len(response)))
else:
    print("NINGUNA respuesta recibida de la Pico.")

ser.close()
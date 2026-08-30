import time

from dht11_sensor import DHT11Sensor
from bme688_sensor import BME688Sensor


DHT11_PIN = 15

dht11 = DHT11Sensor(
    pin=DHT11_PIN
)

bme688 = BME688Sensor(
    sda_pin=2,
    scl_pin=3,
    address=0x76,
)


print()
print("========================================")
print(" P3-03 Environmental sensor interface")
print("========================================")
print()


for sample in range(10):

    dht_reading = dht11.read()
    bme_reading = bme688.read()

    print(
        "Sample:",
        sample + 1,
    )

    print(
        "DHT11 :",
        dht_reading,
    )

    print(
        "BME688:",
        bme_reading,
    )

    print()

    time.sleep(5)
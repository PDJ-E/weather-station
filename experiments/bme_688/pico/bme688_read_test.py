import time

from pimoroni_i2c import PimoroniI2C # type: ignore
from breakout_bme68x import BreakoutBME68X # type: ignore


BME688_ADDRESS = 0x76

i2c = PimoroniI2C(
    sda=2,
    scl=3,
)

bme = BreakoutBME68X(
    i2c,
    address=BME688_ADDRESS,
)

print()
print("========================================")
print(" P3-02 BME688 official driver test")
print("========================================")
print("Address: 0x76")
print()

for sample in range(20):

    (
        temperature_c,
        pressure_pa,
        humidity_pct,
        gas_resistance_ohm,
        status,
        gas_index,
        meas_index,
    ) = bme.read()

    pressure_hpa = pressure_pa / 100.0

    print(
        "#{:02d} | "
        "T={:.2f} C | "
        "RH={:.2f} % | "
        "P={:.2f} hPa | "
        "Gas={:.0f} ohm | "
        "status=0x{:02X} | "
        "gas_idx={} | "
        "meas_idx={}".format(
            sample + 1,
            temperature_c,
            humidity_pct,
            pressure_hpa,
            gas_resistance_ohm,
            status,
            gas_index,
            meas_index,
        )
    )

    time.sleep(1)
from pimoroni_i2c import PimoroniI2C # type: ignore
from breakout_bme68x import BreakoutBME68X # type: ignore

from environmental_reading import (
    EnvironmentalReading,
    SENSOR_STATUS_OK,
    SENSOR_STATUS_ERR_SENSOR,
)


BME68X_NEW_DATA = 0x80
BME68X_GAS_VALID = 0x20
BME68X_HEATER_STABLE = 0x10


class BME688Sensor:
    SENSOR_TYPE = "BME688"

    def __init__(
        self,
        sda_pin=2,
        scl_pin=3,
        address=0x76,
    ):
        self._i2c = PimoroniI2C(
            sda=sda_pin,
            scl=scl_pin,
        )

        self._sensor = BreakoutBME68X(
            self._i2c,
            address=address,
        )

    def read(self):
        try:
            (
                temperature_c,
                pressure_pa,
                humidity_pct,
                gas_resistance_ohm,
                raw_status,
                gas_index,
                meas_index,
            ) = self._sensor.read()

            new_data = bool(
                raw_status & BME68X_NEW_DATA
            )

            gas_valid = bool(
                raw_status & BME68X_GAS_VALID
            )

            heater_stable = bool(
                raw_status & BME68X_HEATER_STABLE
            )

            sensor_status = (
                SENSOR_STATUS_OK
                if new_data
                else SENSOR_STATUS_ERR_SENSOR
            )

            return EnvironmentalReading(
                sensor_type=self.SENSOR_TYPE,
                temperature_c=float(
                    temperature_c
                ),
                humidity_pct=float(
                    humidity_pct
                ),
                pressure_hpa=float(
                    pressure_pa
                ) / 100.0,
                gas_resistance_ohm=int(
                    gas_resistance_ohm
                ),
                sensor_status=sensor_status,
                gas_valid=gas_valid,
                heater_stable=heater_stable,
            )

        except Exception:
            return EnvironmentalReading(
                sensor_type=self.SENSOR_TYPE,
                sensor_status=SENSOR_STATUS_ERR_SENSOR,
            )
from machine import Pin # type: ignore
import dht #type: ignore

from environmental_reading import (
    EnvironmentalReading,
    SENSOR_STATUS_OK,
    SENSOR_STATUS_ERR_SENSOR,
)


class DHT11Sensor:
    SENSOR_TYPE = "DHT11"

    def __init__(self, pin):
        self._sensor = dht.DHT11(Pin(pin))

    def read(self):
        try:
            self._sensor.measure()

            return EnvironmentalReading(
                sensor_type=self.SENSOR_TYPE,
                temperature_c=float(
                    self._sensor.temperature()
                ),
                humidity_pct=float(
                    self._sensor.humidity()
                ),
                pressure_hpa=None,
                gas_resistance_ohm=None,
                sensor_status=SENSOR_STATUS_OK,
                gas_valid=None,
                heater_stable=None,
            )

        except Exception:
            return EnvironmentalReading(
                sensor_type=self.SENSOR_TYPE,
                sensor_status=SENSOR_STATUS_ERR_SENSOR,
            )
SENSOR_STATUS_OK = 0
SENSOR_STATUS_ERR_SENSOR = 1
SENSOR_STATUS_NO_DATA = 2


class EnvironmentalReading:
    __slots__ = (
        "sensor_type",
        "temperature_c",
        "humidity_pct",
        "pressure_hpa",
        "gas_resistance_ohm",
        "sensor_status",
        "gas_valid",
        "heater_stable",
    )

    def __init__(
        self,
        sensor_type,
        temperature_c=None,
        humidity_pct=None,
        pressure_hpa=None,
        gas_resistance_ohm=None,
        sensor_status=SENSOR_STATUS_NO_DATA,
        gas_valid=None,
        heater_stable=None,
    ):
        self.sensor_type = sensor_type
        self.temperature_c = temperature_c
        self.humidity_pct = humidity_pct
        self.pressure_hpa = pressure_hpa
        self.gas_resistance_ohm = gas_resistance_ohm
        self.sensor_status = sensor_status
        self.gas_valid = gas_valid
        self.heater_stable = heater_stable

    def __repr__(self):
        return (
            "EnvironmentalReading("
            "sensor_type={!r}, "
            "temperature_c={!r}, "
            "humidity_pct={!r}, "
            "pressure_hpa={!r}, "
            "gas_resistance_ohm={!r}, "
            "sensor_status={!r}, "
            "gas_valid={!r}, "
            "heater_stable={!r}"
            ")"
        ).format(
            self.sensor_type,
            self.temperature_c,
            self.humidity_pct,
            self.pressure_hpa,
            self.gas_resistance_ohm,
            self.sensor_status,
            self.gas_valid,
            self.heater_stable,
        )
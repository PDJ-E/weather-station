from bme688_sensor import BME688Sensor
from environmental_reading import SENSOR_STATUS_OK


REGISTER_MAP_VERSION = 0x0101

IREG_BME_TEMP = 209
IREG_BME_HUMIDITY = 210
IREG_BME_PRESSURE = 211
IREG_BME_GAS = 213
IREG_BME_FLAGS = 215


FLAG_GAS_VALID = 0x0001
FLAG_HEATER_STABLE = 0x0002


def split_u32(value):
    value = int(value) & 0xFFFFFFFF

    high = (value >> 16) & 0xFFFF
    low = value & 0xFFFF

    return [high, low]


def encode_int16(value):
    value = int(round(value))

    if value < -32768 or value > 32767:
        raise ValueError(
            "Value outside int16 range: {}".format(value)
        )

    return value & 0xFFFF


class BME688ModbusExtension:

    def __init__(
        self,
        server,
        sda_pin=2,
        scl_pin=3,
        address=0x76,
    ):
        self._server = server

        self._sensor = BME688Sensor(
            sda_pin=sda_pin,
            scl_pin=scl_pin,
            address=address,
        )

        self._last_good_reading = None

        self._install_registers()

    def _install_registers(self):
        # BME688 temperature, int16 x100
        self._server.add_ireg(
            address=IREG_BME_TEMP,
            value=0,
        )

        # BME688 humidity, uint16 x100
        self._server.add_ireg(
            address=IREG_BME_HUMIDITY,
            value=0,
        )

        # Pressure in Pa, uint32, high word first
        self._server.add_ireg(
            address=IREG_BME_PRESSURE,
            value=[0, 0],
        )

        # Gas resistance in ohms, uint32, high word first
        self._server.add_ireg(
            address=IREG_BME_GAS,
            value=[0, 0],
        )

        # bit 0 = gas_valid
        # bit 1 = heater_stable
        self._server.add_ireg(
            address=IREG_BME_FLAGS,
            value=0,
        )

        # Map v1.1.
        #
        # Register 208 already exists in the P2 firmware.
        self._server.set_ireg(
            address=208,
            value=REGISTER_MAP_VERSION,
        )

    def acquire_and_commit(self):
        reading = self._sensor.read()

        if reading.sensor_status != SENSOR_STATUS_OK:
            # Keep the last valid physical values.
            # Flags=0 tells the client that the newest
            # BME688 acquisition was not valid.
            self._server.set_ireg(
                address=IREG_BME_FLAGS,
                value=0,
            )

            return False

        temperature_x100 = encode_int16(
            reading.temperature_c * 100.0 # type: ignore
        )

        humidity_x100 = int(
            round(reading.humidity_pct * 100.0)   # type: ignore
        )

        # EnvironmentalReading stores hPa.
        # Modbus v1.1 transports absolute Pa.
        pressure_pa = int(
            round(reading.pressure_hpa * 100.0) # type: ignore
        )

        gas_ohm = int(
            round(reading.gas_resistance_ohm) # type: ignore
        )

        flags = 0

        if reading.gas_valid:
            flags |= FLAG_GAS_VALID

        if reading.heater_stable:
            flags |= FLAG_HEATER_STABLE

        # Build everything first, then expose it.
        pressure_words = split_u32(
            pressure_pa
        )

        gas_words = split_u32(
            gas_ohm
        )

        self._server.set_ireg(
            address=IREG_BME_TEMP,
            value=temperature_x100,
        )

        self._server.set_ireg(
            address=IREG_BME_HUMIDITY,
            value=humidity_x100,
        )

        self._server.set_ireg(
            address=IREG_BME_PRESSURE,
            value=pressure_words,
        )

        self._server.set_ireg(
            address=IREG_BME_GAS,
            value=gas_words,
        )

        self._server.set_ireg(
            address=IREG_BME_FLAGS,
            value=flags,
        )

        self._last_good_reading = reading

        return True
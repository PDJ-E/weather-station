"""
test_modbus_register_map.py

Weather Station
P2-08 — Automated Register Map integration suite.

Requiere:
    - firmware P2-06 corriendo en Pico;
    - compatibility patches habilitados;
    - Pi 5 conectada por /dev/ttyAMA0;
    - WeatherStationModbusClient P2-07.

Valida Register Map v1:

COILS
    100 run_enable
    102 read_now_trigger

HOLDING
    101 sample_interval_s

INPUT
    200 temperature_c
    201 humidity_pct
    202 sensor_status
    203 device_state
    204 uptime HIGH
    205 uptime LOW
    206 sample_counter HIGH
    207 sample_counter LOW
    208 register_map_version

RESERVED
    103

Exceptions:
    02 Illegal Data Address
    03 Illegal Data Value
"""

import time
import unittest

from weather_modbus_client import (
    WeatherStationModbusClient,
    WeatherModbusDeviceError,
    decode_int16,
    decode_uint32,
)


UNIT_ID = 1

STATE_STOPPED = 0
STATE_RUNNING = 1

SENSOR_OK = 0
SENSOR_ERR = 1
SENSOR_NO_DATA = 2

MAP_VERSION = 0x0100

DEFAULT_INTERVAL = 5


class ModbusRegisterMapTests(
    unittest.TestCase
):

    @classmethod
    def setUpClass(cls):

        print()
        print(
            "========================================"
        )
        print(
            " P2-08 Register Map Integration Suite"
        )
        print(
            "========================================"
        )
        print()

        cls.client = (
            WeatherStationModbusClient(
                port="/dev/ttyAMA0",
            )
        )

        cls.client.connect()

        cls.client.validate_register_map_version()

        # Estado inicial conocido.
        cls.client.set_run_enable(False)
        cls.client.set_sample_interval(
            DEFAULT_INTERVAL
        )

        time.sleep(0.2)


    @classmethod
    def tearDownClass(cls):

        try:

            cls.client.set_run_enable(
                False
            )

            cls.client.set_sample_interval(
                DEFAULT_INTERVAL
            )

        finally:

            cls.client.close()


    def setUp(self):

        # Cada prueba empieza STOPPED.
        self.client.set_run_enable(
            False
        )

        self.client.set_sample_interval(
            DEFAULT_INTERVAL
        )

        time.sleep(0.05)


    # ============================================================
    # Coil 100 — run_enable
    # ============================================================

    def test_010_coil_100_read_write(self):

        self.assertFalse(
            self.client.get_run_enable()
        )

        self.client.set_run_enable(
            True
        )

        self.assertTrue(
            self.client.get_run_enable()
        )

        telemetry = (
            self.client.read_telemetry()
        )

        self.assertEqual(
            telemetry.device_state,
            STATE_RUNNING,
        )

        self.client.set_run_enable(
            False
        )

        self.assertFalse(
            self.client.get_run_enable()
        )

        telemetry = (
            self.client.read_telemetry()
        )

        self.assertEqual(
            telemetry.device_state,
            STATE_STOPPED,
        )


    # ============================================================
    # Coil 102 — READ_NOW
    # ============================================================

    def test_020_coil_102_read_now(self):

        self.assertFalse(
            self.client
            .get_read_now_trigger()
        )

        # Evitar back-to-back reads del DHT11.
        time.sleep(2.1)

        before = (
            self.client.read_telemetry()
        )

        self.client.trigger_read_now()

        deadline = (
            time.monotonic()
            + 5.0
        )

        completed = False

        while (
            time.monotonic()
            < deadline
        ):

            trigger = (
                self.client
                .get_read_now_trigger()
            )

            after = (
                self.client
                .read_telemetry()
            )

            if (
                trigger is False
                and
                after.sample_counter
                > before.sample_counter
            ):
                completed = True
                break

            time.sleep(0.05)

        self.assertTrue(
            completed,
            "READ_NOW no terminó.",
        )

        self.assertEqual(
            after.sample_counter,
            before.sample_counter + 1,
        )

        self.assertFalse(
            self.client.get_run_enable()
        )


    # ============================================================
    # Holding 101
    # ============================================================

    def test_030_hreg_101_read_write(self):

        self.assertEqual(
            self.client
            .get_sample_interval(),
            DEFAULT_INTERVAL,
        )

        self.client.set_sample_interval(
            10
        )

        self.assertEqual(
            self.client
            .get_sample_interval(),
            10,
        )


    def test_031_hreg_101_minimum(self):

        self.client.set_sample_interval(
            2
        )

        self.assertEqual(
            self.client
            .get_sample_interval(),
            2,
        )


    def test_032_hreg_101_maximum(self):

        self.client.set_sample_interval(
            3600
        )

        self.assertEqual(
            self.client
            .get_sample_interval(),
            3600,
        )


    def test_033_hreg_101_below_range_exception_03(
        self,
    ):

        before = (
            self.client
            .get_sample_interval()
        )

        with self.assertRaises(
            WeatherModbusDeviceError
        ) as context:

            self.client.diagnostic_write_holding(
                101,
                1,
            )

        self.assertEqual(
            context.exception.exception_code,
            3,
        )

        # Write inválido NO debe alterar el HREG.

        self.assertEqual(
            self.client
            .get_sample_interval(),
            before,
        )


    def test_034_hreg_101_above_range_exception_03(
        self,
    ):

        before = (
            self.client
            .get_sample_interval()
        )

        with self.assertRaises(
            WeatherModbusDeviceError
        ) as context:

            self.client.diagnostic_write_holding(
                101,
                3601,
            )

        self.assertEqual(
            context.exception.exception_code,
            3,
        )

        self.assertEqual(
            self.client
            .get_sample_interval(),
            before,
        )


    # ============================================================
    # Reserved / invalid addresses
    # ============================================================

    def test_040_reserved_103_exception_02(self):

        with self.assertRaises(
            WeatherModbusDeviceError
        ) as context:

            self.client.diagnostic_read_holding(
                103
            )

        self.assertEqual(
            context.exception.exception_code,
            2,
        )


    def test_041_invalid_input_209_exception_02(
        self,
    ):

        with self.assertRaises(
            WeatherModbusDeviceError
        ) as context:

            self.client.diagnostic_read_input(
                209
            )

        self.assertEqual(
            context.exception.exception_code,
            2,
        )


    def test_042_inputs_not_writable_as_hregs(
        self,
    ):

        with self.assertRaises(
            WeatherModbusDeviceError
        ) as context:

            self.client.diagnostic_write_holding(
                200,
                1234,
            )

        self.assertEqual(
            context.exception.exception_code,
            2,
        )


    # ============================================================
    # Input 200 — temperature
    # ============================================================

    def test_100_input_200_temperature(self):

        raw = (
            self.client
            .diagnostic_read_input(
                200
            )[0]
        )

        decoded = (
            decode_int16(raw)
            / 100.0
        )

        telemetry = (
            self.client.read_telemetry()
        )

        self.assertEqual(
            decoded,
            telemetry.temperature_c,
        )


    # ============================================================
    # Input 201 — humidity
    # ============================================================

    def test_110_input_201_humidity(self):

        raw = (
            self.client
            .diagnostic_read_input(
                201
            )[0]
        )

        humidity = raw / 100.0

        self.assertGreaterEqual(
            humidity,
            0.0,
        )

        self.assertLessEqual(
            humidity,
            100.0,
        )


    # ============================================================
    # Input 202 — sensor_status
    # ============================================================

    def test_120_input_202_sensor_status(self):

        value = (
            self.client
            .diagnostic_read_input(
                202
            )[0]
        )

        self.assertIn(
            value,
            {
                SENSOR_OK,
                SENSOR_ERR,
                SENSOR_NO_DATA,
            },
        )


    # ============================================================
    # Input 203 — device_state
    # ============================================================

    def test_130_input_203_device_state(self):

        value = (
            self.client
            .diagnostic_read_input(
                203
            )[0]
        )

        self.assertIn(
            value,
            {
                0,
                1,
                2,
            },
        )

        # setUp deja siempre STOPPED.

        self.assertEqual(
            value,
            STATE_STOPPED,
        )


    # ============================================================
    # Inputs 204-205 — uptime
    # ============================================================

    def test_140_inputs_204_205_uptime(self):

        first = (
            self.client
            .diagnostic_read_input(
                204,
                count=2,
            )
        )

        first_uptime = (
            decode_uint32(
                first[0],
                first[1],
            )
        )

        time.sleep(1.1)

        second = (
            self.client
            .diagnostic_read_input(
                204,
                count=2,
            )
        )

        second_uptime = (
            decode_uint32(
                second[0],
                second[1],
            )
        )

        self.assertGreater(
            second_uptime,
            first_uptime,
        )


    # ============================================================
    # Inputs 206-207 — sample_counter
    # ============================================================

    def test_150_inputs_206_207_sample_counter(
        self,
    ):

        before_words = (
            self.client
            .diagnostic_read_input(
                206,
                count=2,
            )
        )

        before = (
            decode_uint32(
                before_words[0],
                before_words[1],
            )
        )

        time.sleep(2.1)

        self.client.trigger_read_now()

        deadline = (
            time.monotonic()
            + 5.0
        )

        after = None

        while (
            time.monotonic()
            < deadline
        ):

            trigger = (
                self.client
                .get_read_now_trigger()
            )

            words = (
                self.client
                .diagnostic_read_input(
                    206,
                    count=2,
                )
            )

            current = (
                decode_uint32(
                    words[0],
                    words[1],
                )
            )

            if (
                trigger is False
                and current > before
            ):
                after = current
                break

            time.sleep(0.05)

        self.assertIsNotNone(
            after,
        )

        self.assertEqual(
            after,
            before + 1,
        )


    # ============================================================
    # Input 208 — version
    # ============================================================

    def test_160_input_208_map_version(self):

        version = (
            self.client
            .diagnostic_read_input(
                208
            )[0]
        )

        self.assertEqual(
            version,
            MAP_VERSION,
        )


    # ============================================================
    # Full block 200-208
    # ============================================================

    def test_170_full_snapshot_block(self):

        registers = (
            self.client
            .diagnostic_read_input(
                200,
                count=9,
            )
        )

        self.assertEqual(
            len(registers),
            9,
        )

        self.assertEqual(
            registers[8],
            MAP_VERSION,
        )


    # ============================================================
    # Scheduler semantics
    # ============================================================

    def test_200_interval_controls_scheduler(
        self,
    ):

        interval = 2

        self.client.set_sample_interval(
            interval
        )

        before = (
            self.client.read_telemetry()
        )

        self.client.set_run_enable(
            True
        )

        deadline = (
            time.monotonic()
            + interval
            + 3.0
        )

        advanced = False

        while (
            time.monotonic()
            < deadline
        ):

            current = (
                self.client
                .read_telemetry()
            )

            if (
                current.sample_counter
                > before.sample_counter
            ):
                advanced = True
                break

            time.sleep(0.1)

        self.client.set_run_enable(
            False
        )

        self.assertTrue(
            advanced,
            "El scheduler no respetó "
            "sample_interval_s.",
        )


if __name__ == "__main__":

    unittest.main(
        verbosity=2
    )
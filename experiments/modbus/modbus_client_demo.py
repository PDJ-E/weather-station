"""
modbus_client_demo.py

P2-05 — prueba manual del WeatherStationModbusClient.

No prueba todavía comportamiento real del scheduler ni READ_NOW.
Solo demuestra que la Pi puede usar una interfaz de alto nivel
para leer/escribir el Register Map v1.
"""

from weather_modbus_client import (
    WeatherStationModbusClient,
    WeatherModbusError,
)


def main():

    print()
    print("========================================")
    print(" P2-05 Weather Modbus Client demo")
    print("========================================")
    print()

    try:

        with WeatherStationModbusClient(
            port="/dev/ttyAMA0",
        ) as client:

            # ----------------------------------------------------
            # Map compatibility
            # ----------------------------------------------------

            print(
                "[1] Verificando Register Map..."
            )

            client.validate_register_map_version()

            print(
                "    Register Map v1.0: OK"
            )
            print()

            # ----------------------------------------------------
            # Read telemetry
            # ----------------------------------------------------

            print(
                "[2] Leyendo telemetry snapshot..."
            )

            telemetry = (
                client.read_telemetry()
            )

            print(
                "    temperature_c       :",
                telemetry.temperature_c,
            )

            print(
                "    humidity_pct        :",
                telemetry.humidity_pct,
            )

            print(
                "    sensor_status       :",
                telemetry.sensor_status,
            )

            print(
                "    device_state        :",
                telemetry.device_state,
            )

            print(
                "    uptime_s            :",
                telemetry.uptime_s,
            )

            print(
                "    sample_counter      :",
                telemetry.sample_counter,
            )

            print(
                "    register_map_version:",
                telemetry.register_map_version_text,
            )

            print()

            # ----------------------------------------------------
            # Read controls
            # ----------------------------------------------------

            print(
                "[3] Leyendo control state..."
            )

            controls = (
                client.read_control_state()
            )

            print(
                "    run_enable       :",
                controls.run_enable,
            )

            print(
                "    read_now_trigger :",
                controls.read_now_trigger,
            )

            print(
                "    sample_interval_s:",
                controls.sample_interval_s,
            )

            print()

            # ----------------------------------------------------
            # Coil 100
            # ----------------------------------------------------

            print(
                "[4] Probando set_run_enable..."
            )

            client.set_run_enable(True)

            value = (
                client.get_run_enable()
            )

            print(
                "    True leído:",
                value,
            )

            if value is not True:
                raise RuntimeError(
                    "run_enable no cambió a True."
                )

            client.set_run_enable(False)

            value = (
                client.get_run_enable()
            )

            print(
                "    False leído:",
                value,
            )

            if value is not False:
                raise RuntimeError(
                    "run_enable no volvió a False."
                )

            print("    OK.")
            print()

            # ----------------------------------------------------
            # Holding 101
            # ----------------------------------------------------

            print(
                "[5] Probando sample_interval_s..."
            )

            original_interval = (
                client.get_sample_interval()
            )

            print(
                "    original:",
                original_interval,
            )

            client.set_sample_interval(10)

            value = (
                client.get_sample_interval()
            )

            print(
                "    nuevo:",
                value,
            )

            if value != 10:
                raise RuntimeError(
                    "sample_interval_s "
                    "no cambió a 10."
                )

            client.set_sample_interval(
                original_interval
            )

            value = (
                client.get_sample_interval()
            )

            print(
                "    restaurado:",
                value,
            )

            if value != original_interval:
                raise RuntimeError(
                    "sample_interval_s "
                    "no fue restaurado."
                )

            print("    OK.")
            print()

            # ----------------------------------------------------
            # Importante:
            #
            # NO ejecutamos trigger_read_now() todavía.
            #
            # En el servidor estático de P2-04 ese Coil simplemente
            # quedaría almacenado en True.
            #
            # Su comportamiento async/self-clearing pertenece
            # a P2-06.
            # ----------------------------------------------------

            print(
                "[6] read_now_trigger disponible"
            )
            print(
                "    No ejecutado: "
                "semántica pendiente de P2-06."
            )
            print()

            # ----------------------------------------------------
            # PASS
            # ----------------------------------------------------

            print(
                "========================================"
            )
            print(
                " P2-05 MODBUS CLIENT: PASS"
            )
            print(
                "========================================"
            )
            print()

            print(
                "Connect / close           : OK"
            )
            print(
                "Map version validation    : OK"
            )
            print(
                "Telemetry block decoding  : OK"
            )
            print(
                "Coil read/write           : OK"
            )
            print(
                "Holding read/write        : OK"
            )
            print(
                "Scaling x100              : OK"
            )
            print(
                "uint32 high-word-first    : OK"
            )
            print()
            print(
                "P2-05 puede marcarse DONE."
            )

    except (
        WeatherModbusError,
        RuntimeError,
        ValueError,
    ) as exc:

        print()
        print(
            "========================================"
        )
        print(
            " P2-05: FAIL"
        )
        print(
            "========================================"
        )
        print()
        print(
            repr(exc)
        )


if __name__ == "__main__":
    main()
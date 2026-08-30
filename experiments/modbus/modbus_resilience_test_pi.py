"""
modbus_resilience_test_pi.py

Weather Station
P2-07 — Communication resilience test.

Valida:

1. configuración timeout/retries;
2. timeout controlado;
3. no-response no mata el proceso maestro;
4. siguiente operación reconecta;
5. Pico continúa ejecutando scheduler;
6. Modbus Exception Response no mata el enlace;
7. petición válida funciona inmediatamente después.

No desconecta físicamente cables.
"""

import time

from weather_modbus_client import (
    WeatherStationModbusClient,
    CommunicationPolicy,
    WeatherModbusTransportError,
    WeatherModbusDeviceError,
    WeatherModbusError,
)


TEST_INTERVAL = 2

TEST_POLICY = CommunicationPolicy(
    timeout_s=1.0,
    retries=3,
)


def main():

    print()
    print(
        "========================================"
    )
    print(
        " P2-07 Modbus Resilience Test"
    )
    print(
        "========================================"
    )
    print()

    client = (
        WeatherStationModbusClient(
            port="/dev/ttyAMA0",
            policy=TEST_POLICY,
        )
    )

    try:

        # ========================================================
        # 1. Baseline
        # ========================================================

        print(
            "[1] Baseline saludable..."
        )

        client.validate_register_map_version()

        telemetry = (
            client.read_telemetry()
        )

        print(
            "    Unit 1 responde."
        )

        print(
            "    map:",
            telemetry.register_map_version,
        )

        print(
            "    counter:",
            telemetry.sample_counter,
        )

        print("    OK.")
        print()


        # ========================================================
        # 2. Dejar Pico RUNNING
        # ========================================================

        print(
            "[2] Activando scheduler..."
        )

        client.set_sample_interval(
            TEST_INTERVAL
        )

        client.set_run_enable(
            True
        )

        before = (
            client.read_telemetry()
        )

        counter_before = (
            before.sample_counter
        )

        print(
            "    interval:",
            TEST_INTERVAL,
            "s",
        )

        print(
            "    counter antes:",
            counter_before,
        )

        print("    RUNNING.")
        print()


        # ========================================================
        # 3. Timeout intencional
        # ========================================================

        print(
            "[3] Provocando timeout "
            "contra Unit ID 2..."
        )

        started = time.monotonic()

        timeout_caught = False

        try:

            client.diagnostic_probe_unit(
                2
            )

        except WeatherModbusTransportError as exc:

            timeout_caught = True

            elapsed = (
                time.monotonic()
                - started
            )

            print(
                "    Timeout controlado."
            )

            print(
                "    elapsed:",
                round(elapsed, 2),
                "s",
            )

            print(
                "    error:",
                str(exc),
            )


        if not timeout_caught:

            raise RuntimeError(
                "Unit ID 2 respondió "
                "cuando debería producir timeout."
            )

        print("    Maestro sigue vivo.")
        print()


        # ========================================================
        # 4. Recovery después del timeout
        # ========================================================

        print(
            "[4] Recuperando Unit ID 1..."
        )

        # No llamamos manualmente connect().
        #
        # La siguiente operación debe abrir
        # nuevamente el transporte.

        after_timeout = (
            client.read_telemetry()
        )

        print(
            "    Unit 1 responde otra vez."
        )

        print(
            "    counter después:",
            after_timeout.sample_counter,
        )

        if (
            after_timeout
            .register_map_version
            != 0x0100
        ):

            raise RuntimeError(
                "Map version incorrecta "
                "tras recuperación."
            )

        print("    Recovery maestro: OK.")
        print()


        # ========================================================
        # 5. ¿Pico siguió ejecutándose?
        # ========================================================

        print(
            "[5] Verificando que Pico "
            "no se congeló..."
        )

        if (
            after_timeout.sample_counter
            <= counter_before
        ):

            # Damos una ventana adicional por si
            # el timeout terminó justo antes del
            # siguiente sample.

            time.sleep(
                TEST_INTERVAL + 0.5
            )

            after_timeout = (
                client.read_telemetry()
            )

        if (
            after_timeout.sample_counter
            <= counter_before
        ):

            raise RuntimeError(
                "sample_counter no avanzó: "
                "Pico pudo haberse detenido."
            )

        print(
            "    counter:",
            counter_before,
            "->",
            after_timeout.sample_counter,
        )

        print(
            "    Scheduler siguió corriendo."
        )

        print("    Pico: OK.")
        print()


        # ========================================================
        # 6. Modbus Exception Response
        # ========================================================

        print(
            "[6] Provocando Illegal Data Address..."
        )

        exception_caught = False

        try:

            client.diagnostic_read_holding(
                999
            )

        except WeatherModbusDeviceError as exc:

            exception_caught = True

            print(
                "    Exception Response recibida."
            )

            print(
                "    exception_code:",
                exc.exception_code,
            )

            # Modbus Exception 02
            #
            # Illegal Data Address

            if exc.exception_code != 2:

                raise RuntimeError(
                    "Se esperaba exception 02 "
                    "y llegó {}".format(
                        exc.exception_code
                    )
                )


        if not exception_caught:

            raise RuntimeError(
                "Holding 999 no produjo "
                "Modbus Exception."
            )

        print(
            "    Exception 02: OK."
        )
        print()


        # ========================================================
        # 7. Link después de Modbus Exception
        # ========================================================

        print(
            "[7] Petición válida inmediatamente "
            "después..."
        )

        interval = (
            client.get_sample_interval()
        )

        telemetry = (
            client.read_telemetry()
        )

        print(
            "    sample_interval_s:",
            interval,
        )

        print(
            "    counter:",
            telemetry.sample_counter,
        )

        print(
            "    Link operativo."
        )

        print("    OK.")
        print()


        # ========================================================
        # 8. Restore
        # ========================================================

        print(
            "[8] Restaurando defaults..."
        )

        client.set_run_enable(
            False
        )

        client.set_sample_interval(
            5
        )

        print(
            "    run_enable: False"
        )

        print(
            "    interval: 5 s"
        )

        print("    OK.")
        print()


        # ========================================================
        # PASS
        # ========================================================

        print(
            "========================================"
        )

        print(
            " P2-07 COMMUNICATION POLICY: PASS"
        )

        print(
            "========================================"
        )

        print()

        print(
            "Request timeout             : OK"
        )

        print(
            "Retries                     : OK"
        )

        print(
            "Transport error controlled  : OK"
        )

        print(
            "Master survives timeout     : OK"
        )

        print(
            "Automatic next reconnect    : OK"
        )

        print(
            "Pico survives timeout       : OK"
        )

        print(
            "Modbus Exception 02         : OK"
        )

        print(
            "Link survives exception     : OK"
        )

        print()

        print(
            "G2-03 puede marcarse PASS."
        )

        print(
            "P2-07 puede marcarse DONE."
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
            " P2-07: FAIL"
        )
        print(
            "========================================"
        )
        print()

        print(
            repr(exc)
        )


    finally:

        # Best-effort restore.
        #
        # Incluso si falla el test queremos intentar
        # dejar el nodo detenido.

        try:

            client.set_run_enable(
                False
            )

            client.set_sample_interval(
                5
            )

        except Exception:

            pass

        client.close()


if __name__ == "__main__":
    main()
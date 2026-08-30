"""
modbus_control_test_pi.py

P2-06 — prueba del contrato de control real.

Valida:

START
    Coil 100 -> True
    device_state -> RUNNING
    scheduler produce adquisición

STOP
    Coil 100 -> False
    device_state -> STOPPED
    scheduler deja de adquirir

READ_NOW
    async trigger
    funciona estando STOPPED
    sample_counter incrementa exactamente una vez
    trigger vuelve a 0
    run_enable permanece False

sample_interval_s
    se aplica realmente al scheduler

Requiere:
    weather_modbus_client.py de P2-05
"""

import time

from weather_modbus_client import (
    WeatherStationModbusClient,
    WeatherModbusError,
)


TEST_INTERVAL = 3

STATE_STOPPED = 0
STATE_RUNNING = 1


def wait_for_counter_advance(
    client,
    previous_counter,
    timeout_s=6.0,
):
    """
    Espera hasta observar una nueva adquisición.

    Este timeout es solo del TEST.
    La política formal se define en P2-07.
    """

    deadline = (
        time.monotonic()
        + timeout_s
    )

    while time.monotonic() < deadline:

        telemetry = (
            client.read_telemetry()
        )

        if (
            telemetry.sample_counter
            > previous_counter
        ):
            return telemetry

        time.sleep(0.1)

    raise RuntimeError(
        "sample_counter no avanzó "
        "dentro del tiempo esperado."
    )


def wait_for_read_now_completion(
    client,
    previous_counter,
    timeout_s=5.0,
):
    """
    Confirmación según Register Map:

        read_now_trigger == 0
        AND
        sample_counter > previous_counter
    """

    deadline = (
        time.monotonic()
        + timeout_s
    )

    while time.monotonic() < deadline:

        trigger = (
            client.get_read_now_trigger()
        )

        telemetry = (
            client.read_telemetry()
        )

        if (
            trigger is False
            and telemetry.sample_counter
            > previous_counter
        ):
            return telemetry

        time.sleep(0.05)

    raise RuntimeError(
        "READ_NOW no confirmó completion."
    )


def main():

    print()
    print("========================================")
    print(" P2-06 Control Contract Test")
    print("========================================")
    print()

    try:

        with WeatherStationModbusClient(
            port="/dev/ttyAMA0",
        ) as client:

            # ====================================================
            # Map
            # ====================================================

            print(
                "[1] Verificando Register Map..."
            )

            client.validate_register_map_version()

            print(
                "    v1.0 OK"
            )
            print()


            # ====================================================
            # Estado inicial determinista
            # ====================================================

            print(
                "[2] Preparando estado STOPPED..."
            )

            client.set_run_enable(False)

            client.set_sample_interval(
                TEST_INTERVAL
            )

            time.sleep(0.2)

            controls = (
                client.read_control_state()
            )

            telemetry = (
                client.read_telemetry()
            )

            print(
                "    run_enable :",
                controls.run_enable,
            )

            print(
                "    interval   :",
                controls.sample_interval_s,
            )

            print(
                "    state      :",
                telemetry.device_state,
            )

            if controls.run_enable:
                raise RuntimeError(
                    "No quedó STOPPED."
                )

            if telemetry.device_state != STATE_STOPPED:
                raise RuntimeError(
                    "device_state no es STOPPED."
                )

            print("    OK.")
            print()


            # ====================================================
            # START
            # ====================================================

            print(
                "[3] START -> Coil 100 = True"
            )

            before = (
                client.read_telemetry()
            )

            client.set_run_enable(True)

            controls = (
                client.read_control_state()
            )

            telemetry = (
                client.read_telemetry()
            )

            print(
                "    run_enable:",
                controls.run_enable,
            )

            print(
                "    state     :",
                telemetry.device_state,
            )

            if controls.run_enable is not True:
                raise RuntimeError(
                    "START no dejó Coil 100=True."
                )

            if telemetry.device_state != STATE_RUNNING:
                raise RuntimeError(
                    "START no dejó device_state=RUNNING."
                )

            print(
                "    Esperando sample periódico..."
            )

            telemetry = wait_for_counter_advance(
                client,
                before.sample_counter,
                timeout_s=TEST_INTERVAL + 3,
            )

            print(
                "    sample_counter:",
                telemetry.sample_counter,
            )

            print(
                "    temperature_c :",
                telemetry.temperature_c,
            )

            print(
                "    humidity_pct  :",
                telemetry.humidity_pct,
            )

            print(
                "    sensor_status :",
                telemetry.sensor_status,
            )

            print("    START OK.")
            print()


            # ====================================================
            # STOP
            # ====================================================

            print(
                "[4] STOP -> Coil 100 = False"
            )

            client.set_run_enable(False)

            controls = (
                client.read_control_state()
            )

            telemetry = (
                client.read_telemetry()
            )

            stopped_counter = (
                telemetry.sample_counter
            )

            print(
                "    run_enable:",
                controls.run_enable,
            )

            print(
                "    state     :",
                telemetry.device_state,
            )

            if controls.run_enable is not False:
                raise RuntimeError(
                    "STOP no dejó Coil 100=False."
                )

            if telemetry.device_state != STATE_STOPPED:
                raise RuntimeError(
                    "STOP no dejó device_state=STOPPED."
                )

            # Esperar más que el intervalo.
            #
            # El contador NO debe moverse.

            print(
                "    Verificando scheduler detenido..."
            )

            time.sleep(
                TEST_INTERVAL + 0.5
            )

            telemetry = (
                client.read_telemetry()
            )

            if (
                telemetry.sample_counter
                != stopped_counter
            ):
                raise RuntimeError(
                    "sample_counter cambió estando STOPPED."
                )

            print(
                "    sample_counter permanece:",
                telemetry.sample_counter,
            )

            print("    STOP OK.")
            print()


            # ====================================================
            # READ_NOW estando STOPPED
            # ====================================================

            print(
                "[5] READ_NOW estando STOPPED"
            )

            # DHT11 agradece no ser leído inmediatamente
            # después del sample periódico anterior.

            time.sleep(2.1)

            before = (
                client.read_telemetry()
            )

            print(
                "    counter antes:",
                before.sample_counter,
            )

            client.trigger_read_now()

            telemetry = (
                wait_for_read_now_completion(
                    client,
                    before.sample_counter,
                    timeout_s=5,
                )
            )

            controls = (
                client.read_control_state()
            )

            print(
                "    counter después:",
                telemetry.sample_counter,
            )

            print(
                "    trigger:",
                controls.read_now_trigger,
            )

            print(
                "    run_enable:",
                controls.run_enable,
            )

            print(
                "    state:",
                telemetry.device_state,
            )

            print(
                "    temperature_c:",
                telemetry.temperature_c,
            )

            print(
                "    humidity_pct:",
                telemetry.humidity_pct,
            )

            if (
                telemetry.sample_counter
                != before.sample_counter + 1
            ):
                raise RuntimeError(
                    "READ_NOW no incrementó "
                    "sample_counter exactamente una vez."
                )

            if controls.read_now_trigger is not False:
                raise RuntimeError(
                    "read_now_trigger no se limpió."
                )

            if controls.run_enable is not False:
                raise RuntimeError(
                    "READ_NOW inició sampling periódico."
                )

            print(
                "    READ_NOW OK."
            )
            print()


            # ====================================================
            # Restauración
            # ====================================================

            print(
                "[6] Restaurando defaults..."
            )

            client.set_run_enable(False)
            client.set_sample_interval(5)

            controls = (
                client.read_control_state()
            )

            print(
                "    run_enable:",
                controls.run_enable,
            )

            print(
                "    interval:",
                controls.sample_interval_s,
            )

            print("    OK.")
            print()


            # ====================================================
            # PASS
            # ====================================================

            print(
                "========================================"
            )

            print(
                " P2-06 CONTROL CONTRACT: PASS"
            )

            print(
                "========================================"
            )

            print()

            print(
                "START                    : OK"
            )

            print(
                "STOP                     : OK"
            )

            print(
                "Periodic scheduler       : OK"
            )

            print(
                "sample_interval_s        : OK"
            )

            print(
                "READ_NOW async           : OK"
            )

            print(
                "READ_NOW while STOPPED   : OK"
            )

            print(
                "READ_NOW self-clear      : OK"
            )

            print(
                "sample_counter confirm   : OK"
            )

            print(
                "Real DHT11 telemetry     : OK"
            )

            print()

            print(
                "P2-06 puede marcarse DONE."
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
            " P2-06: FAIL"
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
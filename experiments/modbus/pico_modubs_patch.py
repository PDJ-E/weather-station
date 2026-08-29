"""
pico_modbus_patch.py

Compatibility patch TEMPORAL para micropython-modbus 2.3.7
sobre Raspberry Pi Pico 2 durante P2.

Problema observado:
    El frame reader original de micropython-modbus puede consumir varias
    peticiones Modbus RTU acumuladas como si fueran una sola trama.

P2 Register Map v1 solo utiliza:

    FC01 - Read Coils
    FC03 - Read Holding Registers
    FC04 - Read Input Registers
    FC05 - Write Single Coil
    FC06 - Write Single Holding Register

Los REQUESTS RTU de estas cinco funciones tienen exactamente 8 bytes.

Este patch sustituye únicamente _uart_read_frame() de la instancia
ModbusRTU utilizada por nuestro firmware.

NO modifica /lib/umodbus/serial.py.

IMPORTANTE:
    Este patch debe reevaluarse si:
    - se actualiza micropython-modbus;
    - se cambia de plataforma;
    - se añaden function codes con requests de longitud variable,
      como FC15 o FC16.
"""

import time


P2_MODBUS_REQUEST_LENGTH = 8

# Una petición completa de 8 bytes a 115200 baud tarda menos de 1 ms.
# 10 ms deja margen amplio para recibir fragmentos sin bloquear
# indefinidamente el loop cooperativo.
P2_FRAME_TIMEOUT_US = 10_000


def apply_umodbus_p2_patch(server):
    """
    Aplica el frame-reader patch a una instancia ModbusRTU.

    Args:
        server:
            Instancia de umodbus.serial.ModbusRTU.

    Returns:
        None
    """

    interface = server._itf

    def patched_uart_read_frame(timeout=None):
        """
        Lee exactamente una petición Modbus RTU de P2 v1.

        server.process() llama continuamente a esta función.

        Si no hay ningún byte disponible, retorna inmediatamente.

        Una vez comienza a llegar una petición, espera como máximo
        P2_FRAME_TIMEOUT_US para completar sus 8 bytes.
        """

        received = bytearray()

        # ---------------------------------------------------------
        # No hay request esperando.
        #
        # Mantiene server.process() no bloqueante mientras UART está
        # inactivo.
        # ---------------------------------------------------------

        if not interface._uart.any():
            return received

        # ---------------------------------------------------------
        # Ya comenzó una petición.
        #
        # Desde este momento esperamos hasta reunir exactamente
        # los 8 bytes del request o agotar el timeout local.
        # ---------------------------------------------------------

        start_us = time.ticks_us() #type:ignore

        while len(received) < P2_MODBUS_REQUEST_LENGTH:

            available = interface._uart.any()

            if available:

                remaining = (
                    P2_MODBUS_REQUEST_LENGTH
                    - len(received)
                )

                # Nunca consumir bytes que pertenezcan a una
                # petición posterior.
                bytes_to_read = min(
                    available,
                    remaining,
                )

                chunk = interface._uart.read(
                    bytes_to_read
                )

                if chunk:
                    received.extend(chunk)

                    if (
                        len(received)
                        == P2_MODBUS_REQUEST_LENGTH
                    ):
                        return received

            # -----------------------------------------------------
            # Protección contra frame incompleto.
            # -----------------------------------------------------

            elapsed_us = time.ticks_diff( #type:ignore
                time.ticks_us(), #type:ignore
                start_us,
            )

            if elapsed_us > P2_FRAME_TIMEOUT_US:
                break

            # Pequeña cesión para no hacer busy-spin continuo.
            time.sleep_us(50) #type:ignore

        return received

    # -------------------------------------------------------------
    # Monkey patch únicamente sobre ESTA instancia.
    #
    # La librería instalada permanece sin modificaciones.
    # -------------------------------------------------------------

    interface._uart_read_frame = patched_uart_read_frame
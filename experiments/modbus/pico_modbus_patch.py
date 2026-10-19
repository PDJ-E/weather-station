"""
pico_modbus_patch.py

Compatibility patch TEMPORAL para micropython-modbus 2.3.7
sobre Raspberry Pi Pico 2.

Problema original (P2, UART directo):
    El frame reader original de micropython-modbus puede consumir varias
    peticiones Modbus RTU acumuladas como si fueran una sola trama.

Problema añadido en P4-08 (RS-485 half-duplex):
    Con el bus RS-485 aparecen bytes basura en RX:
    - mientras el nodo transmite, /RE está en HIGH y RO queda en
      alta impedancia (la SparkFun no tiene pull-up en RO);
    - con el bus en reposo y terminado, sin resistencias de bias,
      la tensión diferencial queda ~0 V y el receptor puede generar
      bytes espurios.

    El reader de P2 leía "exactamente 8 bytes" a ciegas. Un solo byte
    basura desalineaba todas las peticiones siguientes de forma
    permanente: cada trama quedaba con CRC inválido y el servidor
    dejaba de responder hasta reiniciar la Pico.

Solución:
    Reader con buffer persistente y resincronización:
    - descarta fragmentos viejos tras un silencio en la línea;
    - busca una ventana de 8 bytes con Unit ID válido, function code
      soportado y CRC correcto;
    - si la ventana no es válida, descarta UN byte y vuelve a probar.

P2 Register Map v1 solo utiliza:

    FC01 - Read Coils
    FC03 - Read Holding Registers
    FC04 - Read Input Registers
    FC05 - Write Single Coil
    FC06 - Write Single Holding Register

Los REQUESTS RTU de estas cinco funciones tienen exactamente 8 bytes.

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

P2_SUPPORTED_FUNCTION_CODES = (1, 3, 4, 5, 6)

# Una petición completa de 8 bytes a 115200 baud tarda menos de 1 ms.
# 10 ms deja margen amplio para recibir fragmentos sin bloquear
# indefinidamente el loop cooperativo.
P2_FRAME_TIMEOUT_US = 10_000

# Si hay bytes en el buffer y la línea lleva este tiempo en silencio,
# son restos de una trama incompleta o ruido: se descartan.
# Modbus RTU usa 3.5 caracteres (~1750 us a >19200 baud); aquí se
# deja margen porque el loop principal cede ~2 ms por iteración.
P2_STALE_FRAGMENT_US = 5_000

# Límite del buffer interno para no crecer sin control con ruido.
P2_MAX_BUFFER = 64


def apply_umodbus_p2_patch(server):
    """
    Aplica el frame-reader patch (con resincronización) a una
    instancia ModbusRTU.

    Expone en la interfaz dos contadores de diagnóstico:
        interface.p2_discarded_bytes
        interface.p2_stale_flushes

    Args:
        server:
            Instancia de umodbus.serial.ModbusRTU.

    Returns:
        None
    """

    interface = server._itf
    addr_list = server._addr_list

    interface.p2_discarded_bytes = 0
    interface.p2_stale_flushes = 0

    state = {
        "buf": bytearray(),
        "last_rx_us": time.ticks_us(),  # type: ignore
    }

    def _pull_available():
        """Mueve a buf todo lo disponible en UART (acotado)."""

        available = interface._uart.any()

        if not available:
            return False

        room = P2_MAX_BUFFER - len(state["buf"])

        if room <= 0:
            # Buffer lleno de basura: conservar solo la cola.
            keep = P2_MODBUS_REQUEST_LENGTH - 1
            dropped = len(state["buf"]) - keep
            state["buf"] = state["buf"][-keep:]
            interface.p2_discarded_bytes += dropped
            room = P2_MAX_BUFFER - len(state["buf"])

        chunk = interface._uart.read(min(available, room))

        if chunk:
            state["buf"].extend(chunk)
            state["last_rx_us"] = time.ticks_us()  # type: ignore
            return True

        return False

    def _extract_frame():
        """
        Busca una petición válida al inicio de buf.

        Devuelve la trama (bytearray de 8) o None.
        Descarta byte a byte lo que no puede ser inicio de trama.
        """

        buf = state["buf"]

        while len(buf) >= P2_MODBUS_REQUEST_LENGTH:

            candidate = buf[:P2_MODBUS_REQUEST_LENGTH]

            if (
                candidate[0] in addr_list
                and candidate[1] in P2_SUPPORTED_FUNCTION_CODES
            ):
                expected_crc = interface._calculate_crc16(
                    candidate[:-2]
                )

                if (
                    candidate[6] == expected_crc[0]
                    and candidate[7] == expected_crc[1]
                ):
                    state["buf"] = buf[P2_MODBUS_REQUEST_LENGTH:]
                    return candidate

            # No es una trama válida: avanzar un byte.
            buf = buf[1:]
            interface.p2_discarded_bytes += 1

        # Menos de 8 bytes: descartar prefijos que no pueden
        # iniciar una trama (Unit ID ajeno).
        while buf and buf[0] not in addr_list:
            buf = buf[1:]
            interface.p2_discarded_bytes += 1

        state["buf"] = buf
        return None

    def patched_uart_read_frame(timeout=None):
        """
        Devuelve exactamente una petición Modbus RTU válida de 8 bytes,
        o un bytearray vacío si no hay ninguna completa.

        server.process() llama continuamente a esta función: si no hay
        nada pendiente, retorna inmediatamente.
        """

        _pull_available()

        # Fragmento viejo + silencio en la línea = descartar.
        if state["buf"]:
            idle_us = time.ticks_diff(  # type: ignore
                time.ticks_us(),  # type: ignore
                state["last_rx_us"],
            )

            if (
                idle_us > P2_STALE_FRAGMENT_US
                and len(state["buf"]) < P2_MODBUS_REQUEST_LENGTH
            ):
                interface.p2_discarded_bytes += len(state["buf"])
                interface.p2_stale_flushes += 1
                state["buf"] = bytearray()
                return bytearray()

        frame = _extract_frame()

        if frame is not None:
            return frame

        if not state["buf"]:
            return bytearray()

        # Hay el inicio plausible de una petición: esperar el resto
        # como máximo P2_FRAME_TIMEOUT_US.
        start_us = time.ticks_us()  # type: ignore

        while time.ticks_diff(  # type: ignore
            time.ticks_us(),  # type: ignore
            start_us,
        ) <= P2_FRAME_TIMEOUT_US:

            if _pull_available():
                frame = _extract_frame()

                if frame is not None:
                    return frame

            time.sleep_us(50)  # type: ignore

        # Se conserva lo recibido; si la línea sigue en silencio,
        # la próxima llamada lo descartará como fragmento viejo.
        return bytearray()

    # -------------------------------------------------------------
    # Monkey patch únicamente sobre ESTA instancia.
    # -------------------------------------------------------------

    interface._uart_read_frame = patched_uart_read_frame


# Second patch: validate sample_interval_s before sending response.

def apply_sample_interval_validation_patch(
    server,
    register_address=101,
    minimum=2,
    maximum=3600,
):
    """
    Weather Station P2 compatibility patch.

    micropython-modbus 2.3.7 realiza FC06 así:

        set_hreg()
        send_response()
        on_set_cb()

    Eso impide que on_set_cb rechace un valor antes
    de que el servidor responda.

    El Register Map v1 de Weather Station exige:

        Holding 101 sample_interval_s
        valid: 2..3600
        invalid: Modbus Exception 03

    Este patch intercepta únicamente FC06/HREG 101
    ANTES del handler original.

    El resto de operaciones siguen usando el código
    original de micropython-modbus.
    """

    from umodbus import const as Const  # type: ignore

    original_write_access = (
        server._process_write_access
    )

    def patched_write_access(
        request,
        reg_type,
    ):
        is_target = (
            reg_type == "HREGS"
            and request.function
            == Const.WRITE_SINGLE_REGISTER
            and request.register_addr
            == register_address
        )

        if is_target:

            if (
                request.data is None
                or len(request.data) != 2
            ):
                request.send_exception(
                    Const.ILLEGAL_DATA_VALUE
                )
                return

            value = (
                (request.data[0] << 8)
                | request.data[1]
            )

            if not (
                minimum
                <= value
                <= maximum
            ):
                request.send_exception(
                    Const.ILLEGAL_DATA_VALUE
                )
                return

        return original_write_access(
            request=request,
            reg_type=reg_type,
        )

    server._process_write_access = (
        patched_write_access
    )

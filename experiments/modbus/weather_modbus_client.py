"""
weather_modbus_client.py

Weather Station
P2-05 + P2-07

Cliente Modbus RTU reutilizable y resiliente.

Pi 5:
    /dev/ttyAMA0
    115200 8N1
    PyModbus 3.15.0
    Unit ID 1

Política P2-07:
    timeout = 1.0 s
    retries = 3

Transport error:
    - no crash del proceso
    - cerrar/resetear transporte local
    - siguiente operación intenta reconectar

Modbus exception response:
    - reportar error controlado
    - NO resetear el enlace

No existen loops infinitos de retry.
"""

from dataclasses import dataclass

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException


# ================================================================
# Register Map v1
# ================================================================

UNIT_ID = 1

COIL_RUN_ENABLE = 100
COIL_READ_NOW_TRIGGER = 102

HREG_SAMPLE_INTERVAL_S = 101

IREG_TELEMETRY_START = 200
IREG_TELEMETRY_COUNT = 9

EXPECTED_MAP_VERSION = 0x0100


# ================================================================
# Communication policy
# ================================================================

@dataclass(frozen=True)
class CommunicationPolicy:
    timeout_s: float = 1.0
    retries: int = 3


DEFAULT_POLICY = CommunicationPolicy()


# ================================================================
# Exceptions
# ================================================================

class WeatherModbusError(Exception):
    """Base error."""


class WeatherModbusConnectionError(
    WeatherModbusError
):
    """No se pudo abrir el transporte."""


class WeatherModbusResponseError(
    WeatherModbusError
):
    """
    Base para errores ocurridos durante
    una transacción Modbus.
    """


class WeatherModbusTransportError(
    WeatherModbusResponseError
):
    """
    Timeout, pérdida de conexión,
    error serial o ModbusIOException.
    """


class WeatherModbusDeviceError(
    WeatherModbusResponseError
):
    """
    El dispositivo respondió con una
    Exception Response Modbus válida.
    """

    def __init__(
        self,
        operation,
        exception_code,
        response,
    ):
        self.operation = operation
        self.exception_code = exception_code
        self.response = response

        super().__init__(
            "{}: Modbus exception code {}: {}".format(
                operation,
                exception_code,
                response,
            )
        )


class WeatherModbusProtocolError(
    WeatherModbusResponseError
):
    """
    Respuesta válida a nivel transporte
    pero incompatible/mal formada.
    """


# ================================================================
# Models
# ================================================================

@dataclass(frozen=True)
class TelemetrySnapshot:
    temperature_c: float
    humidity_pct: float

    sensor_status: int
    device_state: int

    uptime_s: int
    sample_counter: int

    register_map_version: int

    @property
    def register_map_version_text(self):
        major = (
            self.register_map_version >> 8
        ) & 0xFF

        minor = (
            self.register_map_version
        ) & 0xFF

        return "v{}.{}".format(
            major,
            minor,
        )


@dataclass(frozen=True)
class ControlState:
    run_enable: bool
    read_now_trigger: bool
    sample_interval_s: int


# ================================================================
# Decode helpers
# ================================================================

def decode_int16(value):
    value &= 0xFFFF

    if value & 0x8000:
        return value - 0x10000

    return value


def decode_uint32(
    high_word,
    low_word,
):
    return (
        ((high_word & 0xFFFF) << 16)
        |
        (low_word & 0xFFFF)
    )


# ================================================================
# Client
# ================================================================

class WeatherStationModbusClient:

    def __init__(
        self,
        port="/dev/ttyAMA0",
        baudrate=115200,
        unit_id=UNIT_ID,
        policy=DEFAULT_POLICY,
    ):
        self.port = port
        self.baudrate = baudrate
        self.unit_id = unit_id
        self.policy = policy

        self._client = (
            self._build_client()
        )

        self._is_connected = False


    # ------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------

    def _build_client(self):

        return ModbusSerialClient(
            port=self.port,
            baudrate=self.baudrate,
            parity="N",
            stopbits=1,
            bytesize=8,
            timeout=self.policy.timeout_s,
            retries=self.policy.retries,
        )


    # ------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------

    def connect(self):

        if self._is_connected:
            return

        try:

            connected = (
                self._client.connect()
            )

        except Exception as exc:

            self._reset_transport()

            raise WeatherModbusConnectionError(
                "No se pudo abrir {}: {}".format(
                    self.port,
                    exc,
                )
            ) from exc

        if not connected:

            self._reset_transport()

            raise WeatherModbusConnectionError(
                "No se pudo abrir {}".format(
                    self.port
                )
            )

        self._is_connected = True


    def close(self):

        try:

            self._client.close()

        finally:

            self._is_connected = False


    def _reset_transport(self):
        """
        Deja el objeto preparado para una
        conexión limpia en el siguiente request.
        """

        try:

            self._client.close()

        except Exception:

            pass

        self._is_connected = False

        # Objeto PyModbus nuevo para evitar
        # conservar estado serial dudoso.

        self._client = (
            self._build_client()
        )


    def _ensure_connected(self):

        if not self._is_connected:
            self.connect()


    def __enter__(self):

        self.connect()

        return self


    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):

        self.close()


    # ============================================================
    # Transaction wrapper
    # ============================================================

    def _execute(
        self,
        operation,
        description,
    ):
        """
        ÚNICO punto donde las operaciones
        PyModbus atraviesan la política P2-07.
        """

        self._ensure_connected()

        try:

            result = operation()

        except (
            ModbusException,
            OSError,
        ) as exc:

            # Transport failure.
            #
            # No dejamos el objeto serial en
            # estado potencialmente inconsistente.

            self._reset_transport()

            raise WeatherModbusTransportError(
                "{}: {}".format(
                    description,
                    exc,
                )
            ) from exc


        if result is None:

            self._reset_transport()

            raise WeatherModbusTransportError(
                "{}: respuesta vacía".format(
                    description
                )
            )


        # Una Modbus Exception Response es una
        # respuesta VÁLIDA del dispositivo.
        #
        # Por tanto NO reseteamos el transporte.

        if result.isError():

            exception_code = getattr(
                result,
                "exception_code",
                None,
            )

            raise WeatherModbusDeviceError(
                operation=description,
                exception_code=exception_code,
                response=result,
            )


        return result


    # ============================================================
    # Low-level map access
    # ============================================================

    def _read_coil(
        self,
        address,
        device_id=None,
    ):

        target = (
            self.unit_id
            if device_id is None
            else device_id
        )

        result = self._execute(
            lambda: self._client.read_coils(
                address=address,
                count=1,
                device_id=target,
            ),
            "FC01 Coil {} Unit {}".format(
                address,
                target,
            ),
        )

        if not result.bits:

            raise WeatherModbusProtocolError(
                "FC01 Coil {} sin datos".format(
                    address
                )
            )

        return bool(
            result.bits[0]
        )


    def _write_coil(
        self,
        address,
        value,
    ):

        self._execute(
            lambda: self._client.write_coil(
                address=address,
                value=bool(value),
                device_id=self.unit_id,
            ),
            "FC05 Coil {}".format(
                address
            ),
        )


    def _read_holding_register(
        self,
        address,
        device_id=None,
    ):

        target = (
            self.unit_id
            if device_id is None
            else device_id
        )

        result = self._execute(
            lambda: (
                self._client
                .read_holding_registers(
                    address=address,
                    count=1,
                    device_id=target,
                )
            ),
            "FC03 Holding {} Unit {}".format(
                address,
                target,
            ),
        )

        if len(result.registers) != 1:

            raise WeatherModbusProtocolError(
                "Holding {}: se esperaba "
                "1 register y llegaron {}".format(
                    address,
                    len(result.registers),
                )
            )

        return result.registers[0]


    def _write_holding_register(
        self,
        address,
        value,
    ):

        self._execute(
            lambda: (
                self._client.write_register(
                    address=address,
                    value=value,
                    device_id=self.unit_id,
                )
            ),
            "FC06 Holding {}".format(
                address
            ),
        )


    # ============================================================
    # Public control API
    # ============================================================

    def get_run_enable(self):

        return self._read_coil(
            COIL_RUN_ENABLE
        )


    def set_run_enable(
        self,
        enabled,
    ):

        self._write_coil(
            COIL_RUN_ENABLE,
            enabled,
        )


    def get_read_now_trigger(self):

        return self._read_coil(
            COIL_READ_NOW_TRIGGER
        )


    def trigger_read_now(self):

        self._write_coil(
            COIL_READ_NOW_TRIGGER,
            True,
        )


    def get_sample_interval(self):

        return (
            self._read_holding_register(
                HREG_SAMPLE_INTERVAL_S
            )
        )


    def set_sample_interval(
        self,
        seconds,
    ):

        if not isinstance(
            seconds,
            int,
        ):

            raise ValueError(
                "sample_interval_s debe ser int"
            )

        if not 2 <= seconds <= 3600:

            raise ValueError(
                "sample_interval_s debe estar "
                "entre 2 y 3600"
            )

        self._write_holding_register(
            HREG_SAMPLE_INTERVAL_S,
            seconds,
        )


    # ============================================================
    # Telemetry
    # ============================================================

    def read_telemetry(self):

        result = self._execute(
            lambda: (
                self._client
                .read_input_registers(
                    address=(
                        IREG_TELEMETRY_START
                    ),
                    count=(
                        IREG_TELEMETRY_COUNT
                    ),
                    device_id=self.unit_id,
                )
            ),
            "FC04 Input 200-208",
        )

        registers = (
            result.registers
        )

        if (
            len(registers)
            != IREG_TELEMETRY_COUNT
        ):

            raise WeatherModbusProtocolError(
                "Se esperaban 9 Input Registers "
                "y llegaron {}".format(
                    len(registers)
                )
            )

        temperature_raw = (
            decode_int16(
                registers[0]
            )
        )

        temperature_c = (
            temperature_raw
            / 100.0
        )

        humidity_pct = (
            registers[1]
            / 100.0
        )

        uptime_s = (
            decode_uint32(
                registers[4],
                registers[5],
            )
        )

        sample_counter = (
            decode_uint32(
                registers[6],
                registers[7],
            )
        )

        return TelemetrySnapshot(
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            sensor_status=registers[2],
            device_state=registers[3],
            uptime_s=uptime_s,
            sample_counter=sample_counter,
            register_map_version=registers[8],
        )


    # ============================================================
    # Convenience
    # ============================================================

    def read_control_state(self):

        return ControlState(
            run_enable=(
                self.get_run_enable()
            ),
            read_now_trigger=(
                self.get_read_now_trigger()
            ),
            sample_interval_s=(
                self.get_sample_interval()
            ),
        )


    def read_register_map_version(self):

        return (
            self.read_telemetry()
            .register_map_version
        )


    def validate_register_map_version(self):

        version = (
            self.read_register_map_version()
        )

        if version != EXPECTED_MAP_VERSION:

            raise WeatherModbusProtocolError(
                "Register Map incompatible: "
                "esperado 0x{:04X}, "
                "recibido 0x{:04X}".format(
                    EXPECTED_MAP_VERSION,
                    version,
                )
            )


    # ============================================================
    # P2-07 diagnostics
    # ============================================================

    def diagnostic_probe_unit(
        self,
        unit_id,
    ):
        """
        Lee Coil 100 de un Unit ID arbitrario.

        Solo diagnóstico/integration testing.

        Unit inexistente:
            -> timeout
            -> retries
            -> WeatherModbusTransportError
        """

        return self._read_coil(
            COIL_RUN_ENABLE,
            device_id=unit_id,
        )


    def diagnostic_read_holding(
        self,
        address,
    ):
        """
        Permite provocar una Exception Response
        con una dirección inexistente.

        Solo diagnóstico/integration testing.
        """

        return (
            self._read_holding_register(
                address
            )
        )
    def diagnostic_write_holding(
        self,
        address,
        value,
    ):
        """
        Escritura FC06 sin validación de negocio
        del cliente.

        Solo integration testing.

        Permite comprobar las validaciones
        implementadas por el servidor.
        """

        self._write_holding_register(
            address,
            value,
        )

    def diagnostic_read_input(
        self,
        address,
        count=1,
    ):
        """
        Lectura FC04 arbitraria.

        Solo integration testing.
        """

        result = self._execute(
            lambda: (
                self._client
                .read_input_registers(
                    address=address,
                    count=count,
                    device_id=self.unit_id,
                )
            ),
            "FC04 Input {} count {}".format(
                address,
                count,
            ),
        )

        return result.registers
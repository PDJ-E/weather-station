"""
weather_modbus_client.py

P2-05 — Cliente Modbus RTU reutilizable para Weather Station.

Raspberry Pi 5:
    PyModbus 3.15.0
    /dev/ttyAMA0
    115200 8N1
    Unit ID 1

Responsabilidad de este módulo:
- Encapsular PyModbus.
- Encapsular offsets del Register Map v1.
- Leer/escribir Coils y Holding Registers.
- Leer y decodificar el bloque 200-208.
- Aplicar scaling y word order del contrato.

NO pertenece todavía a este módulo:
- reconciliación tras reboot;
- política avanzada de reconnect;
- liveness;
- confirmación semántica de READ_NOW;
- lógica START/STOP;
- persistencia en PostgreSQL.

Eso corresponde a P2-06 / P2-07.
"""

from dataclasses import dataclass

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException


# ================================================================
# Register Map v1
# ================================================================

UNIT_ID = 1

# Coils
COIL_RUN_ENABLE = 100
COIL_READ_NOW_TRIGGER = 102

# Holding Registers
HREG_SAMPLE_INTERVAL_S = 101

# Input Registers
IREG_TELEMETRY_START = 200
IREG_TELEMETRY_COUNT = 9

IREG_TEMPERATURE_C = 200
IREG_HUMIDITY_PCT = 201
IREG_SENSOR_STATUS = 202
IREG_DEVICE_STATE = 203
IREG_UPTIME_HIGH = 204
IREG_UPTIME_LOW = 205
IREG_SAMPLE_COUNTER_HIGH = 206
IREG_SAMPLE_COUNTER_LOW = 207
IREG_REGISTER_MAP_VERSION = 208

EXPECTED_MAP_VERSION = 0x0100


# ================================================================
# Exceptions
# ================================================================

class WeatherModbusError(Exception):
    """Error base del cliente Modbus de Weather Station."""


class WeatherModbusConnectionError(WeatherModbusError):
    """No fue posible abrir/utilizar el enlace Modbus."""


class WeatherModbusResponseError(WeatherModbusError):
    """El dispositivo respondió con error o respuesta inválida."""


# ================================================================
# Modelos
# ================================================================

@dataclass(frozen=True)
class TelemetrySnapshot:
    """
    Snapshot decodificado de Input Registers 200-208.
    """

    temperature_c: float
    humidity_pct: float

    sensor_status: int
    device_state: int

    uptime_s: int
    sample_counter: int

    register_map_version: int

    @property
    def register_map_version_text(self) -> str:
        major = (self.register_map_version >> 8) & 0xFF
        minor = self.register_map_version & 0xFF

        return f"v{major}.{minor}"


@dataclass(frozen=True)
class ControlState:
    """
    Estado observable de la interfaz de control.
    """

    run_enable: bool
    read_now_trigger: bool
    sample_interval_s: int


# ================================================================
# Decoders
# ================================================================

def decode_int16(value: int) -> int:
    """
    Convierte un word Modbus uint16 a int16 two's complement.
    """

    value &= 0xFFFF

    if value & 0x8000:
        return value - 0x10000

    return value


def decode_uint32(high_word: int, low_word: int) -> int:
    """
    Reconstruye uint32 según el contrato P2:
    HIGH word primero.
    """

    return (
        ((high_word & 0xFFFF) << 16)
        | (low_word & 0xFFFF)
    )


# ================================================================
# Cliente
# ================================================================

class WeatherStationModbusClient:
    """
    Cliente Modbus RTU de alto nivel para Weather Station.
    """

    def __init__(
        self,
        port: str = "/dev/ttyAMA0",
        baudrate: int = 115200,
        unit_id: int = UNIT_ID,
        timeout: float = 1.0,
        retries: int = 3,
    ):
        self.port = port
        self.baudrate = baudrate
        self.unit_id = unit_id

        # timeout/retries son provisionales durante P2-05.
        # La política formal se congela en P2-07.
        self._client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity="N",
            stopbits=1,
            bytesize=8,
            timeout=timeout,
            retries=retries,
        )

        self._is_connected = False

    # ------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------

    def connect(self) -> None:
        """
        Abre el puerto serial.

        Nota:
        Esto demuestra que el puerto pudo abrirse.
        No demuestra por sí solo que la Pico esté respondiendo.
        """

        try:
            connected = self._client.connect()

        except Exception as exc:
            raise WeatherModbusConnectionError(
                f"No se pudo abrir {self.port}: {exc}"
            ) from exc

        if not connected:
            raise WeatherModbusConnectionError(
                f"No se pudo abrir {self.port}"
            )

        self._is_connected = True

    def close(self) -> None:
        self._client.close()
        self._is_connected = False

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

    # ------------------------------------------------------------
    # Response validation
    # ------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._is_connected:
            raise WeatherModbusConnectionError(
                "El cliente Modbus no está conectado."
            )

    @staticmethod
    def _require_ok(result, operation: str):
        if result is None:
            raise WeatherModbusResponseError(
                f"{operation}: respuesta vacía."
            )

        if result.isError():
            raise WeatherModbusResponseError(
                f"{operation}: {result}"
            )

        return result

    # ------------------------------------------------------------
    # Low-level map access
    # ------------------------------------------------------------

    def _read_coil(self, address: int) -> bool:
        self._require_connected()

        try:
            result = self._client.read_coils(
                address=address,
                count=1,
                device_id=self.unit_id,
            )

        except (ModbusException, OSError) as exc:
            raise WeatherModbusResponseError(
                f"Error leyendo Coil {address}: {exc}"
            ) from exc

        result = self._require_ok(
            result,
            f"Read Coil {address}",
        )

        return bool(result.bits[0])

    def _write_coil(
        self,
        address: int,
        value: bool,
    ) -> None:
        self._require_connected()

        try:
            result = self._client.write_coil(
                address=address,
                value=bool(value),
                device_id=self.unit_id,
            )

        except (ModbusException, OSError) as exc:
            raise WeatherModbusResponseError(
                f"Error escribiendo Coil {address}: {exc}"
            ) from exc

        self._require_ok(
            result,
            f"Write Coil {address}",
        )

    def _read_holding_register(
        self,
        address: int,
    ) -> int:
        self._require_connected()

        try:
            result = self._client.read_holding_registers(
                address=address,
                count=1,
                device_id=self.unit_id,
            )

        except (ModbusException, OSError) as exc:
            raise WeatherModbusResponseError(
                f"Error leyendo Holding Register {address}: {exc}"
            ) from exc

        result = self._require_ok(
            result,
            f"Read Holding Register {address}",
        )

        if len(result.registers) != 1:
            raise WeatherModbusResponseError(
                f"Holding Register {address}: "
                f"se esperaba 1 word y llegaron "
                f"{len(result.registers)}."
            )

        return result.registers[0]

    def _write_holding_register(
        self,
        address: int,
        value: int,
    ) -> None:
        self._require_connected()

        try:
            result = self._client.write_register(
                address=address,
                value=value,
                device_id=self.unit_id,
            )

        except (ModbusException, OSError) as exc:
            raise WeatherModbusResponseError(
                f"Error escribiendo Holding Register "
                f"{address}: {exc}"
            ) from exc

        self._require_ok(
            result,
            f"Write Holding Register {address}",
        )

    # ============================================================
    # Public control interface
    # ============================================================

    def get_run_enable(self) -> bool:
        return self._read_coil(
            COIL_RUN_ENABLE
        )

    def set_run_enable(
        self,
        enabled: bool,
    ) -> None:
        self._write_coil(
            COIL_RUN_ENABLE,
            enabled,
        )

    def get_read_now_trigger(self) -> bool:
        return self._read_coil(
            COIL_READ_NOW_TRIGGER
        )

    def trigger_read_now(self) -> None:
        """
        Escribe 1 sobre Coil 102.

        P2-05 valida únicamente que el cliente puede emitir
        correctamente la escritura.

        La semántica:
        - async,
        - self-clearing,
        - sample_counter confirmation

        se implementa y valida en P2-06.
        """

        self._write_coil(
            COIL_READ_NOW_TRIGGER,
            True,
        )

    def get_sample_interval(self) -> int:
        return self._read_holding_register(
            HREG_SAMPLE_INTERVAL_S
        )

    def set_sample_interval(
        self,
        seconds: int,
    ) -> None:
        """
        Escribe Holding Register 101.

        La validación definitiva del rango y Exception 03
        pertenece al contrato del servidor/P2-06/P2-08.

        El cliente hace aquí una validación básica para no
        generar accidentalmente una escritura obviamente inválida.
        """

        if not isinstance(seconds, int):
            raise ValueError(
                "sample_interval_s debe ser int."
            )

        if not 2 <= seconds <= 3600:
            raise ValueError(
                "sample_interval_s debe estar "
                "entre 2 y 3600 segundos."
            )

        self._write_holding_register(
            HREG_SAMPLE_INTERVAL_S,
            seconds,
        )

    # ============================================================
    # Input Register block
    # ============================================================

    def read_telemetry(self) -> TelemetrySnapshot:
        """
        Lee Input Registers 200-208 en UNA transacción FC04.

        Esto conserva:
        - snapshot coherente;
        - uint32 high-word-first;
        - scaling x100.
        """

        self._require_connected()

        try:
            result = self._client.read_input_registers(
                address=IREG_TELEMETRY_START,
                count=IREG_TELEMETRY_COUNT,
                device_id=self.unit_id,
            )

        except (ModbusException, OSError) as exc:
            raise WeatherModbusResponseError(
                f"Error leyendo Input Registers "
                f"200-208: {exc}"
            ) from exc

        result = self._require_ok(
            result,
            "Read Input Registers 200-208",
        )

        registers = result.registers

        if len(registers) != IREG_TELEMETRY_COUNT:
            raise WeatherModbusResponseError(
                "Se esperaban 9 Input Registers "
                f"y llegaron {len(registers)}."
            )

        # --------------------------------------------------------
        # 200 — signed int16 x100
        # --------------------------------------------------------

        temperature_raw = decode_int16(
            registers[0]
        )

        temperature_c = (
            temperature_raw / 100.0
        )

        # --------------------------------------------------------
        # 201 — uint16 x100
        # --------------------------------------------------------

        humidity_pct = (
            registers[1] / 100.0
        )

        # --------------------------------------------------------
        # 202 / 203 enums
        # --------------------------------------------------------

        sensor_status = registers[2]
        device_state = registers[3]

        # --------------------------------------------------------
        # 204-205 uint32
        # --------------------------------------------------------

        uptime_s = decode_uint32(
            registers[4],
            registers[5],
        )

        # --------------------------------------------------------
        # 206-207 uint32
        # --------------------------------------------------------

        sample_counter = decode_uint32(
            registers[6],
            registers[7],
        )

        # --------------------------------------------------------
        # 208
        # --------------------------------------------------------

        register_map_version = (
            registers[8]
        )

        return TelemetrySnapshot(
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            sensor_status=sensor_status,
            device_state=device_state,
            uptime_s=uptime_s,
            sample_counter=sample_counter,
            register_map_version=register_map_version,
        )

    # ============================================================
    # Convenience operations
    # ============================================================

    def read_control_state(self) -> ControlState:
        """
        Lee los tres objetos de control del mapa.
        """

        return ControlState(
            run_enable=self.get_run_enable(),
            read_now_trigger=self.get_read_now_trigger(),
            sample_interval_s=self.get_sample_interval(),
        )

    def read_register_map_version(self) -> int:
        """
        Lee el snapshot completo y retorna map version.

        Por ahora aprovechamos el block read normal de 200-208.
        """

        return (
            self.read_telemetry()
            .register_map_version
        )

    def validate_register_map_version(self) -> None:
        """
        Verifica que la Pico implemente Register Map v1.0.
        """

        version = (
            self.read_register_map_version()
        )

        if version != EXPECTED_MAP_VERSION:
            raise WeatherModbusResponseError(
                "Register Map incompatible: "
                f"esperado 0x{EXPECTED_MAP_VERSION:04X}, "
                f"recibido 0x{version:04X}."
            )
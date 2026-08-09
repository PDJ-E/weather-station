"""pi_station_client.py — cliente UART interactivo + receptor de
telemetria, para correr en la Raspberry Pi 5.

(Nota de nombre: este archivo NO corre en la Pico. Vive y se ejecuta en
la Pi, y habla por UART con el firmware que si corre en la Pico
-- pico_dht11_station.py. Se renombro desde pico_station_client.py
justamente para evitar esa confusion.)

Arquitectura de hilos:
  - Hilo principal: prompt interactivo (prompt_toolkit) para mandar
    comandos (PING, START, STOP, SET_INTERVAL, READ_NOW, STATUS).
  - Hilo de recepcion (_receive_loop): lee UART sin parar, separa
    telemetria (JSON) de respuestas de comando (texto plano), valida
    la telemetria y la deja en telemetry_queue. No toca Postgres.
  - Hilo escritor (db_writer_loop, en main()): consume telemetry_queue
    y escribe en PostgreSQL. Si la escritura se cuelga o tarda, el
    unico afectado es este hilo -- la lectura UART sigue funcionando
    sin cortes.
"""

import json
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import serial
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

import experiments.dht_11.db as db


SERIAL_PORT = "/dev/ttyAMA0"
BAUD_RATE = 115200
READ_TIMEOUT_S = 0.2


@dataclass
class Telemetry:
    sequence: int
    sensor: str
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    status: str
    uptime_ms: int


class PicoStationClient:
    def __init__(self, port: str, baudrate: int) -> None:
        self.uart = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT_S,
        )

        self.running = False
        self.rx_thread: Optional[threading.Thread] = None

        # Cola thread-safe: el hilo de recepcion solo hace put() (rapido,
        # nunca bloquea por Postgres). Quien consuma la cola (el hilo
        # escritor en main()) es el unico que puede quedar lento por I/O
        # de base de datos, sin afectar la lectura UART.
        self.telemetry_queue: "queue.Queue[Optional[tuple]]" = queue.Queue()

        self.last_telemetry: Optional[Telemetry] = None
        self.last_status: Optional[str] = None
        self.last_response: Optional[str] = None

    def start(self) -> None:
        self.running = True

        self.rx_thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
        )

        self.rx_thread.start()

        print(f"[INFO] Puerto abierto: {self.uart.port}")
        print(f"[INFO] Baud rate: {self.uart.baudrate}")
        print("[INFO] Receptor UART iniciado.")

    def stop(self) -> None:
        self.running = False

        if self.rx_thread is not None:
            self.rx_thread.join(timeout=1)

        if self.uart.is_open:
            self.uart.close()

        # Sentinela: le avisa al hilo escritor que ya no va a llegar mas
        # telemetria, para que termine su bucle despues de vaciar lo
        # que quede pendiente (no se pierde nada por cerrar de golpe).
        self.telemetry_queue.put(None)

        print("[INFO] Puerto UART cerrado.")

    def send_command(self, command: str) -> None:
        line = command.strip()

        if not line:
            return

        self.uart.write((line + "\n").encode("utf-8"))
        self.uart.flush()

        print(f"[TX] {line}")

    def _receive_loop(self) -> None:
        while self.running:
            try:
                raw = self.uart.readline()

                if not raw:
                    continue

                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    print("[RX][ERROR] Mensaje con encoding inválido.")
                    continue

                if not line:
                    continue

                self._handle_message(line)

            except serial.SerialException as exc:
                print(f"[RX][SERIAL ERROR] {exc}")
                print(
                    "[FATAL] Se perdió la conexión UART. "
                    "El receptor se detuvo; reinicia el script para reconectar."
                )
                self.running = False

            except Exception as exc:
                print(f"[RX][UNEXPECTED ERROR] {exc}")

    def _handle_message(self, line: str) -> None:
        if line.startswith("{"):
            self._handle_telemetry(line)
            return

        self.last_response = line

        if line == "PONG":
            print("[RX][RESPONSE] PONG")
            return

        if line.startswith("ACK:"):
            print(f"[RX][ACK] {line}")
            return

        if line.startswith("STATUS:"):
            print(f"[RX][STATUS] {line}")
            self.last_status = line
            return

        if line.startswith("ERR:"):
            print(f"[RX][ERROR] {line}")
            return

        print(f"[RX][UNKNOWN] {line}")

    def _handle_telemetry(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[RX][JSON ERROR] {exc}")
            print(f"[RX][RAW] {line}")
            return

        required_fields = {
            "sequence",
            "sensor",
            "temperature_c",
            "humidity_pct",
            "status",
            "uptime_ms",
        }

        missing = required_fields - payload.keys()

        if missing:
            print(
                "[RX][TELEMETRY ERROR] "
                f"Campos faltantes: {sorted(missing)}"
            )
            print(f"[RX][RAW] {line}")
            return

        try:
            telemetry = Telemetry(
                sequence=int(payload["sequence"]),
                sensor=str(payload["sensor"]),
                temperature_c=(
                    None
                    if payload["temperature_c"] is None
                    else float(payload["temperature_c"])
                ),
                humidity_pct=(
                    None
                    if payload["humidity_pct"] is None
                    else float(payload["humidity_pct"])
                ),
                status=str(payload["status"]),
                uptime_ms=int(payload["uptime_ms"]),
            )

        except (TypeError, ValueError) as exc:
            print(f"[RX][TELEMETRY ERROR] Tipos inválidos: {exc}")
            print(f"[RX][RAW] {line}")
            return

        self.last_telemetry = telemetry

        print(
            "[RX][TELEMETRY] "
            f"seq={telemetry.sequence} "
            f"sensor={telemetry.sensor} "
            f"temp={telemetry.temperature_c} C "
            f"hum={telemetry.humidity_pct} % "
            f"status={telemetry.status} "
            f"uptime={telemetry.uptime_ms} ms"
        )

        # put() en una Queue sin limite de tamaño es practicamente
        # instantaneo -- esto es lo unico que el hilo de recepcion hace
        # con la telemetria. Escribirla a Postgres es trabajo de otro hilo.
        self.telemetry_queue.put((telemetry, payload))


def db_writer_loop(telemetry_queue: "queue.Queue", conn) -> None:
    """Corre en su propio hilo. Bloquearse aqui (por una insercion lenta
    o Postgres caido) no afecta la lectura UART en absoluto."""
    while True:
        item = telemetry_queue.get()

        if item is None:  # sentinela de apagado, ver PicoStationClient.stop()
            telemetry_queue.task_done()
            break

        telemetry, payload = item

        try:
            db.insert_telemetry(conn, telemetry, payload)
        except Exception as exc:
            print(f"[DB][ERROR] No se pudo guardar seq={telemetry.sequence}: {exc}")
        finally:
            telemetry_queue.task_done()


def print_help() -> None:
    print(
        """
Comandos disponibles:

  PING
  STATUS
  START
  STOP
  SET_INTERVAL <segundos>
  READ_NOW

Comandos locales de esta aplicación:

  help
  last
  exit
"""
    )


def main() -> None:
    try:
        conn = db.get_connection()
        print(f"[INFO] Conectado a PostgreSQL: {db.DB_NAME}")
    except Exception as exc:
        print(f"[DB][FATAL] No se pudo conectar a PostgreSQL: {exc}")
        return

    client = PicoStationClient(port=SERIAL_PORT, baudrate=BAUD_RATE)

    writer_thread = threading.Thread(
        target=db_writer_loop,
        args=(client.telemetry_queue, conn),
        daemon=True,
    )
    writer_thread.start()

    session = PromptSession()

    try:
        client.start()

        print_help()

        with patch_stdout():
            while client.running:
                try:
                    command = session.prompt("> ").strip()

                except EOFError:
                    break

                if not command:
                    continue

                local_command = command.lower()

                if local_command in {"exit", "quit"}:
                    break

                if local_command == "help":
                    print_help()
                    continue

                if local_command == "last":
                    if client.last_telemetry is None:
                        print("[LOCAL] No se ha recibido telemetría todavía.")
                    else:
                        print("[LOCAL] Última telemetría:", client.last_telemetry)

                    if client.last_status is not None:
                        print("[LOCAL] Último STATUS:", client.last_status)

                    if client.last_response is not None:
                        print("[LOCAL] Última respuesta:", client.last_response)

                    continue

                client.send_command(command)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupción por teclado.")

    finally:
        client.stop()
        # Espera a que el hilo escritor vacie lo que quede en la cola
        # antes de cerrar la conexion, para no perder las ultimas muestras.
        writer_thread.join(timeout=5)
        conn.close()


if __name__ == "__main__":
    main()
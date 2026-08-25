"""Cliente UART interactivo + receptor de telemetria. Corre en la
Raspberry Pi y habla con el firmware de la Pico (pico_dht11_station.py).

Hilos: prompt interactivo (main) | _receive_loop (lee UART, valida
telemetria, la encola) | db_writer_loop (consume la cola y escribe en
Postgres) | _heartbeat_loop (PING periodico + deteccion de DESCONECTADO,
P1-20 / G1-10).
"""


import json
import queue
import sys
import threading
import time
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

# --- Heartbeat / liveness (P1-20, satisface G1-10) ---
HEARTBEAT_PING_INTERVAL_S = 5              # ritmo de PING con el nodo CONECTADO/DESCONOCIDO
HEARTBEAT_PING_INTERVAL_DISCONNECTED_S = 20  # ritmo mas lento (backoff) mientras esta DESCONECTADO
LIVENESS_TIMEOUT_S = 15         # sin trafico reconocido en este tiempo -> DESCONECTADO
# Nota: la comprobacion de liveness ocurre dentro del mismo ciclo del PING,
# asi que la deteccion efectiva puede tardar entre 15 y 20s segun la fase
# del ciclo. Documentado a proposito, no es un bug -- alcanza para P1.

CONN_UNKNOWN = "DESCONOCIDO"
CONN_CONNECTED = "CONECTADO"
CONN_DISCONNECTED = "DESCONECTADO"


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
        self.heartbeat_thread: Optional[threading.Thread] = None

        # Cola thread-safe: el hilo de recepcion solo hace put() (rapido,
        # nunca bloquea por Postgres). Quien consuma la cola (el hilo
        # escritor en main()) es el unico que puede quedar lento por I/O
        # de base de datos, sin afectar la lectura UART.
        self.telemetry_queue: "queue.Queue[Optional[tuple]]" = queue.Queue()

        self.last_telemetry: Optional[Telemetry] = None
        self.last_status: Optional[str] = None
        self.last_response: Optional[str] = None

        # Estado de conexion (P1-20): distinto del "state" de la Pico.
        self.connection_state = CONN_UNKNOWN
        self.last_rx_time = time.monotonic()
        self._conn_lock = threading.Lock()

        # "Estado deseado": la ultima intencion de START/STOP/SET_INTERVAL
        # que mando el usuario. La Pico NO se acuerda de esto si pierde
        # energia (reinicia siempre en STOPPED con el intervalo default).
        # Al reconectar, se compara contra el STATUS real -- no se reenvia
        # nada si la Pico nunca perdio el estado (ver _reconcile_state()).
        self.desired_running = False
        self.desired_interval_s: Optional[int] = None

        # Se limpia antes de mandar un STATUS "interno" (de reconciliacion)
        # y lo marca _handle_message() cuando llega la respuesta -- asi
        # el hilo que reconcilia puede esperar esa respuesta puntual sin
        # bloquear al hilo de recepcion.
        self._status_ready = threading.Event()

        # Protege cualquier escritura al UART: ahora dos hilos pueden
        # transmitir (comandos del usuario + PING del heartbeat).
        self._tx_lock = threading.Lock()

        # Si es False, el heartbeat sigue mandando PING y vigilando
        # liveness igual que siempre -- solo deja de imprimir el
        # [TX] PING / [RX][RESPONSE] PONG de cada ciclo. Las alertas de
        # CONECTADO/DESCONECTADO se imprimen siempre, pase lo que pase.
        # Se controla en caliente con los comandos locales "quiet"/"verbose".
        self.heartbeat_verbose = True

    def start(self) -> None:
        self.running = True

        self.rx_thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
        )
        self.rx_thread.start()

        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
        )
        self.heartbeat_thread.start()

        print(f"[INFO] Puerto abierto: {self.uart.port}")
        print(f"[INFO] Baud rate: {self.uart.baudrate}")
        print("[INFO] Receptor UART iniciado.")
        print(f"[INFO] Heartbeat iniciado. Estado de conexión: {self.connection_state}")

    def stop(self) -> None:
        self.running = False

        if self.rx_thread is not None:
            self.rx_thread.join(timeout=1)

        if self.heartbeat_thread is not None:
            # El sleep interno puede ser el largo (backoff, nodo
            # DESCONECTADO) -- el timeout tiene que cubrir el peor caso,
            # no el ritmo normal, o este join corta antes de que el hilo
            # se entere de que self.running ya es False.
            self.heartbeat_thread.join(
                timeout=HEARTBEAT_PING_INTERVAL_DISCONNECTED_S + 1
            )

        if self.uart.is_open:
            self.uart.close()

        # Sentinela: le avisa al hilo escritor que ya no va a llegar mas
        # telemetria, para que termine su bucle despues de vaciar lo
        # que quede pendiente (no se pierde nada por cerrar de golpe).
        self.telemetry_queue.put(None)

        print("[INFO] Puerto UART cerrado.")

    def send_command(self, command: str, log: bool = True) -> None:
        """Unico punto de escritura al UART (comandos del usuario y PING
        del heartbeat pasan por aca). Si falla la transmision, se loguea
        y se marca DESCONECTADO aca mismo -- asi ningun llamador necesita
        su propio try/except ni el error se propaga sin controlar.

        log=False solo suprime la linea de exito [TX]; los errores se
        imprimen siempre. Lo usa el heartbeat para no llenar la consola
        de PING cuando self.heartbeat_verbose esta en False."""
        line = command.strip()

        if not line:
            return

        self._track_desired_state(line)

        try:
            with self._tx_lock:
                self.uart.write((line + "\n").encode("utf-8"))
                self.uart.flush()
        except serial.SerialException as exc:
            print(f"[TX][ERROR] No se pudo enviar '{line}': {exc}")
            self._mark_disconnected(f"error al transmitir '{line}'")
            return

        if log:
            print(f"[TX] {line}")

    def _track_desired_state(self, line: str) -> None:
        """Registra la intencion del ultimo comando de control que mando
        el usuario (o esta misma clase, al reconciliar). Es la referencia
        contra la que se compara el STATUS real de la Pico al reconectar."""
        parts = line.split()
        if not parts:
            return

        cmd = parts[0].upper()

        if cmd == "START":
            self.desired_running = True
        elif cmd == "STOP":
            self.desired_running = False
        elif cmd == "SET_INTERVAL" and len(parts) >= 2:
            try:
                self.desired_interval_s = int(parts[1])
            except ValueError:
                pass  # invalido; la Pico lo va a rechazar con su propio ERR

    def _mark_alive(self) -> None:
        """Se llama SOLO con trafico ya reconocido como valido: telemetria
        bien formada, PONG, ACK, STATUS o ERR. Una linea corrupta o
        desconocida NO cuenta como senal de vida."""
        reconnecting = False

        with self._conn_lock:
            self.last_rx_time = time.monotonic()
            if self.connection_state != CONN_CONNECTED:
                previous = self.connection_state
                self.connection_state = CONN_CONNECTED
                print(f"[HEARTBEAT] Nodo CONECTADO (era {previous}).")
                reconnecting = previous == CONN_DISCONNECTED

        if reconnecting:
            # En su propio hilo: si lo hicieramos aca mismo, el hilo que
            # esta corriendo esto (_receive_loop) tendria que esperar su
            # propia respuesta al STATUS que va a mandar -- se bloquearia
            # a si mismo. Un reconecte es un evento raro, no un hilo por
            # cada linea recibida.
            threading.Thread(target=self._reconcile_state, daemon=True).start()

    @staticmethod
    def _parse_status(line: Optional[str]) -> tuple:
        """Extrae (state, interval_s) de una linea 'STATUS:RUNNING,
        INTERVAL:5,UPTIME_MS:...'. Devuelve (None, None) si no matchea."""
        if line is None or not line.startswith("STATUS:"):
            return None, None

        try:
            fields = dict(part.split(":", 1) for part in line.split(","))
            state = fields.get("STATUS")
            interval = int(fields["INTERVAL"]) if "INTERVAL" in fields else None
            return state, interval
        except (ValueError, IndexError):
            return None, None

    def _reconcile_state(self) -> None:
        """Ante una reconexion, pregunta el estado REAL de la Pico (STATUS)
        y solo actua si no coincide con lo que el usuario pidio la ultima
        vez. Si la Pico nunca perdio el estado (ej. un blip de UART y el
        firmware siguio RUNNING solo), no se reenvia nada."""
        self._status_ready.clear()
        self.send_command("STATUS", log=self.heartbeat_verbose)

        if not self._status_ready.wait(timeout=2.0):
            print(
                "[HEARTBEAT][RECONCILE] Sin respuesta a STATUS tras la "
                "reconexion; no se pudo verificar el estado de la Pico."
            )
            return

        actual_state, actual_interval = self._parse_status(self.last_status)

        if actual_state is None:
            print(
                "[HEARTBEAT][RECONCILE] STATUS con formato inesperado, "
                "no se pudo verificar el estado de la Pico."
            )
            return

        if not self.desired_running:
            print(f"[HEARTBEAT][RECONCILE] Pico volvio en {actual_state}; nada que restaurar.")
            return

        if actual_state == "RUNNING" and (
            self.desired_interval_s is None or actual_interval == self.desired_interval_s
        ):
            print(f"[HEARTBEAT][RECONCILE] Pico ya esta {actual_state} como se esperaba; no se reenvia nada.")
            return

        print(
            f"[HEARTBEAT][RECONCILE] Pico volvio en {actual_state} (interval={actual_interval}) "
            f"pero deberia estar RUNNING (interval={self.desired_interval_s}) -- restaurando."
        )

        if self.desired_interval_s is not None and actual_interval != self.desired_interval_s:
            self.send_command(f"SET_INTERVAL {self.desired_interval_s}")

        self.send_command("START")

    def _mark_disconnected(self, reason: str) -> None:
        """Punto unico de transicion a DESCONECTADO: timeout de liveness,
        error de recepcion UART, o error al transmitir por UART (PING del
        heartbeat o comando del usuario). Idempotente -- no repite el
        aviso si ya estaba desconectado."""
        with self._conn_lock:
            if self.connection_state != CONN_DISCONNECTED:
                self.connection_state = CONN_DISCONNECTED
                print(f"[HEARTBEAT][ALERTA] Nodo DESCONECTADO ({reason}).")

    def _heartbeat_loop(self) -> None:
        while self.running:
            with self._conn_lock:
                disconnected = self.connection_state == CONN_DISCONNECTED

            # Backoff: mientras el nodo esta DESCONECTADO, pingueamos mas
            # espaciado -- ya sabemos que no responde, no hace falta
            # insistir cada 5s. Apenas vuelve trafico reconocido (via
            # _mark_alive) el proximo ciclo retoma el ritmo normal.
            interval = (
                HEARTBEAT_PING_INTERVAL_DISCONNECTED_S
                if disconnected
                else HEARTBEAT_PING_INTERVAL_S
            )
            time.sleep(interval)

            if not self.running:
                break

            # Sonda activa: se manda pase lo que pase, para tener señal
            # de vida incluso cuando el nodo esta STOPPED y no hay
            # telemetria fluyendo por su cuenta. Si falla, send_command
            # ya loguea y marca DESCONECTADO -- no hace falta duplicarlo aca.
            self.send_command("PING", log=self.heartbeat_verbose)

            with self._conn_lock:
                elapsed = time.monotonic() - self.last_rx_time

            if elapsed > LIVENESS_TIMEOUT_S:
                self._mark_disconnected(f"sin respuesta hace {elapsed:.1f}s")

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
                self._mark_disconnected("error de recepción UART")
                self.running = False

            except Exception as exc:
                print(f"[RX][UNEXPECTED ERROR] {exc}")

    def _handle_message(self, line: str) -> None:
        if line.startswith("{"):
            self._handle_telemetry(line)
            return

        self.last_response = line

        if line == "PONG":
            self._mark_alive()
            # No hay forma de distinguir un PONG del heartbeat de uno
            # en respuesta a un PING manual, asi que ambos respetan el
            # mismo flag -- es justo el ruido que heartbeat_verbose=False
            # busca esconder.
            if self.heartbeat_verbose:
                print("[RX][RESPONSE] PONG")
            return

        if line.startswith("ACK:"):
            self._mark_alive()
            print(f"[RX][ACK] {line}")
            return

        if line.startswith("STATUS:"):
            self._mark_alive()
            print(f"[RX][STATUS] {line}")
            self.last_status = line
            self._status_ready.set()
            return

        if line.startswith("ERR:"):
            self._mark_alive()
            print(f"[RX][ERROR] {line}")
            return

        # Linea decodificable pero no reconocida: NO cuenta como señal
        # de vida (podria ser ruido o basura en el bus).
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

        # Solo aca, con la telemetria ya validada por completo, cuenta
        # como señal de vida.
        self._mark_alive()

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
  quiet     -- oculta el [TX] PING / [RX][RESPONSE] PONG rutinario del heartbeat
  verbose   -- lo vuelve a mostrar (default)
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

                    print("[LOCAL] Estado de conexión:", client.connection_state)
                    print(
                        "[LOCAL] Estado deseado:",
                        "RUNNING" if client.desired_running else "STOPPED",
                        f"(interval={client.desired_interval_s})"
                        if client.desired_interval_s is not None
                        else "",
                    )

                    continue

                if local_command == "quiet":
                    client.heartbeat_verbose = False
                    print("[LOCAL] Logs de PING/PONG del heartbeat: OFF")
                    continue

                if local_command == "verbose":
                    client.heartbeat_verbose = True
                    print("[LOCAL] Logs de PING/PONG del heartbeat: ON")
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
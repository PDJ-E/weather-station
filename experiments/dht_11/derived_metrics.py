"""derived_metrics.py — P1-16: métricas derivadas (punto de rocío) sobre
la telemetria cruda ya almacenada.

Corre como proceso independiente de pi_dht11_station.py: solo LEE
weather_measurement_raw y escribe en weather_measurement_derived. No
toca UART ni el proceso de ingesta -- se puede arrancar y parar sin
afectar una prueba de resistencia en curso.

Al arrancar procesa todo lo pendiente que ya se haya acumulado (catch-up),
y despues sigue en un bucle simple revisando cada pocos segundos si hay
filas nuevas.
"""

import math
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    # Apunta a la carpeta que contiene este archivo (donde vive db.py),
    # sin importar qué directorio de trabajo use el lanzador (terminal
    # manual, botón Play, debugger). Ver conversación sobre el
    # comportamiento inconsistente del botón Play en VS Code.
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

import db


# Constantes clasicas de Magnus. Deliberadamente NO se usa la variante
# mas precisa (Alduchov-Eskridge): el DHT11 solo entrega grados y
# porcentajes enteros, asi que mas cifras de precision en la formula
# serian precision falsa, no real.
MAGNUS_B = 17.27
MAGNUS_C = 237.7

POLL_INTERVAL_S = 3

SELECT_PENDING_SQL = """
    SELECT r.id, r.temperature_c, r.humidity_pct
    FROM weather_measurement_raw r
    LEFT JOIN weather_measurement_derived d ON d.raw_id = r.id
    WHERE d.id IS NULL
      AND r.sensor_status = 'OK'
      AND r.temperature_c IS NOT NULL
      AND r.humidity_pct IS NOT NULL
    ORDER BY r.id
"""

INSERT_DERIVED_SQL = """
    INSERT INTO weather_measurement_derived (raw_id, dew_point_c)
    VALUES (%(raw_id)s, %(dew_point_c)s)
"""


def compute_dew_point_c(temperature_c: float, humidity_pct: float) -> float:
    """Formula de Magnus. humidity_pct debe estar en (0, 100]."""
    alpha = (
        (MAGNUS_B * temperature_c) / (MAGNUS_C + temperature_c)
        + math.log(humidity_pct / 100.0)
    )
    return (MAGNUS_C * alpha) / (MAGNUS_B - alpha)


def process_pending(conn) -> int:
    """Busca filas crudas validas sin metrica derivada todavia, calcula
    el punto de rocio y las inserta. Devuelve cuantas filas proceso.
    Un error en una fila puntual se registra y no interrumpe el resto
    (misma filosofia que la validacion de telemetria en pi_dht11_station.py)."""
    with conn.cursor() as cur:
        cur.execute(SELECT_PENDING_SQL)
        rows = cur.fetchall()

    processed = 0

    for raw_id, temperature_c, humidity_pct in rows:
        try:
            dew_point_c = compute_dew_point_c(temperature_c, humidity_pct)
        except ValueError as exc:
            print(f"[WARN] raw_id={raw_id}: no se pudo calcular punto de rocío: {exc}")
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(
                    INSERT_DERIVED_SQL,
                    {"raw_id": raw_id, "dew_point_c": dew_point_c},
                )
        except Exception as exc:
            print(f"[ERROR] raw_id={raw_id}: no se pudo guardar la métrica derivada: {exc}")
            continue

        processed += 1

    return processed


def main() -> None:
    try:
        conn = db.get_connection()
    except Exception as exc:
        print(f"[DB][FATAL] No se pudo conectar a PostgreSQL: {exc}")
        return

    print(f"[INFO] Conectado a PostgreSQL: {db.DB_NAME}")
    print("[INFO] Calculando métricas derivadas pendientes (catch-up)...")

    try:
        n = process_pending(conn)
        print(f"[INFO] Catch-up inicial: {n} fila(s) procesada(s).")

        while True:
            time.sleep(POLL_INTERVAL_S)
            n = process_pending(conn)
            if n:
                print(f"[INFO] {n} fila(s) nueva(s) procesada(s).")

    except KeyboardInterrupt:
        print("\n[INFO] Detenido.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
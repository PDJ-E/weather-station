"""db.py — persistencia de telemetria cruda en PostgreSQL (P1-15).

Todo lo que NO es secreto queda como constante fija, visible aqui mismo
(host, puerto, base de datos, usuario). Lo unico que sale del codigo es
la contraseña: se lee de una variable de entorno DB_PASSWORD, definida
en un archivo .env que vive en esta misma carpeta (ver .env.example).
Ese .env esta en .gitignore -- nunca se sube a git.

Este modulo no se ejecuta solo (no tiene "if __name__ == '__main__'").
Existe para que pi_dht11_station.py haga "import db" y use sus funciones.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


# Busca el .env junto a este archivo, sin importar desde donde se
# ejecute python3 (asi no depende del directorio de trabajo actual).
load_dotenv(Path(__file__).resolve().parent / ".env")

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "weather_station_dev"
DB_USER = "weather_station_app"

DB_PASSWORD = os.environ.get("DB_PASSWORD")

if not DB_PASSWORD:
    raise RuntimeError(
        "Falta DB_PASSWORD. Copia .env.example a .env en esta misma "
        "carpeta y completa la contraseña real de weather_station_app."
    )

# Identificador del nodo fisico que emite esta telemetria. Hoy solo hay
# un nodo DHT11 sobre la Pico; cuando existan mas nodos (P3, P5), cada
# uno necesita su propio NODE_ID para poder distinguirlos en la tabla.
NODE_ID = "pico-01-dht11"

INSERT_RAW_SQL = """
    INSERT INTO weather_measurement_raw
        (
            node_id,
            sequence,
            uptime_ms,
            sensor_type,
            temperature_c,
            humidity_pct,
            sensor_status,
            raw_payload
        )
    VALUES
        (
            %(node_id)s,
            %(sequence)s,
            %(uptime_ms)s,
            %(sensor_type)s,
            %(temperature_c)s,
            %(humidity_pct)s,
            %(sensor_status)s,
            %(raw_payload)s
        )
"""


def get_connection() -> psycopg.Connection:
    """Abre una conexion. autocommit=True porque cada insercion es
    independiente; no hay necesidad de transacciones multi-fila aqui."""
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        autocommit=True,
    )


def insert_telemetry(conn: psycopg.Connection, telemetry, raw_payload: dict) -> None:
    """Inserta una muestra de Telemetry ya validada. raw_payload es el
    dict original completo (P1-15 / ADR-004: separar crudo de derivado,
    pero el crudo debe conservar el mensaje tal cual llego)."""
    with conn.cursor() as cur:
        cur.execute(
            INSERT_RAW_SQL,
            {
                "node_id": NODE_ID,
                "sequence": telemetry.sequence,
                "uptime_ms": telemetry.uptime_ms,
                "sensor_type": telemetry.sensor,
                "temperature_c": telemetry.temperature_c,
                "humidity_pct": telemetry.humidity_pct,
                "sensor_status": telemetry.status,
                "raw_payload": Jsonb(raw_payload),
            },
        )
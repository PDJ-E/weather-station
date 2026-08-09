-- P1-15: tabla de mediciones crudas.
-- Esquema basado en weather_station_referencia_tecnica.xlsx,
-- hoja "Fase 1 - DHT11" -> "Modelo de almacenamiento propuesto".
--
-- sensor_type es TEXT deliberadamente, no un enum:
-- sensores adicionales como BME688 deben poder incorporarse
-- sin modificar el esquema de almacenamiento raw.

CREATE TABLE IF NOT EXISTS weather_measurement_raw (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),

    node_id             TEXT NOT NULL,
    sequence            BIGINT NOT NULL,
    uptime_ms           BIGINT NOT NULL,

    sensor_type         TEXT NOT NULL,
    temperature_c       DOUBLE PRECISION,
    humidity_pct        DOUBLE PRECISION,
    sensor_status       TEXT NOT NULL,

    raw_payload         JSONB NOT NULL
);

-- P1-17 / G1-08:
-- las consultas principales filtrarán por nodo y rango temporal.
CREATE INDEX IF NOT EXISTS idx_weather_measurement_raw_node_time
    ON weather_measurement_raw (node_id, received_at_utc);

-- No se crea UNIQUE(node_id, sequence).
--
-- La Pico reinicia sequence desde 1 después de cada boot, por lo que
-- (node_id, sequence) no constituye una identidad global estable.
--
-- Esta tabla funciona como un log append-only. Si posteriormente se
-- necesita deduplicación fiable, se incorporará un identificador de
-- sesión/boot y se definirá la clave correspondiente.
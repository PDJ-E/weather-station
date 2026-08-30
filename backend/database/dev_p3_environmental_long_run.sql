-- ================================================================
-- Weather Station
-- P3-06 — Environmental long-run storage
--
-- Extiende el modelo P1 sin destruir ni modificar los datos
-- históricos existentes.
--
-- Modelo:
--
-- weather_test_run
--      1
--      |
--      +---- weather_measurement_raw
--                |
--                +-- DHT11
--                +-- BME688
--
-- Para cada sample_counter de P3-06 se insertan dos filas raw:
--
--      DHT11
--      BME688
--
-- Ambas comparten:
--
--      run_id
--      node_id
--      sequence
--      uptime_ms
--
-- ================================================================


BEGIN;


-- ================================================================
-- 1. Test / experiment run
-- ================================================================

CREATE TABLE IF NOT EXISTS weather_test_run (

    id                      BIGINT GENERATED ALWAYS AS IDENTITY
                            PRIMARY KEY,

    run_name                TEXT NOT NULL UNIQUE,

    phase                   TEXT NOT NULL,

    node_id                 TEXT NOT NULL,

    started_at_utc          TIMESTAMPTZ NOT NULL DEFAULT now(),

    ended_at_utc            TIMESTAMPTZ,

    status                  TEXT NOT NULL DEFAULT 'RUNNING',

    sample_period_s         DOUBLE PRECISION NOT NULL,

    register_map_version    INTEGER NOT NULL,

    notes                   TEXT,

    CONSTRAINT chk_weather_test_run_status
        CHECK (
            status IN (
                'RUNNING',
                'COMPLETED',
                'ABORTED',
                'FAILED'
            )
        ),

    CONSTRAINT chk_weather_test_run_period
        CHECK (
            sample_period_s > 0
        )
);


-- ================================================================
-- 2. Extend existing raw table
--
-- Existing P1 rows remain valid:
-- all new columns are nullable.
-- ================================================================

ALTER TABLE weather_measurement_raw

    ADD COLUMN IF NOT EXISTS run_id
        BIGINT
        REFERENCES weather_test_run(id),

    ADD COLUMN IF NOT EXISTS pressure_hpa
        DOUBLE PRECISION,

    ADD COLUMN IF NOT EXISTS gas_resistance_ohm
        DOUBLE PRECISION,

    ADD COLUMN IF NOT EXISTS gas_valid
        BOOLEAN,

    ADD COLUMN IF NOT EXISTS heater_stable
        BOOLEAN,

    ADD COLUMN IF NOT EXISTS gas_ready
        BOOLEAN;


-- ================================================================
-- 3. Indexes
-- ================================================================

CREATE INDEX IF NOT EXISTS
    idx_weather_measurement_raw_run_sequence
ON weather_measurement_raw (
    run_id,
    sequence
);


CREATE INDEX IF NOT EXISTS
    idx_weather_measurement_raw_run_sensor_time
ON weather_measurement_raw (
    run_id,
    sensor_type,
    received_at_utc
);


-- Dentro de una ejecución concreta solo debe existir una fila
-- por sensor y sample_counter.
--
-- Los datos históricos P1 no tienen run_id, por lo que quedan fuera
-- de esta regla.

CREATE UNIQUE INDEX IF NOT EXISTS
    idx_weather_measurement_raw_run_seq_sensor_unique
ON weather_measurement_raw (
    run_id,
    sequence,
    sensor_type
)
WHERE run_id IS NOT NULL;


-- ================================================================
-- 4. Comparison view
--
-- Una fila por adquisición ambiental completa.
--
-- Permite analizar DHT11 vs BME688 directamente.
-- ================================================================

CREATE OR REPLACE VIEW weather_p3_sensor_comparison AS

SELECT

    r.id AS run_id,

    r.run_name,

    r.phase,

    d.node_id,

    d.sequence,

    d.uptime_ms,

    d.received_at_utc AS dht_received_at_utc,

    b.received_at_utc AS bme_received_at_utc,


    -- ------------------------------------------------------------
    -- DHT11
    -- ------------------------------------------------------------

    d.temperature_c
        AS dht_temperature_c,

    d.humidity_pct
        AS dht_humidity_pct,

    d.sensor_status
        AS dht_sensor_status,


    -- ------------------------------------------------------------
    -- BME688
    -- ------------------------------------------------------------

    b.temperature_c
        AS bme_temperature_c,

    b.humidity_pct
        AS bme_humidity_pct,

    b.pressure_hpa,

    b.gas_resistance_ohm,

    b.gas_valid,

    b.heater_stable,

    b.gas_ready,

    b.sensor_status
        AS bme_sensor_status,


    -- ------------------------------------------------------------
    -- Sensor-to-sensor comparison
    --
    -- Convention:
    --      DHT11 - BME688
    -- ------------------------------------------------------------

    (
        d.temperature_c
        - b.temperature_c
    ) AS temperature_diff_c,

    (
        d.humidity_pct
        - b.humidity_pct
    ) AS humidity_diff_pct


FROM weather_test_run r

JOIN weather_measurement_raw d

    ON d.run_id = r.id

    AND d.sensor_type = 'DHT11'


JOIN weather_measurement_raw b

    ON b.run_id = d.run_id

    AND b.sequence = d.sequence

    AND b.sensor_type = 'BME688';


-- ================================================================
-- 5. Permissions
--
-- El DDL debe ejecutarlo el owner de las tablas / DB.
-- El proceso de la estación seguirá usando weather_station_app.
-- ================================================================

GRANT
    SELECT,
    INSERT,
    UPDATE
ON weather_test_run
TO weather_station_app;


GRANT
    USAGE,
    SELECT
ON SEQUENCE weather_test_run_id_seq
TO weather_station_app;


GRANT
    SELECT,
    INSERT
ON weather_measurement_raw
TO weather_station_app;


GRANT
    SELECT
ON weather_p3_sensor_comparison
TO weather_station_app;


COMMIT;
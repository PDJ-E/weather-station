-- P1-16: métricas derivadas, separadas del dato crudo (ver ADR de
-- separación crudo/derivado en weather_station_referencia_tecnica.xlsx).
--
-- raw_id referencia la fila de weather_measurement_raw que originó este
-- cálculo, para trazabilidad completa: siempre se puede volver del dato
-- derivado al dato crudo que lo produjo.

CREATE TABLE IF NOT EXISTS weather_measurement_derived (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_id              BIGINT NOT NULL REFERENCES weather_measurement_raw(id),
    computed_at_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),
    dew_point_c         DOUBLE PRECISION
);

-- Evita duplicados si el módulo llegara a correr dos veces en paralelo
-- por error: cada fila cruda tiene a lo sumo una fila derivada.
CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_measurement_derived_raw_id
    ON weather_measurement_derived (raw_id);
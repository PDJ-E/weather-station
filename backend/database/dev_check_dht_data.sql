
-- Check the number of rows, first and last received_at_utc, last sequence, and last uptime_ms for the node 'pico-01-dht11'
SELECT
    count(*)              AS filas,
    min(received_at_utc)  AS primera,
    max(received_at_utc)  AS ultima,
    max(sequence)          AS ultima_sequence,
    max(uptime_ms)         AS ultimo_uptime_ms
FROM weather_measurement_raw
WHERE node_id = 'pico-01-dht11';

-- Check the average temperature and humidity for the node 'pico-01-dht11'
SELECT COUNT (*) as total_rows,
AVG (temperature_c) AS avg_temp,
AVG (humidity_pct) AS avg_humidity
FROM weather_measurement_raw
WHERE node_id = 'pico-01-dht11';

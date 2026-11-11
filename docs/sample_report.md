# Auto-Insights Report: wind_turbine_scada

_Dataset: wind_turbine_scada · 52,560 rows · narrative: rule-based (offline mode)_

## Executive summary

The dataset has 50,530 rows and 5 columns covering 2018-01-01 to 2018-12-31. 2,030 expected readings (3.86%) are missing. Capacity factor was 36.28% and the asset delivered 87.61% of its expected output.

## KPIs

| KPI | Value |
|---|---|
| mean_output | 1306.2 |
| peak_output | 3618.7 |
| data_coverage_pct | 96.28 |
| producing_intervals_pct | 78.48 |
| total_energy | 11016594.0 |
| rated_capacity | 3600.0 |
| capacity_factor_pct | 36.28 |
| performance_ratio_pct | 87.61 |
| lost_output_pct | 12.39 |
| availability_pct | 95.55 |
| underperforming_intervals | 2497 |

## Key findings

- No output although wind is above cut-in speed (downtime or curtailment) (1,805 rows, 3.43%).
- ML model flagged 157 periods where LV ActivePower (kW) stayed far below its expected value (115 while still running, 42 stoppages).
- 2,030 expected 10-minute readings are missing (3.86% of the period).
- Producing less than half of the theoretical power curve (728 rows, 1.39%).
- 2 extreme outliers in 'Wind Speed (m/s)' (more than 4.0 standard deviations from the mean).
- Negative active power (turbine consuming grid power while idle) (57 rows, 0.11%).
- LV ActivePower (kW): highest month 2018-08 (1974.69), lowest month 2018-07 (477.01).
- Wind Speed (m/s): highest month 2018-03 (9.73), lowest month 2018-07 (4.95).
- LV ActivePower (kW) and Theoretical_Power_Curve (KWh) correlate at r = 0.95.
- Wind Speed (m/s) and Theoretical_Power_Curve (KWh) correlate at r = 0.944.
- ML normal-behaviour model predicts LV ActivePower (kW) with R2 = 0.9875 (MAE 83.94) on unseen data, 31.4% more accurate than Theoretical_Power_Curve (KWh).
- Largest ML anomaly: 2018-01-22 19:10:00 to 2018-01-26 02:40:00, stoppage, output 89.7% below expected.
- Worst day (SQL): 2018-01-24, 61.7 MWh lost with 22.3 h of downtime.

## Data quality findings

| Severity | Check | Rows | Detail |
|---|---|---|---|
| high | zero_output_in_wind | 1,805 | No output although wind is above cut-in speed (downtime or curtailment) (1,805 rows, 3.43%). |
| high | ml_anomalies | 3,435 | ML model flagged 157 periods where LV ActivePower (kW) stayed far below its expected value (115 while still running, 42 stoppages). |
| medium | missing_timestamps | 2,030 | 2,030 expected 10-minute readings are missing (3.86% of the period). |
| medium | severe_underperformance | 728 | Producing less than half of the theoretical power curve (728 rows, 1.39%). |
| low | outliers_Wind Speed (m/s) | 2 | 2 extreme outliers in 'Wind Speed (m/s)' (more than 4.0 standard deviations from the mean). |
| low | negative_power | 57 | Negative active power (turbine consuming grid power while idle) (57 rows, 0.11%). |

## Cleaning steps

- Missing values: strategy=interpolate, duplicate timestamps removed=0, timestamps added=2030, cells filled=292, cells still empty=7828, rows 50530->52560
- Flagged 1805 rows as flag_downtime: `LV ActivePower (kW)` <= 0 and `Wind Speed (m/s)` >= 4
- Flagged 728 rows as flag_underperformance: `LV ActivePower (kW)` > 0 and `LV ActivePower (kW)` < 0.5 * `Theoretical_Power_Curve (KWh)` and `Theoretical_Power_Curve (KWh)` > 500
- ML normal-behaviour model trained on 36,052 healthy rows (test R2 = 0.9875, MAE = 83.94); added ml_expected and ml_residual columns.

## SQL: Monthly energy, expected energy and downtime

```sql
SELECT strftime('%Y-%m', date_time)                          AS month,
       ROUND(SUM(MAX(lv_activepower_kw, 0)) / 6 / 1000, 1)           AS energy_mwh,
       ROUND(SUM(MAX(theoretical_power_curve_kwh, 0)) / 6 / 1000, 1) AS expected_mwh,
       ROUND(100.0 * SUM(MAX(lv_activepower_kw, 0))
             / SUM(MAX(theoretical_power_curve_kwh, 0)), 1)          AS performance_pct,
       ROUND(SUM(flag_downtime) / 6.0, 1)                            AS downtime_hours,
       ROUND(AVG(wind_speed_m_s), 2)                                 AS avg_wind_ms
FROM readings
WHERE lv_activepower_kw IS NOT NULL
GROUP BY month
ORDER BY month
```

| month   |   energy_mwh |   expected_mwh |   performance_pct |   downtime_hours |   avg_wind_ms |
|:--------|-------------:|---------------:|------------------:|-----------------:|--------------:|
| 2018-01 |        843.4 |         1177   |              71.7 |            101.7 |          8.54 |
| 2018-02 |       1010.3 |         1150.9 |              87.8 |             47.3 |          8.62 |
| 2018-03 |       1452.3 |         1544.1 |              94.1 |             20.3 |          9.73 |
| 2018-04 |        591.5 |          686.7 |              86.1 |             21.7 |          5.85 |
| 2018-05 |        621.2 |          712.9 |              87.1 |              8   |          5.86 |
| 2018-06 |        704.7 |          806.5 |              87.4 |              2   |          6.33 |
| 2018-07 |        354.9 |          434.3 |              81.7 |              2.5 |          4.95 |
| 2018-08 |       1459.3 |         1639.2 |              89   |              7   |          9.33 |
| 2018-09 |        953   |         1036   |              92   |              2.3 |          7.58 |
| 2018-10 |        958.3 |         1044.4 |              91.8 |              1.2 |          7.53 |
| 2018-11 |       1194.9 |         1285.7 |              92.9 |              5.8 |          9.37 |
| 2018-12 |        872.7 |         1056.3 |              82.6 |             81   |          7.35 |

## SQL: Where energy is lost, by wind-speed band

```sql
SELECT (CAST(wind_speed_m_s / 3 AS INT) * 3) || '-'
         || (CAST(wind_speed_m_s / 3 AS INT) * 3 + 3) || ' m/s'  AS wind_band,
       COUNT(*)                                                     AS intervals,
       ROUND(SUM(MAX(theoretical_power_curve_kwh - lv_activepower_kw, 0)) / 6 / 1000, 1)                            AS lost_mwh,
       SUM(flag_downtime)                                           AS downtime_intervals
FROM readings
WHERE lv_activepower_kw IS NOT NULL AND wind_speed_m_s IS NOT NULL
GROUP BY CAST(wind_speed_m_s / 3 AS INT)
ORDER BY CAST(wind_speed_m_s / 3 AS INT)
```

| wind_band   |   intervals |   lost_mwh |   downtime_intervals |
|:------------|------------:|-----------:|---------------------:|
| 0-3 m/s     |        7772 |        0   |                    0 |
| 3-6 m/s     |       12166 |      127.9 |                  549 |
| 6-9 m/s     |       13614 |      483   |                  687 |
| 9-12 m/s    |        9372 |      774.4 |                  440 |
| 12-15 m/s   |        4900 |      172.6 |                  106 |
| 15-18 m/s   |        1802 |       38.8 |                   23 |
| 18-21 m/s   |         866 |        6.6 |                    0 |
| 21-24 m/s   |         103 |        0.4 |                    0 |
| 24-27 m/s   |           8 |        0   |                    0 |

## SQL: Top 10 days with the most lost energy

```sql
SELECT date(date_time)                          AS day,
       ROUND(AVG(wind_speed_m_s), 1)                    AS avg_wind_ms,
       ROUND(SUM(MAX(theoretical_power_curve_kwh - lv_activepower_kw, 0)) / 6 / 1000, 1)                AS lost_mwh,
       ROUND(SUM(flag_downtime) / 6.0, 1)               AS downtime_hours
FROM readings
WHERE lv_activepower_kw IS NOT NULL
GROUP BY day
ORDER BY lost_mwh DESC
LIMIT 10
```

| day        |   avg_wind_ms |   lost_mwh |   downtime_hours |
|:-----------|--------------:|-----------:|-----------------:|
| 2018-01-24 |           9.8 |       61.7 |             22.3 |
| 2018-01-25 |           9.4 |       56.5 |             24   |
| 2018-01-14 |          10.1 |       45.9 |             15.2 |
| 2018-01-23 |           9.3 |       40.1 |              4.2 |
| 2018-02-25 |           8.5 |       38.4 |              5.8 |
| 2018-01-16 |          13.3 |       36.6 |              8.8 |
| 2018-12-25 |           9.6 |       35   |              0   |
| 2018-12-05 |          11.5 |       32.8 |             13.3 |
| 2018-12-19 |           9.9 |       25.1 |              5.7 |
| 2018-02-26 |           9.4 |       22.3 |             10.5 |

## Machine learning: normal-behaviour model

- Model: HistGradientBoostingRegressor (absolute-error loss), predicts `LV ActivePower (kW)` from Wind Speed (m/s), Wind Direction (°)_sin, Wind Direction (°)_cos
- Trained on 36,052 healthy rows; chronological split: first 75% train, last 25% test
- Test R² = 0.9875, MAE = 83.94, RMSE = 147.94
- Baseline (Theoretical_Power_Curve (KWh)) MAE = 122.28: the model is 31.4% more accurate
- Anomaly rule: actual < predicted - 3.0 x 38.7, at least 6 consecutive readings; 157 events

Largest anomaly events:

| start               | end                 |   duration_hours | type             |   readings |   avg_actual |   avg_expected |   shortfall_pct |   lost_energy_kWh |
|:--------------------|:--------------------|-----------------:|:-----------------|-----------:|-------------:|---------------:|----------------:|------------------:|
| 2018-01-22 19:10:00 | 2018-01-26 02:40:00 |             79.7 | stoppage         |        478 |        219.5 |         2124.5 |            89.7 |          151764   |
| 2018-01-13 20:40:00 | 2018-01-15 05:10:00 |             32.7 | stoppage         |        196 |        287.4 |         1992.2 |            85.6 |           55690.4 |
| 2018-02-24 23:50:00 | 2018-02-26 10:20:00 |             34.7 | underperformance |        208 |        191.2 |         1654.8 |            88.4 |           50737.7 |
| 2018-12-25 05:30:00 | 2018-12-26 12:10:00 |             30.8 | underperformance |        185 |        422.1 |         1882   |            77.6 |           45013.6 |
| 2018-01-16 01:10:00 | 2018-01-16 10:30:00 |              9.5 | stoppage         |         57 |         87.1 |         3570.5 |            97.6 |           33092.6 |
| 2018-12-04 21:40:00 | 2018-12-05 13:20:00 |             15.8 | stoppage         |         95 |         15.2 |         2083.9 |            99.3 |           32754.1 |
| 2018-12-19 04:10:00 | 2018-12-20 07:50:00 |             27.8 | underperformance |        167 |        582   |         1519.5 |            61.7 |           26094.1 |
| 2018-02-23 06:00:00 | 2018-02-23 14:30:00 |              8.7 | stoppage         |         52 |         48.7 |         2049.1 |            97.6 |           17337   |
| 2018-11-30 09:40:00 | 2018-12-01 00:20:00 |             14.8 | underperformance |         89 |        970.9 |         1895.1 |            48.8 |           13708.8 |
| 2018-01-30 16:50:00 | 2018-01-30 21:10:00 |              4.5 | stoppage         |         27 |         20.7 |         2690.4 |            99.2 |           12013.8 |

## Recommendations

- Investigate the high-severity findings first; they represent lost output.
- Fix data logging for the longest gaps so KPIs are not understated.
- Use the ML anomaly events (turbine running but far below expected output) as early warnings for inspection before they become stoppages.
- Review under-performing intervals against maintenance logs.

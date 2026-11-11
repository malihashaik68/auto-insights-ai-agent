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
- 2,030 expected 10-minute readings are missing (3.86% of the period).
- Producing less than half of the theoretical power curve (728 rows, 1.39%).
- Negative active power (turbine consuming grid power while idle) (57 rows, 0.11%).
- LV ActivePower (kW): best month 2018-08 (1974.69), worst month 2018-07 (477.01).
- Wind Speed (m/s): best month 2018-03 (9.73), worst month 2018-07 (4.95).
- LV ActivePower (kW) and Theoretical_Power_Curve (KWh) correlate at r = 0.95.
- Wind Speed (m/s) and Theoretical_Power_Curve (KWh) correlate at r = 0.944.

## Data quality findings

| Severity | Check | Rows | Detail |
|---|---|---|---|
| high | zero_output_in_wind | 1,805 | No output although wind is above cut-in speed (downtime or curtailment) (1,805 rows, 3.43%). |
| medium | missing_timestamps | 2,030 | 2,030 expected 10-minute readings are missing (3.86% of the period). |
| medium | severe_underperformance | 728 | Producing less than half of the theoretical power curve (728 rows, 1.39%). |
| low | negative_power | 57 | Negative active power (turbine consuming grid power while idle) (57 rows, 0.11%). |

## Cleaning steps

- Missing values: strategy=interpolate, timestamps added=2030, cells filled=292, cells still empty=7828, rows 50530->52560
- Flagged 1805 rows as flag_downtime: `LV ActivePower (kW)` <= 0 and `Wind Speed (m/s)` >= 4
- Flagged 728 rows as flag_underperformance: `LV ActivePower (kW)` > 0 and `LV ActivePower (kW)` < 0.5 * `Theoretical_Power_Curve (KWh)` and `Theoretical_Power_Curve (KWh)` > 500

## Recommendations

- Investigate the high-severity findings first; they represent lost output.
- Fix data logging for the longest gaps so KPIs are not understated.
- Review under-performing intervals against maintenance logs.

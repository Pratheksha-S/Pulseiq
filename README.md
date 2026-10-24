# PulseIQ

PulseIQ takes raw HR, finance and engineering-task data for a fictional company (Zenith Nova Technologies, part of Aura Workforce) and turns it into numbers people can trust. The data is loaded into a local DuckDB file, checked against written data contracts, shaped into a star-schema warehouse, guarded by 14 data-quality checks, and exposed through 28 governed metric views.

All names and data are fictional and for training only.

## Run it

```
cd 05_Starter
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt

python load_to_duckdb.py        # 1. copy every CSV into schema raw
python run_pipeline.py          # 2. ETL, then warehouse, then quality checks
python test_dq_checks.py        # 3. prove each quality check can fail
python build_metrics.py         # 4. build and verify the 28 metric views
```

`run_pipeline.py` runs three steps in order: `etl.py`, `build_warehouse.py` and `dq_checks.py`. Every step rebuilds its own output, so you can run it as many times as you like and get the same counts.

On this dataset the quality gate reports **LOAD BLOCKED** and the script exits with code 1. That is expected: the data has 11 planted problems on purpose, and four of them hit blocking checks (DQ-C01, C03, C04 and C12).

If a table is missing from `raw`, `build_warehouse.py` loads it from `03_Data/PulseIQ_Dataset.xlsx` and runs the ETL again.

## What is in the database

The file is `05_Starter/pulseiq.duckdb`. It is not committed (see `.gitignore`), so rebuild it with the commands above.

| Schema | What it holds |
|---|---|
| `raw` | Exact copy of each CSV or workbook sheet. Never edited. |
| `clean` | Rows that passed the contract checks |
| `etl` | `quarantine` (failed rows with the reason in `reasons`), `summary` (rows in, passed and quarantined per table) and `column_warnings` |
| `wh` | The star-schema warehouse: 11 dimension tables and 12 fact tables, plus `orphan_check`, `orphan_rows` and `reconciliation` |
| `dq` | `results` (one line per check per run), `bad_rows` (the failing rows themselves) and `latest_results` |

Every row ends up in exactly one of `clean` or `etl.quarantine`, so rows in = passed + quarantined. The script checks this and exits with an error if it is ever untrue.

Metric views (`m01_headcount` to `m28_forecast_error`), the helper views (`ref_*`) and `metric_catalog` sit in the main schema and read from `wh`.

## Files

- `05_Starter/load_to_duckdb.py`: loads the CSVs into `raw`
- `05_Starter/etl.py`: checks the four feeds that have a contract in the `data_contracts` sheet (fact_task, fact_anomaly, fact_attrition_risk, fact_forecast), quarantines bad rows, and copies the other tables through unchanged
- `05_Starter/contracts.py`: written contracts for the other main tables too, made from the data dictionary and by profiling the data (marked "inferred")
- `05_Starter/build_warehouse.py`: builds the star schema in `wh`, then runs the orphan check, the history check and the reconciliation back to the ETL
- `05_Starter/dq_checks.py`: the 14 data-quality checks, each marked STOP (blocks the load) or WARN
- `05_Starter/test_dq_checks.py`: adds one deliberately bad row per check, on a scratch copy, to prove the check notices it
- `05_Starter/metric_views.sql` and `05_Starter/build_metrics.py`: the 28 metric views, the catalog and their checks
- `05_Starter/run_pipeline.py`: runs ETL, warehouse and quality checks in one command
- `docs/contract_notes.md`: contract notes for three feeds
- `docs/star_schema.md`: the ER diagram of the warehouse
- `docs/metric_layer_notes.md`: metric decisions and hand-checks

## How I know it works

- **ETL:** the summary table shows rows in = passed + quarantined for every table. Quarantine caught 20 rows in fact_task and 5 in fact_attrition_risk.
- **Warehouse:** every table has exactly the rows that passed the ETL, every orphan is explained (2 payroll rows for employees who do not exist, 36 forecast months in the future, 12 forecast rows for ALL), and `employee_key` points to the right version of each person in 0 wrong cases.
- **Quality checks:** `test_dq_checks.py` proves all 14 checks fail when given a bad row, and all 11 planted problems are caught.
- **Metrics:** all 28 views return data, the small-group rule holds in every view that has a minimum, and five numbers were worked out by hand and matched the views.

## A new, unexpected column in a feed

The pipeline warns and keeps going. A new column does not make the existing columns any less trustworthy, so stopping everything would punish every dashboard for something harmless. But ignoring it quietly would break the rule that nothing disappears silently, so `etl.py` prints a warning that names the column and records it in `etl.column_warnings`. The column is kept in `clean` but not validated until someone writes a contract for it. The one case where it must stop is the opposite: a column the contract needs is missing. Then the run stops with an error before anything is loaded, because I cannot validate a column that is not there.

## Known limits and next steps

- The payroll cost does not match the ledger in June 2026. It is 18,450 lower, and this is one of the planted problems. It is reported, not hidden.
- `etl.py` enforces the four contracts from the `data_contracts` sheet. The extra contracts in `contracts.py` are written down but not wired into `etl.py` yet, and the small lookup tables (dim_team, dim_sprint, dim_role, dim_client, dim_vendor, dim_account, kpi_targets, budget) have no contract.
- M28 (forecast error) shows "Awaiting actuals" because the forecasts start in October 2026 and the actuals stop in September.
- Next: a dashboard on top of the metric views.
  

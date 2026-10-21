# PulseIQ

Raw HR, finance and task data loaded into a local DuckDB file, then checked against written data contracts on the way in.

## Run it

```
cd 05_Starter
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python etl.py
```

One command (`python etl.py`) loads every CSV into `raw`, validates the tables that have contracts, and prints the count summary. Run it as many times as you like: every output table is rebuilt, so the counts come out the same.

## What is in the database (`05_Starter/pulseiq.duckdb`, not committed)

| Schema | What it holds |
|---|---|
| `raw` | Exact copy of each CSV. Never edited. |
| `clean` | Rows that passed every check |
| `quarantine` | Rows that failed, with the reason in `_reasons` |
| `audit` | `pipeline_summary` (rows in / passed / quarantined per table) and `quarantine_reasons` (rows caught per rule) |

Every row ends up in exactly one of `clean` or `quarantine`, so rows in = passed + quarantined. The script checks this and exits with an error if it ever isn't true.

## Files

- `05_Starter/etl.py`: the pipeline
- `05_Starter/contracts.py`: the written promises per column, plus cross-column and cross-table rules. Change a contract here, not in `etl.py`.
- `05_Starter/load_to_duckdb.py`: loads the CSVs into `raw`
- `docs/contract_notes.md`: contract notes for 3 tables

## A new, unexpected column in a feed

(Write this in your own words before submitting. My reasoning, for you to rework:)

Warn and keep going. A new column doesn't make the existing columns any less trustworthy, so stopping the whole pipeline would punish every dashboard for something harmless. But ignoring it silently would break the no-silent-loss rule, so the pipeline prints a warning naming the column and records it in the summary notes. The column is kept in `clean` but not validated until someone writes a contract for it. The one case where it should stop is the opposite: a column the contract *requires* going missing. Then the table is stopped, all its rows go to quarantine with the reason, and the run exits with an error.

"""
run_pipeline.py - one command for the whole pipeline.

Run (from the 05_Starter folder, with the virtual environment active):
    python run_pipeline.py

Order
1. etl.py             checks the raw tables against the contracts (clean + quarantine)
2. build_warehouse.py builds the star schema (schema wh)
3. dq_checks.py       runs the 14 quality checks and prints one summary

If raw data changes (new CSV files), run  python load_to_duckdb.py  first.
The exit code is 1 when a blocking quality check fails, so a scheduler can stop the load.
"""

import sys

import build_warehouse
import dq_checks
import etl


def main():
    print("STEP 1 of 3: contracts and quarantine (etl.py)")
    etl.main()
    print("\nSTEP 2 of 3: star-schema warehouse (build_warehouse.py)")
    build_warehouse.main()
    print("\nSTEP 3 of 3: data quality gatekeeper (dq_checks.py)")
    return dq_checks.main("wh")


if __name__ == "__main__":
    sys.exit(main())


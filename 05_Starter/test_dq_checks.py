"""
test_dq_checks.py - "testing a test" for the 14 quality checks (Task 3, step 5).

Run (from the 05_Starter folder, with the virtual environment active):
    python test_dq_checks.py

How it works
1. Copies the warehouse tables (schema wh) into a scratch schema called  dqtest.
   The real warehouse is never changed.
2. For each check it counts the bad rows BEFORE, adds ONE deliberately bad row to the copy,
   and counts again.
3. The check is proven only if the count goes UP.
4. Drops the scratch schema.

A check that never fails may be checking nothing, so each one must be seen failing.
"""

import sys
from datetime import datetime, timedelta

import duckdb

import dq_checks

DB_FILE = "pulseiq.duckdb"

# One deliberately bad row (or change) per check. {t} = scratch schema name.
# "SELECT * REPLACE (...)" copies an existing row and changes only the columns we name.
BAD_ROW = {
    "DQ-C01": ("Payroll cost for one row raised by 1000, so payroll no longer equals the ledger",
               ["UPDATE {t}.fact_payroll SET employer_cost = employer_cost + 1000 WHERE payroll_item_id = 'PI-000001'"]),
    "DQ-C02": ("A ledger entry with a debit of 100 and no credit",
               ["INSERT INTO {t}.fact_ledger SELECT * REPLACE ('JE-TEST01' AS entry_id, 'JL-TEST01' AS line_id, "
                "100.0 AS debit, 0.0 AS credit) FROM {t}.fact_ledger LIMIT 1"]),
    "DQ-C03": ("A copy of an existing payroll row with a new item id",
               ["INSERT INTO {t}.fact_payroll SELECT * REPLACE ('PI-TEST01' AS payroll_item_id) "
                "FROM {t}.fact_payroll WHERE employee_id = 'EMP-0002' LIMIT 1"]),
    "DQ-C04": ("A payroll row for employee EMP-TEST, who does not exist",
               ["INSERT INTO {t}.fact_payroll SELECT * REPLACE ('PI-TEST02' AS payroll_item_id, "
                "'EMP-TEST' AS employee_id) FROM {t}.fact_payroll LIMIT 1"]),
    "DQ-C05": ("An attendance row 60 days after an employee's exit date",
               ["INSERT INTO {t}.fact_attendance_monthly SELECT * REPLACE ('FA-TEST01' AS attendance_fact_id, "
                "'{exit_emp}' AS employee_id, DATE '{exit_plus_60}' AS period_end_date) "
                "FROM {t}.fact_attendance_monthly LIMIT 1"]),
    "DQ-C06": ("A payroll row that shows 20 LOP days when the attendance shows none",
               ["UPDATE {t}.fact_payroll SET lop_days = 20 WHERE payroll_item_id = 'PI-000001'"]),
    "DQ-C07": ("An invoice where the amount paid is 100 more than the total",
               ["INSERT INTO {t}.fact_invoice SELECT * REPLACE ('INV-TEST01' AS invoice_id, "
                "total_amount + 100 AS amount_paid) FROM {t}.fact_invoice LIMIT 1"]),
    "DQ-C08": ("An expense whose total is 50 more than amount plus GST",
               ["INSERT INTO {t}.fact_expense SELECT * REPLACE ('EXP-TEST01' AS expense_id, "
                "total_amount + 50 AS total_amount) FROM {t}.fact_expense LIMIT 1"]),
    "DQ-C09": ("A task completed before it started",
               ["INSERT INTO {t}.fact_task SELECT * REPLACE ('TASK-99999' AS task_id, "
                "DATE '2020-01-01' AS completed_date, DATE '2020-02-01' AS started_date) FROM {t}.fact_task LIMIT 1"]),
    "DQ-C10": ("A leave request that ends before it starts",
               ["INSERT INTO {t}.fact_leave SELECT * REPLACE ('LR-TEST01' AS leave_request_id, "
                "DATE '2026-01-10' AS start_date, DATE '2026-01-05' AS end_date) FROM {t}.fact_leave LIMIT 1"]),
    "DQ-C11": ("An attrition prediction dated 2020, far older than the rest of the batch",
               ["INSERT INTO {t}.fact_attrition_risk SELECT * REPLACE ('AR-TEST01' AS prediction_id, "
                "DATE '2020-01-01' AS as_of_date) FROM {t}.fact_attrition_risk LIMIT 1"]),
    "DQ-C12": ("A second version of EMP-0002 with the same dates as the first (overlap)",
               ["INSERT INTO {t}.dim_employee SELECT * REPLACE (999999 AS employee_key) "
                "FROM {t}.dim_employee WHERE employee_id = 'EMP-0002' LIMIT 1"]),
    "DQ-C13": ("The clock moved 3 days forward, so the last load is 3 days old", []),
    "DQ-C14": ("A department with only 2 people in a month",
               ["INSERT INTO {t}.fact_headcount_monthly SELECT * REPLACE ('D05' AS department_id, "
                "20300131 AS date_key, DATE '2030-01-01' AS month_start_date, 2 AS headcount_end) "
                "FROM {t}.fact_headcount_monthly LIMIT 1"]),
}


def main():
    con = duckdb.connect(DB_FILE)
    scratch = "dqtest"
    now = datetime.now()

    con.execute(f"DROP SCHEMA IF EXISTS {scratch} CASCADE")
    con.execute(f"CREATE SCHEMA {scratch}")
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'wh' AND table_type = 'BASE TABLE'"
    ).fetchall()]
    for t in tables:
        con.execute(f"CREATE TABLE {scratch}.{t} AS SELECT * FROM wh.{t}")

    # facts for the C05 test: an employee who has left
    exit_emp, exit_date = con.execute(
        f"SELECT employee_id, exit_date FROM {scratch}.dim_employee "
        "WHERE is_current = 'Y' AND exit_date IS NOT NULL ORDER BY employee_id LIMIT 1").fetchall()[0]
    fill = {"t": scratch, "exit_emp": exit_emp, "exit_plus_60": str(exit_date + timedelta(days=60))}

    line = "-" * 100
    print(f"\nTESTING THE TESTS   (scratch copy of the warehouse: schema {scratch})")
    print(line)
    print(f"{'check':8}{'before':>8}{'after':>8}  {'proof':8}the bad row I added")
    print(line)

    failures = []
    for check in dq_checks.CHECKS:
        what, statements = BAD_ROW[check["id"]]
        before, _, err1 = dq_checks.evaluate(con, check, scratch, now)
        bad_now = now
        for st in statements:
            con.execute(st.format(**fill))
        if check["id"] == "DQ-C13":
            bad_now = now + timedelta(days=3)
        after, _, err2 = dq_checks.evaluate(con, check, scratch, bad_now)

        error = err1 or err2
        proven = (error is None) and after > before
        if not proven:
            failures.append(check["id"])
        shown_before = "err" if before is None else before
        shown_after = "err" if after is None else after
        print(f"{check['id']:8}{shown_before:>8}{shown_after:>8}  {'PROVEN' if proven else 'NOT OK':8}{what}")
        if error:
            print(f"        error: {error}")

    print(line)
    con.execute(f"DROP SCHEMA {scratch} CASCADE")
    con.close()

    if failures:
        print("\nNOT PROVEN: " + ", ".join(failures) + "  (the check did not notice the bad row)")
        return 1
    print(f"\nOK: all {len(dq_checks.CHECKS)} checks failed on purpose when given a bad row. "
          "The real warehouse was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

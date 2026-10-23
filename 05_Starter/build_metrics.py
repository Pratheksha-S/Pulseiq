"""
build_metrics.py - PulseIQ Task 4: build the governed metric layer.

Run from 05_Starter with the virtual environment active:
    python build_metrics.py

What it does
1. Loads metric_dictionary.csv into the table ref_metric_dictionary.
2. Runs metric_views.sql (28 metric views + helper views + metric_catalog).
3. Checks every one of the 28 views exists and returns rows.
4. Checks the small-group rule (no group below its minimum size is returned).
5. Prints the metric_catalog (evidence) and the inputs for 5 hand-checks.
Safe to re-run: every view is CREATE OR REPLACE.
"""
import re
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB_FILE = str(HERE / "pulseiq.duckdb")
SQL_FILE = HERE / "metric_views.sql"
DICT_CSV = HERE.parent / "03_Data" / "csv" / "metric_dictionary.csv"


def show(con, title, sql):
    print(f"\n=== {title} ===")
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))
    return rows


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DICT_CSV
    con = duckdb.connect(DB_FILE)

    # 1. dictionary -> table
    con.execute(
        f"CREATE OR REPLACE TABLE ref_metric_dictionary AS "
        f"SELECT * FROM read_csv_auto('{csv_path.as_posix()}', all_varchar=true)"
    )
    n = con.execute("SELECT COUNT(*) FROM ref_metric_dictionary").fetchone()[0]
    print(f"Loaded {n} metrics from {csv_path.name}")

    # 2. run the SQL file, one statement at a time
    sql = re.sub(r"--[^\n]*", "", SQL_FILE.read_text(encoding="utf-8"))
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for s in statements:
        con.execute(s)
    print(f"Ran {len(statements)} SQL statements from {SQL_FILE.name}")

    # 3. every catalog view exists and returns rows
    print("\n=== View check: every metric view exists and returns data ===")
    catalog = con.execute("SELECT metric_id, view_name FROM metric_catalog ORDER BY 1").fetchall()
    failures = 0
    for metric_id, view in catalog:
        try:
            rows = con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
            status = "OK" if rows > 0 else "EMPTY"
        except Exception as e:  # view missing or broken
            rows, status = 0, f"ERROR {e}"
        if status != "OK":
            failures += 1
        print(f"{metric_id}  {view:32s} {rows:>6} rows  {status}")
    print(f"Views listed in catalog: {len(catalog)} (expected 28)")

    # 4. small-group rule
    print("\n=== Small-group rule: smallest group each view returns ===")
    rules = [
        ("m01_headcount", "headcount", 5),
        ("m04_attrition_monthly", "headcount_start", 10),
        ("m05_attrition_rolling_12m", "avg_headcount_12m", 10),
        ("m07_gross_pay", "employees_paid", 5),
        ("m13_attendance_rate", "employees", 5),
        ("m22_task_completion_rate", "tasks_planned", 5),
        ("m25_avg_cycle_time", "tasks_done", 5),
        ("m27_high_risk_share", "employees_scored", 10),
    ]
    for view, col, minimum in rules:
        smallest = con.execute(f"SELECT MIN({col}) FROM {view}").fetchone()[0]
        ok = smallest is not None and smallest >= minimum
        failures += 0 if ok else 1
        print(f"{view:32s} smallest {col} = {smallest}  (minimum {minimum})  {'PASS' if ok else 'FAIL'}")

    # 5a. payroll exclusions and ledger reconciliation (M09)
    show(con, "Payroll rows excluded (ghost employees and duplicates)",
         """SELECT (SELECT COUNT(*) FROM wh.fact_payroll) AS rows_in_wh,
                   (SELECT COUNT(*) FROM ref_payroll_clean) AS rows_used,
                   (SELECT COUNT(*) FROM wh.fact_payroll) - (SELECT COUNT(*) FROM ref_payroll_clean) AS rows_excluded""")
    show(con, "M09 payroll cost vs ledger accounts 5001-5005 (months that differ by more than 1 rupee)",
         """SELECT p.month_start_date, ROUND(p.cost, 2) AS payroll_cost, ROUND(l.cost, 2) AS ledger_cost,
                   ROUND(l.cost - p.cost, 2) AS difference
            FROM (SELECT month_start_date, SUM(employer_cost) AS cost FROM ref_payroll_clean GROUP BY 1) p
            JOIN (SELECT month_start_date, SUM(signed_amount_debit_positive) AS cost
                  FROM ref_ledger WHERE account_code BETWEEN 5001 AND 5005 GROUP BY 1) l
              ON l.month_start_date = p.month_start_date
            WHERE ABS(l.cost - p.cost) > 1 ORDER BY 1""")

    # 5b. attrition denominator comparison (think-for-yourself)
    show(con, "Attrition: same month, three denominators (Sep 2026)",
         "SELECT * FROM m04_denominator_compare WHERE month_start_date = DATE '2026-09-01' ORDER BY department_id")

    # 5c. catalog (evidence)
    show(con, "metric_catalog (evidence)",
         "SELECT metric_id, metric_name, view_name, source_table, grain, owner_team, minimum_group_size FROM metric_catalog")

    # 5d. five hand-checks: raw inputs next to the view value
    print("\n=== HAND-CHECKS: do the sum on a calculator, then compare with the view value ===")
    r = con.execute("SELECT exits, headcount_start FROM wh.fact_headcount_monthly "
                    "WHERE department_id='D01' AND month_start_date=DATE '2026-09-01'").fetchone()
    v = con.execute("SELECT attrition_pct FROM m04_attrition_monthly "
                    "WHERE department_id='D01' AND month_start_date=DATE '2026-09-01'").fetchone()[0]
    print(f"1. M04 attrition D01 Sep 2026: exits={r[0]}, headcount_start={r[1]}  -> {r[0]}/{r[1]}x100 = view {v}")

    r = con.execute("SELECT SUM(employer_cost), COUNT(DISTINCT employee_id) FROM wh.fact_payroll "
                    "WHERE department_id='D01' AND date_key=20250831 AND employee_key <> -1").fetchone()
    v = con.execute("SELECT cost_per_head FROM m10_cost_per_head "
                    "WHERE department_id='D01' AND month_start_date=DATE '2025-08-01'").fetchone()[0]
    print(f"2. M10 cost per head D01 Aug 2025: employer_cost={round(r[0], 2)}, people={r[1]}  -> {round(r[0], 2)}/{r[1]} = view {v}")

    r = con.execute("SELECT SUM(present_days), SUM(half_days_unpaid), SUM(scheduled_days) FROM wh.fact_attendance_monthly "
                    "WHERE department_id='D01' AND date_key=20250831").fetchone()
    v = con.execute("SELECT attendance_rate FROM m13_attendance_rate "
                    "WHERE department_id='D01' AND month_start_date=DATE '2025-08-01'").fetchone()[0]
    print(f"3. M13 attendance D01 Aug 2025: present={r[0]}, half days={r[1]}, scheduled={r[2]}  -> ({r[0]}+0.5x{r[1]})/{r[2]} = view {v}")

    r = con.execute("SELECT SUM(CASE WHEN status='Done' THEN 1 ELSE 0 END), SUM(CASE WHEN status<>'Cancelled' THEN 1 ELSE 0 END) "
                    "FROM wh.fact_task WHERE team_id='T02' AND sprint_id='SP-03'").fetchone()
    v = con.execute("SELECT completion_rate_pct FROM m22_task_completion_rate "
                    "WHERE team_id='T02' AND sprint_id='SP-03'").fetchone()[0]
    print(f"4. M22 completion T02 SP-03: done={r[0]}, not cancelled={r[1]}  -> {r[0]}/{r[1]}x100 = view {v}")

    r = con.execute("SELECT -SUM(CASE WHEN account_code BETWEEN 4001 AND 4004 THEN signed_amount_debit_positive ELSE 0 END), "
                    "-SUM(CASE WHEN account_code BETWEEN 4001 AND 5999 THEN signed_amount_debit_positive ELSE 0 END) "
                    "FROM ref_ledger WHERE month_start_date=DATE '2026-09-01'").fetchone()
    v = con.execute("SELECT operating_margin_pct FROM m18_operating_margin "
                    "WHERE month_start_date=DATE '2026-09-01'").fetchone()[0]
    print(f"5. M18 margin Sep 2026: revenue={round(r[0], 2)}, net profit={round(r[1], 2)}  -> {round(r[1], 2)}/{round(r[0], 2)}x100 = view {v}")

    print("\nRESULT:", "ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) NEED ATTENTION")
    con.close()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

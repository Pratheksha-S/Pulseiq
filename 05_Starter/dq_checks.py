"""
dq_checks.py - PulseIQ Task 3: the data quality gatekeeper.

Run (from the 05_Starter folder, with the virtual environment active):
    python dq_checks.py          # checks the warehouse (schema wh)
    python dq_checks.py raw      # checks the raw tables (what arrived)

What it does
- Runs the 14 checks from dq_checks_catalog. Each check is a SQL query that returns the BAD rows.
- Writes one line per check to  dq.results  (check, table, bad rows, status, run time).
- Keeps the bad rows themselves in  dq.bad_rows  so anyone can look at them.
- Prints one summary a manager can read.
- Exits with code 1 if a check marked STOP fails, so a scheduler can stop the load.

Statuses
    PASS   no bad rows
    FAIL   bad rows found in a STOP check (the load is blocked)
    WARN   bad rows found in a WARN check (the load goes on, but someone must look)
    ERROR  the check could not run (treated as a failure: a check that cannot run proves nothing)
"""

import sys
from datetime import datetime

import duckdb

HERE_DB = "pulseiq.duckdb"

# ---------------------------------------------------------------------------
# THE 14 CHECKS
#   sql    : returns the bad rows. {s} = schema being checked, {now} = time of the run
#   action : STOP = a failure blocks the load.  WARN = a failure is reported, load goes on.
#            Change an action here to change the rule. Write your reason in the notes.
# ---------------------------------------------------------------------------

CHECKS = [
    {
        "id": "DQ-C01", "name": "Payroll equals ledger", "tables": "fact_payroll, fact_ledger",
        "severity": "Critical", "action": "STOP", "owner": "Finance Analytics",
        "fix": "Check the payroll posting entry for that run",
        "sql": """
            WITH p AS (SELECT strftime(TRY_CAST(period_end_date AS DATE), '%Y-%m') AS month,
                              SUM(employer_cost) AS payroll_cost
                       FROM {s}.fact_payroll GROUP BY 1),
                 l AS (SELECT posting_period AS month, SUM(debit - credit) AS ledger_expense
                       FROM {s}.fact_ledger WHERE account_code BETWEEN 5001 AND 5005 GROUP BY 1)
            SELECT COALESCE(p.month, l.month) AS month,
                   ROUND(COALESCE(p.payroll_cost, 0), 2) AS payroll_cost,
                   ROUND(COALESCE(l.ledger_expense, 0), 2) AS ledger_expense,
                   ROUND(COALESCE(p.payroll_cost, 0) - COALESCE(l.ledger_expense, 0), 2) AS diff
            FROM p FULL JOIN l ON p.month = l.month
            WHERE ABS(ROUND(COALESCE(p.payroll_cost, 0) - COALESCE(l.ledger_expense, 0), 2)) > 0.01
            ORDER BY 1
        """,
    },
    {
        "id": "DQ-C02", "name": "Ledger balanced", "tables": "fact_ledger",
        "severity": "Critical", "action": "STOP", "owner": "Finance Analytics",
        "fix": "Reject the entry and alert the ledger owner",
        "sql": """
            SELECT entry_id, ROUND(SUM(debit), 2) AS total_debit, ROUND(SUM(credit), 2) AS total_credit
            FROM {s}.fact_ledger
            GROUP BY entry_id
            HAVING ABS(SUM(debit) - SUM(credit)) > 0.005
        """,
    },
    {
        "id": "DQ-C03", "name": "Unique payroll rows", "tables": "fact_payroll",
        "severity": "Critical", "action": "STOP", "owner": "Payroll Analytics",
        "fix": "Remove duplicate and trace its source",
        "sql": """
            SELECT employee_id, run_id, COUNT(*) AS rows_found
            FROM {s}.fact_payroll
            GROUP BY employee_id, run_id
            HAVING COUNT(*) > 1
        """,
    },
    {
        "id": "DQ-C04", "name": "No orphan employees", "tables": "6 fact tables",
        "severity": "Critical", "action": "STOP", "owner": "Data Engineering",
        "fix": "Fix the key or load the missing employee",
        "sql": """
            SELECT 'fact_payroll' AS source_table, employee_id FROM {s}.fact_payroll
              WHERE employee_id IS NULL OR employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
            UNION ALL
            SELECT 'fact_attendance_monthly', employee_id FROM {s}.fact_attendance_monthly
              WHERE employee_id IS NULL OR employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
            UNION ALL
            SELECT 'fact_leave', employee_id FROM {s}.fact_leave
              WHERE employee_id IS NULL OR employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
            UNION ALL
            SELECT 'fact_task', assignee_employee_id FROM {s}.fact_task
              WHERE assignee_employee_id IS NULL OR assignee_employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
            UNION ALL
            SELECT 'fact_anomaly', employee_id FROM {s}.fact_anomaly
              WHERE employee_id IS NULL OR employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
            UNION ALL
            SELECT 'fact_attrition_risk', employee_id FROM {s}.fact_attrition_risk
              WHERE employee_id IS NULL OR employee_id NOT IN (SELECT employee_id FROM {s}.dim_employee)
        """,
    },
    {
        "id": "DQ-C05", "name": "No attendance after exit", "tables": "fact_attendance_monthly, dim_employee",
        "severity": "Warning", "action": "WARN", "owner": "HR Analytics",
        "fix": "Ask HR to confirm exit date or remove the row",
        "sql": """
            SELECT a.attendance_fact_id, a.employee_id, a.period_end_date, d.exit_date
            FROM {s}.fact_attendance_monthly a
            JOIN {s}.dim_employee d ON a.employee_id = d.employee_id AND d.is_current = 'Y'
            WHERE TRY_CAST(d.exit_date AS DATE) IS NOT NULL
              AND TRY_CAST(a.period_end_date AS DATE) > last_day(TRY_CAST(d.exit_date AS DATE))
        """,
    },
    {
        "id": "DQ-C06", "name": "Unpaid leave deducted", "tables": "fact_attendance_monthly, fact_payroll",
        "severity": "Warning", "action": "WARN", "owner": "Payroll Analytics",
        "fix": "Review unpaid-leave mapping",
        # unpaid days = full unpaid days + half of the unpaid half days (tolerance 0.5 day)
        "sql": """
            SELECT a.employee_id, a.period_end_date, a.unpaid_absence_days, a.half_days_unpaid,
                   p.lop_days AS payroll_lop_days
            FROM {s}.fact_attendance_monthly a
            LEFT JOIN {s}.fact_payroll p
              ON a.employee_id = p.employee_id
             AND date_trunc('month', TRY_CAST(a.period_end_date AS DATE)) = date_trunc('month', TRY_CAST(p.period_end_date AS DATE))
            WHERE ABS(a.unpaid_absence_days + 0.5 * a.half_days_unpaid - COALESCE(p.lop_days, 0)) > 0.5
        """,
    },
    {
        "id": "DQ-C07", "name": "Invoice arithmetic", "tables": "fact_invoice",
        "severity": "Warning", "action": "WARN", "owner": "Finance Analytics",
        "fix": "Correct the payment record",
        "sql": """
            SELECT invoice_id, total_amount, amount_paid, balance_due
            FROM {s}.fact_invoice
            WHERE amount_paid > total_amount + 0.01 OR balance_due < -0.01
        """,
    },
    {
        "id": "DQ-C08", "name": "Expense arithmetic", "tables": "fact_expense",
        "severity": "Warning", "action": "WARN", "owner": "Finance Analytics",
        "fix": "Correct the bill entry",
        "sql": """
            SELECT expense_id, amount_excl_gst, gst_amount, total_amount
            FROM {s}.fact_expense
            WHERE ABS(total_amount - (amount_excl_gst + gst_amount)) > 0.01
        """,
    },
    {
        "id": "DQ-C09", "name": "Task dates sensible", "tables": "fact_task",
        "severity": "Warning", "action": "WARN", "owner": "Engineering Ops",
        "fix": "Ask the team to fix the board data",
        "sql": """
            SELECT task_id, status, started_date, completed_date
            FROM {s}.fact_task
            WHERE TRY_CAST(completed_date AS DATE) < TRY_CAST(started_date AS DATE)
               OR (status = 'Done' AND TRY_CAST(completed_date AS DATE) IS NULL)
        """,
    },
    {
        "id": "DQ-C10", "name": "Leave dates sensible", "tables": "fact_leave",
        "severity": "Warning", "action": "WARN", "owner": "HR Analytics",
        "fix": "Fix swapped dates",
        "sql": """
            SELECT leave_request_id, employee_id, start_date, end_date
            FROM {s}.fact_leave
            WHERE TRY_CAST(end_date AS DATE) < TRY_CAST(start_date AS DATE)
        """,
    },
    {
        "id": "DQ-C11", "name": "AI feeds are fresh", "tables": "fact_attrition_risk, fact_anomaly",
        "severity": "Warning", "action": "WARN", "owner": "AI/ML",
        "fix": "Ask the AI team to rerun scoring",
        "sql": """
            SELECT 'fact_attrition_risk' AS feed, prediction_id AS record_id, CAST(as_of_date AS VARCHAR) AS problem_value
            FROM {s}.fact_attrition_risk
            WHERE TRY_CAST(as_of_date AS DATE) < (SELECT MAX(TRY_CAST(as_of_date AS DATE)) FROM {s}.fact_attrition_risk) - 7
            UNION ALL
            SELECT 'fact_anomaly', result_id, CAST(scored_at AS VARCHAR)
            FROM {s}.fact_anomaly
            WHERE TRY_CAST(scored_at AS TIMESTAMP) < (SELECT MAX(TRY_CAST(scored_at AS TIMESTAMP)) FROM {s}.fact_anomaly) - INTERVAL 24 HOUR
        """,
    },
    {
        "id": "DQ-C12", "name": "Dimension history does not overlap", "tables": "dim_employee",
        "severity": "Critical", "action": "STOP", "owner": "Data Engineering",
        "fix": "Close the earlier version the day before the next one starts",
        "sql": """
            SELECT a.employee_id, a.employee_key AS earlier_key, b.employee_key AS later_key,
                   a.valid_to AS earlier_valid_to, b.valid_from AS later_valid_from
            FROM {s}.dim_employee a
            JOIN {s}.dim_employee b
              ON a.employee_id = b.employee_id
             AND a.employee_key < b.employee_key
             AND TRY_CAST(a.valid_to AS DATE) >= TRY_CAST(b.valid_from AS DATE)
        """,
    },
    {
        "id": "DQ-C13", "name": "Pipeline freshness", "tables": "etl.summary",
        "severity": "Critical", "action": "STOP", "owner": "Data Engineering",
        "fix": "Check scheduler and source availability",
        # latest successful load = the latest run time written by etl.py
        "sql": """
            SELECT MAX(run_at) AS last_load, TIMESTAMP '{now}' AS checked_at
            FROM etl.summary
            HAVING MAX(run_at) IS NULL OR MAX(run_at) < TIMESTAMP '{now}' - INTERVAL 24 HOUR
        """,
    },
    {
        "id": "DQ-C14", "name": "Minimum group size", "tables": "fact_headcount_monthly, metric_dictionary",
        "severity": "Critical", "action": "WARN", "owner": "Security / Analytics",
        "fix": "Suppress the cell or combine groups",
        # cells (department x month) with fewer people than the smallest minimum_group_size in the metric dictionary
        "sql": """
            SELECT department_id, month_start_date, headcount_end,
                   (SELECT MIN(TRY_CAST(minimum_group_size AS INTEGER)) FROM clean.metric_dictionary) AS minimum_group_size
            FROM {s}.fact_headcount_monthly
            WHERE headcount_end < (SELECT MIN(TRY_CAST(minimum_group_size AS INTEGER)) FROM clean.metric_dictionary)
        """,
    },
]


# ---------------------------------------------------------------------------
# RUNNING A CHECK
# ---------------------------------------------------------------------------

def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def evaluate(con, check, schema, now):
    """Run one check. Returns (bad_rows, examples, error). bad_rows is None if the check could not run."""
    sql = check["sql"].format(s=schema, now=stamp(now))
    try:
        bad = con.execute(f"SELECT count(*) FROM ({sql}) b").fetchall()[0][0]
        examples = []
        if bad:
            rows = con.execute(f"SELECT * FROM ({sql}) b LIMIT 3").fetchall()
            examples = [str(r[0]) if len(r) == 1 else " / ".join(str(v) for v in r[:2]) for r in rows]
        return bad, examples, None
    except Exception as e:  # a check that cannot run must never look like a pass
        return None, [], str(e).splitlines()[0]


def status_of(check, bad_rows):
    if bad_rows is None:
        return "ERROR"
    if bad_rows == 0:
        return "PASS"
    return "FAIL" if check["action"] == "STOP" else "WARN"


def save_results(con, schema, now, results):
    con.execute("CREATE SCHEMA IF NOT EXISTS dq")
    con.execute("""
        CREATE TABLE IF NOT EXISTS dq.results (
            run_id VARCHAR, run_at TIMESTAMP, checked_schema VARCHAR,
            check_id VARCHAR, check_name VARCHAR, table_name VARCHAR,
            severity VARCHAR, gate_action VARCHAR,
            bad_rows BIGINT, status VARCHAR, detail VARCHAR)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS dq.bad_rows (
            run_id VARCHAR, checked_schema VARCHAR, check_id VARCHAR, row_data JSON)
    """)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    con.execute("DELETE FROM dq.bad_rows WHERE checked_schema = ?", [schema])
    for check, bad, examples, error, status in results:
        detail = error if error else ("; ".join(examples) if examples else "")
        con.execute("INSERT INTO dq.results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, now, schema, check["id"], check["name"], check["tables"],
                     check["severity"], check["action"], bad, status, detail])
        if bad:
            sql = check["sql"].format(s=schema, now=stamp(now))
            con.execute(f"INSERT INTO dq.bad_rows SELECT '{run_id}', '{schema}', '{check['id']}', to_json(b) "
                        f"FROM ({sql}) b LIMIT 1000")
    con.execute("""
        CREATE OR REPLACE VIEW dq.latest_results AS
        SELECT * FROM dq.results
        WHERE run_id = (SELECT run_id FROM dq.results ORDER BY run_at DESC LIMIT 1)
    """)
    return run_id


def main(schema="wh"):
    con = duckdb.connect(HERE_DB)
    now = datetime.now()

    results = []
    for check in CHECKS:
        bad, examples, error = evaluate(con, check, schema, now)
        results.append((check, bad, examples, error, status_of(check, bad)))
    run_id = save_results(con, schema, now, results)
    con.close()

    blocked = [r for r in results if r[0]["action"] == "STOP" and r[4] in ("FAIL", "ERROR")]
    warned = [r for r in results if r[4] == "WARN" or (r[4] == "ERROR" and r[0]["action"] != "STOP")]
    passed = [r for r in results if r[4] == "PASS"]

    line = "=" * 92
    print()
    print(f"DATA QUALITY GATEKEEPER   layer: {schema}   run: {stamp(now)}")
    print(line)
    if blocked:
        print(f"RESULT: LOAD BLOCKED - {len(blocked)} critical check(s) failed: "
              + ", ".join(r[0]["id"] for r in blocked))
    else:
        print("RESULT: LOAD OK - no blocking check failed")
    print(f"        {len(passed)} passed, {len(blocked)} failed (blocking), {len(warned)} warning(s)")
    print(line)
    print(f"{'check':8}{'what it checks':36}{'bad rows':>10}  {'status':8}{'if it fails':12}severity")
    print("-" * 92)
    for check, bad, examples, error, status in results:
        shown = "n/a" if bad is None else bad
        print(f"{check['id']:8}{check['name']:36}{shown:>10}  {status:8}"
              f"{('stops load' if check['action'] == 'STOP' else 'warns only'):12}{check['severity']}")
    print("-" * 92)

    problems = [r for r in results if r[4] != "PASS"]
    if problems:
        print("\nWHAT NEEDS ATTENTION")
        for check, bad, examples, error, status in problems:
            print(f"\n{check['id']} {check['name']}  [{status}]  owner: {check['owner']}")
            if error:
                print(f"   The check could not run: {error}")
            else:
                print(f"   {bad} bad row(s) in {check['tables']}.  First ones: {'; '.join(examples)}")
            print(f"   Fix: {check['fix']}")
    print(f"\nSaved in dq.results (run {run_id}). The bad rows themselves are in dq.bad_rows.")
    return 1 if blocked else 0


if __name__ == "__main__":
    layer = sys.argv[1] if len(sys.argv) > 1 else "wh"
    sys.exit(main(layer))

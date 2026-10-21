"""
etl.py - PulseIQ Task 1: validate every feed against its data contract.

Run (from the 05_Starter folder, with the virtual environment active):
    python etl.py

What it does
1. Reads every table in schema  raw   (loaded by load_to_duckdb.py). Raw is never changed.
2. For the 4 feeds that have a contract (fact_task, fact_anomaly,
   fact_attrition_risk, fact_forecast) it checks every column.
3. Rows that fail go to  etl.quarantine  with a written reason.
   Rows that pass go to  clean.<table>.
4. Tables with no contract are copied to clean.<table> unchanged (all rows pass).
5. etl.summary holds: rows in, rows passed, rows quarantined, for every table.

Safe to re-run: every run rebuilds etl.* and clean.* from raw, so running it
twice gives exactly the same result.
"""

import sys
from datetime import datetime

import duckdb

DB_FILE = "pulseiq.duckdb"

# ---------------------------------------------------------------------------
# 1. THE CONTRACTS  (copied from the data_contracts sheet)
#    Each column: (name, type, can_be_empty)
#    type is one of: str, int, date, dec, ts
# ---------------------------------------------------------------------------

CONTRACTS = {
    "fact_task": {
        "id_column": "task_id",
        "columns": [
            ("task_id", "str", False),
            ("team_id", "str", False),
            ("sprint_id", "str", False),
            ("assignee_employee_id", "str", False),
            ("task_type", "str", False),
            ("priority", "str", False),
            ("story_points", "int", False),
            ("created_date", "date", False),
            ("started_date", "date", True),
            ("due_date", "date", False),
            ("completed_date", "date", True),
            ("status", "str", False),
            ("reopened_count", "int", False),
        ],
        # extra columns joined in so rules can use them
        "extra_select": "CAST(sp.end_date AS DATE) AS sprint_end_date",
        "joins": "LEFT JOIN raw.dim_sprint sp ON sp.sprint_id = trim(CAST(r.sprint_id AS VARCHAR))",
        "rules": [
            ("task_id: not format TASK-nnnnn", "task_id IS NOT NULL AND NOT regexp_matches(task_id, '^TASK-[0-9]{5}$')"),
            ("task_id: duplicate", "task_id IS NOT NULL AND count(*) OVER (PARTITION BY task_id) > 1"),
            ("team_id: not found in dim_team", "team_id NOT IN (SELECT CAST(team_id AS VARCHAR) FROM raw.dim_team)"),
            ("sprint_id: not found in dim_sprint", "sprint_id NOT IN (SELECT CAST(sprint_id AS VARCHAR) FROM raw.dim_sprint)"),
            ("assignee_employee_id: not found in dim_employee", "assignee_employee_id NOT IN (SELECT CAST(employee_id AS VARCHAR) FROM raw.dim_employee)"),
            ("task_type: not Feature/Bug/Chore/Spike", "task_type NOT IN ('Feature','Bug','Chore','Spike')"),
            ("priority: not P1 to P4", "priority NOT IN ('P1','P2','P3','P4')"),
            ("story_points: not 1,2,3,5,8,13", "story_points NOT IN (1,2,3,5,8,13)"),
            ("created_date: after started_date", "created_date > started_date"),
            ("started_date: empty but status is not To Do", "started_date_text IS NULL AND status <> 'To Do'"),
            ("due_date: not equal to sprint end date", "due_date <> sprint_end_date"),
            ("completed_date: empty but status is Done", "completed_date_text IS NULL AND status = 'Done'"),
            ("completed_date: before started_date", "completed_date < started_date"),
            ("status: not an allowed status", "status NOT IN ('Done','In Progress','Blocked','To Do','Cancelled')"),
            ("reopened_count: below 0", "reopened_count < 0"),
        ],
    },
    "fact_anomaly": {
        "id_column": "result_id",
        "columns": [
            ("result_id", "str", False),
            ("source_dataset", "str", False),
            ("record_id", "str", False),
            ("employee_id", "str", False),
            ("period", "date", False),
            ("anomaly_score", "dec", False),
            ("flagged", "str", False),
            ("reason_code", "str", False),
            ("reason_text", "str", False),
            ("money_at_risk", "dec", True),
            ("model_version", "str", False),
            ("scored_at", "ts", False),
        ],
        "extra_select": "",
        "joins": "",
        "rules": [
            ("result_id: not format AN-nnnn", "result_id IS NOT NULL AND NOT regexp_matches(result_id, '^AN-[0-9]{4}$')"),
            ("result_id: duplicate", "result_id IS NOT NULL AND count(*) OVER (PARTITION BY result_id) > 1"),
            ("source_dataset: not an allowed dataset", "source_dataset NOT IN ('attendance_monthly_features','payroll_monthly_features')"),
            # The source feature tables are not in the workbook, so we can only check the id shape.
            ("record_id: unexpected format", "record_id IS NOT NULL AND NOT regexp_matches(record_id, '^[A-Z]{2}-[0-9]+$')"),
            ("employee_id: not found in dim_employee", "employee_id NOT IN (SELECT CAST(employee_id AS VARCHAR) FROM raw.dim_employee)"),
            ("period: not a month start or month end", "period <> date_trunc('month', period) AND period <> last_day(period)"),
            ("anomaly_score: outside 0 to 1", "anomaly_score < 0 OR anomaly_score > 1"),
            ("flagged: not Y or N", "flagged NOT IN ('Y','N')"),
            ("money_at_risk: should be blank for attendance rows", "source_dataset = 'attendance_monthly_features' AND money_at_risk_text IS NOT NULL"),
            ("model_version: no version number", "model_version IS NOT NULL AND NOT regexp_matches(model_version, '[0-9]+[.][0-9]+')"),
        ],
    },
    "fact_attrition_risk": {
        "id_column": "prediction_id",
        "columns": [
            ("prediction_id", "str", False),
            ("employee_id", "str", False),
            ("department_id", "str", False),
            ("as_of_date", "date", False),
            ("risk_score_12m", "dec", False),
            ("risk_band", "str", False),
            ("top_reason_1", "str", True),
            ("top_reason_2", "str", True),
            ("top_reason_3", "str", True),
            ("model_version", "str", False),
        ],
        "extra_select": "",
        "joins": "",
        "rules": [
            ("prediction_id: not format AR-nnnn", "prediction_id IS NOT NULL AND NOT regexp_matches(prediction_id, '^AR-[0-9]{4}$')"),
            ("prediction_id: duplicate", "prediction_id IS NOT NULL AND count(*) OVER (PARTITION BY prediction_id) > 1"),
            ("employee_id: not found in dim_employee", "employee_id NOT IN (SELECT CAST(employee_id AS VARCHAR) FROM raw.dim_employee)"),
            ("employee_id: employee is not active",
             "employee_id IN (SELECT CAST(employee_id AS VARCHAR) FROM raw.dim_employee) "
             "AND employee_id NOT IN (SELECT CAST(employee_id AS VARCHAR) FROM raw.dim_employee "
             "WHERE is_current = 'Y' AND employment_status = 'Active')"),
            ("department_id: not a known department", "department_id NOT IN (SELECT DISTINCT CAST(department_id AS VARCHAR) FROM raw.dim_employee)"),
            ("as_of_date: differs from the rest of the batch",
             "as_of_date IS NOT NULL AND as_of_date <> (SELECT mode(TRY_CAST(CAST(as_of_date AS VARCHAR) AS DATE)) FROM src)"),
            ("risk_score_12m: outside 0 to 1", "risk_score_12m < 0 OR risk_score_12m > 1"),
            ("risk_band: not Low/Medium/High", "risk_band NOT IN ('Low','Medium','High')"),
        ],
    },
    "fact_forecast": {
        "id_column": "forecast_id",
        "columns": [
            ("forecast_id", "str", False),
            ("metric", "str", False),
            ("department_id", "str", False),
            ("forecast_month", "date", False),
            ("forecast_value", "dec", False),
            ("lower_80", "dec", False),
            ("upper_80", "dec", False),
            ("method", "str", False),
            ("trained_through", "str", False),
        ],
        "extra_select": "",
        "joins": "",
        "rules": [
            ("forecast_id: not format FC-nnnn", "forecast_id IS NOT NULL AND NOT regexp_matches(forecast_id, '^FC-[0-9]{4}$')"),
            ("forecast_id: duplicate", "forecast_id IS NOT NULL AND count(*) OVER (PARTITION BY forecast_id) > 1"),
            ("metric: not headcount_end or attrition_rate_monthly_pct", "metric NOT IN ('headcount_end','attrition_rate_monthly_pct')"),
            ("department_id: not ALL and not a known department",
             "department_id <> 'ALL' AND department_id NOT IN (SELECT DISTINCT CAST(department_id AS VARCHAR) FROM raw.dim_employee)"),
            ("forecast_month: not the first day of a month", "forecast_month <> date_trunc('month', forecast_month)"),
            ("forecast_value: below 0", "forecast_value < 0"),
            ("lower_80: above forecast_value", "lower_80 > forecast_value"),
            ("upper_80: below forecast_value", "upper_80 < forecast_value"),
            ("trained_through: not format YYYY-MM", "trained_through IS NOT NULL AND NOT regexp_matches(trained_through, '^[0-9]{4}-(0[1-9]|1[0-2])$')"),
        ],
    },
}

SQL_TYPE = {"str": "VARCHAR", "int": "BIGINT", "date": "DATE", "dec": "DOUBLE", "ts": "TIMESTAMP"}
TYPE_NAME = {"str": "text", "int": "whole number", "date": "date", "dec": "number", "ts": "timestamp"}


# ---------------------------------------------------------------------------
# 2. HELPERS
# ---------------------------------------------------------------------------

def q(name):
    """Quote a column name."""
    return '"' + name + '"'


def staging_select(spec):
    """
    Build the SELECT that turns every contract column into:
      <col>_text : the value as trimmed text, NULL when blank
      <col>      : the value converted to its contract type, NULL when blank or invalid
    """
    parts = ["r.raw_row_num"]
    for name, typ, _ in spec["columns"]:
        text = f"NULLIF(trim(CAST(r.{q(name)} AS VARCHAR)), '')"
        parts.append(f"{text} AS {q(name + '_text')}")
        if typ == "str":
            parts.append(f"{text} AS {q(name)}")
        else:
            parts.append(f"TRY_CAST({text} AS {SQL_TYPE[typ]}) AS {q(name)}")
    if spec["extra_select"]:
        parts.append(spec["extra_select"])
    return ",\n    ".join(parts)


def check_sql(spec):
    """Build the list of 'reason' expressions: generic checks first, then the contract rules."""
    reasons = []
    for name, typ, can_be_empty in spec["columns"]:
        if not can_be_empty:
            reasons.append(f"CASE WHEN {q(name + '_text')} IS NULL THEN '{name}: is empty' END")
        if typ != "str":
            reasons.append(
                f"CASE WHEN {q(name + '_text')} IS NOT NULL AND {q(name)} IS NULL "
                f"THEN '{name}: not a valid {TYPE_NAME[typ]}' END"
            )
    for reason, condition in spec["rules"]:
        reasons.append(f"CASE WHEN {condition} THEN '{reason}' END")
    return reasons


def run_contract_table(con, table, spec):
    """Validate one table. Returns (rows_in, rows_passed, rows_quarantined)."""
    # numbered copy of the raw rows, so every row can be traced back
    con.execute(f"CREATE OR REPLACE TEMP TABLE src AS SELECT row_number() OVER () AS raw_row_num, * FROM raw.{table}")

    reasons_sql = ",\n    ".join(check_sql(spec))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE chk AS
        SELECT raw_row_num,
               concat_ws('; ',
    {reasons_sql}
               ) AS reasons
        FROM (
            SELECT
    {staging_select(spec)}
            FROM src r
            {spec['joins']}
        ) s
    """)

    # quarantine: failing rows with their reason
    con.execute(f"DELETE FROM etl.quarantine WHERE source_table = '{table}'")
    con.execute(f"""
        INSERT INTO etl.quarantine
        SELECT '{table}', r.raw_row_num, CAST(r.{q(spec['id_column'])} AS VARCHAR), c.reasons, to_json(r)
        FROM src r JOIN chk c USING (raw_row_num)
        WHERE c.reasons <> ''
    """)

    # clean: passing rows, original columns and types
    con.execute(f"""
        CREATE OR REPLACE TABLE clean.{table} AS
        SELECT r.* EXCLUDE (raw_row_num)
        FROM src r JOIN chk c USING (raw_row_num)
        WHERE c.reasons = ''
    """)

    rows_in = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
    passed = con.execute(f"SELECT count(*) FROM clean.{table}").fetchone()[0]
    bad = con.execute(f"SELECT count(*) FROM etl.quarantine WHERE source_table = '{table}'").fetchone()[0]
    return rows_in, passed, bad


def copy_without_contract(con, table):
    """Tables with no contract: nothing to check, so every row passes."""
    con.execute(f"CREATE OR REPLACE TABLE clean.{table} AS SELECT * FROM raw.{table}")
    n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
    return n, n, 0


def check_columns(con, table, spec):
    """
    Compare the real columns with the contract.
    - Missing contract column  -> STOP (we cannot validate that column).
    - Extra, unexpected column -> WARN, keep it, carry on.
    """
    actual = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = ? ORDER BY ordinal_position", [table]).fetchall()]
    expected = [c[0] for c in spec["columns"]]
    missing = [c for c in expected if c not in actual]
    extra = [c for c in actual if c not in expected]
    for c in extra:
        con.execute("INSERT INTO etl.column_warnings VALUES (?, ?, 'extra column not in contract: kept, not validated')",
                    [table, c])
    return missing, extra


# ---------------------------------------------------------------------------
# 3. MAIN
# ---------------------------------------------------------------------------

def main():
    started = datetime.now()
    con = duckdb.connect(DB_FILE)

    con.execute("CREATE SCHEMA IF NOT EXISTS etl")
    con.execute("CREATE SCHEMA IF NOT EXISTS clean")

    # etl tables are rebuilt every run -> re-running is safe
    con.execute("""
        CREATE OR REPLACE TABLE etl.quarantine (
            source_table VARCHAR, raw_row_num BIGINT, record_id VARCHAR,
            reasons VARCHAR, row_data JSON)
    """)
    con.execute("CREATE OR REPLACE TABLE etl.column_warnings (source_table VARCHAR, column_name VARCHAR, action VARCHAR)")

    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'raw' AND table_type = 'BASE TABLE' ORDER BY table_name").fetchall()]
    if not tables:
        sys.exit("No tables found in schema raw. Run  python load_to_duckdb.py  first.")

    # a contract for a table that is not loaded is a problem, not something to skip
    not_loaded = [t for t in CONTRACTS if t not in tables]
    if not_loaded:
        sys.exit(f"STOP: contract table(s) not found in raw: {', '.join(not_loaded)}")

    # first pass: columns (stop early if a contract column is missing)
    for table, spec in CONTRACTS.items():
        missing, _ = check_columns(con, table, spec)
        if missing:
            sys.exit(f"STOP: {table} is missing contract column(s): {', '.join(missing)}")

    summary = []
    for table in tables:
        if table in CONTRACTS:
            rows_in, passed, bad = run_contract_table(con, table, CONTRACTS[table])
            note = "validated against contract"
        else:
            rows_in, passed, bad = copy_without_contract(con, table)
            note = "no contract: all rows pass"
        summary.append((table, rows_in, passed, bad, rows_in == passed + bad, note))

    con.execute("""
        CREATE OR REPLACE TABLE etl.summary (
            table_name VARCHAR, rows_in BIGINT, rows_passed BIGINT, rows_quarantined BIGINT,
            counts_balance BOOLEAN, note VARCHAR, run_at TIMESTAMP)
    """)
    for row in summary:
        con.execute("INSERT INTO etl.summary VALUES (?, ?, ?, ?, ?, ?, ?)", [*row, started])

    # ---- print the report ----
    print()
    print("PulseIQ ETL summary   (run at " + started.strftime("%Y-%m-%d %H:%M:%S") + ")")
    print("-" * 92)
    print(f"{'table':32}{'rows in':>10}{'passed':>10}{'quarantined':>14}  {'balance':8}note")
    print("-" * 92)
    for table, rows_in, passed, bad, ok, note in summary:
        print(f"{table:32}{rows_in:>10}{passed:>10}{bad:>14}  {'OK' if ok else 'ERROR':8}{note}")
    print("-" * 92)
    t_in = sum(s[1] for s in summary)
    t_pass = sum(s[2] for s in summary)
    t_bad = sum(s[3] for s in summary)
    print(f"{'TOTAL':32}{t_in:>10}{t_pass:>10}{t_bad:>14}")

    print("\nQuarantine reasons (contract tables):")
    reasons = con.execute("""
        SELECT source_table, reasons, count(*) FROM etl.quarantine
        GROUP BY ALL ORDER BY source_table, count(*) DESC""").fetchall()
    if not reasons:
        print("  none - every row passed")
    for table, reason, n in reasons:
        print(f"  {table}: {reason}  ({n})")

    warns = con.execute("SELECT source_table, string_agg(column_name, ', ') FROM etl.column_warnings GROUP BY 1 ORDER BY 1").fetchall()
    if warns:
        print("\nWARNING - extra columns not in the contract (kept, not validated):")
        for table, cols in warns:
            print(f"  {table}: {cols}")

    problems = [s for s in summary if not s[4]]
    if problems:
        con.close()
        sys.exit("ERROR: rows in does not equal passed + quarantined for: " + ", ".join(p[0] for p in problems))
    print("\nOK: for every table, rows in = passed + quarantined. No row disappeared silently.")
    con.close()


if __name__ == "__main__":
    main()

"""
build_warehouse.py - PulseIQ Task 2: build the star-schema warehouse.

Run (from the 05_Starter folder, with the virtual environment active):
    python build_warehouse.py

What it does
0. Makes sure every table the star schema needs is loaded. Tables that are missing
   from schema raw (for example dim_department, fact_payroll) are loaded from the
   Excel workbook 03_Data/PulseIQ_Dataset.xlsx, and etl.py is run again so that
   schema clean has every table.
1. Builds schema  wh  (the warehouse) from schema  clean  (rows that passed Task 1):
   11 dimension tables and 12 fact tables, each with a primary key.
2. fact_task and fact_anomaly get an employee_key too (the version of the person
   that was valid at that time).
3. Orphan check: finds fact rows whose key has no matching dimension row.
   Each orphan is explained or marked UNEXPLAINED.
4. History check: proves employee_key points to the right version of each person.
5. Reconciliation: every wh table has exactly the rows that passed in Task 1.

Safe to re-run: schema wh is rebuilt from clean on every run.
"""

import sys
import tempfile
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB_FILE = str(HERE / "pulseiq.duckdb")
DEFAULT_WORKBOOK = HERE.parent / "03_Data" / "PulseIQ_Dataset.xlsx"

# ---------------------------------------------------------------------------
# 1. THE DESIGN: tables and their primary keys
# ---------------------------------------------------------------------------

DIMENSIONS = {
    "dim_date": ["date_key"],
    "dim_employee": ["employee_key"],        # surrogate key: one number per VERSION of a person
    "dim_department": ["department_id"],
    "dim_role": ["role_id"],
    "dim_location": ["work_location"],
    "dim_leave_type": ["leave_type_code"],
    "dim_account": ["account_code"],
    "dim_client": ["client_id"],
    "dim_vendor": ["vendor_id"],
    "dim_team": ["team_id"],
    "dim_sprint": ["sprint_id"],
}

FACTS = {
    "fact_payroll": ["payroll_item_id"],
    "fact_attendance_monthly": ["attendance_fact_id"],
    "fact_leave": ["leave_request_id"],
    "fact_headcount_monthly": ["date_key", "department_id"],
    "fact_ledger": ["line_id"],
    "fact_invoice": ["invoice_id"],
    "fact_invoice_payment": ["payment_id"],
    "fact_expense": ["expense_id"],
    "fact_task": ["task_id"],
    "fact_anomaly": ["result_id"],
    "fact_attrition_risk": ["prediction_id"],
    "fact_forecast": ["forecast_id"],
}

# These two feeds only carry employee_id, so we add an employee_key:
# the version of the person that started on or before the reference date.
# date_expr is the reference date: the sprint end for a task, the month end for an anomaly.
ADD_EMPLOYEE_KEY = {
    "fact_task": {"employee_col": "assignee_employee_id", "date_expr": "TRY_CAST(due_date AS DATE)",
                  "new_col": "assignee_employee_key"},
    "fact_anomaly": {"employee_col": "employee_id", "date_expr": "last_day(TRY_CAST(period AS DATE))",
                     "new_col": "employee_key"},
}

# Every link from a fact to a dimension (from the README join map).
# (fact, fact column, dimension, dimension key, blank allowed?)
LINKS = [
    ("fact_payroll", "employee_key", "dim_employee", "employee_key", False),
    ("fact_payroll", "date_key", "dim_date", "date_key", False),
    ("fact_payroll", "department_id", "dim_department", "department_id", False),
    ("fact_payroll", "role_id", "dim_role", "role_id", False),
    ("fact_attendance_monthly", "employee_key", "dim_employee", "employee_key", False),
    ("fact_attendance_monthly", "date_key", "dim_date", "date_key", False),
    ("fact_attendance_monthly", "department_id", "dim_department", "department_id", False),
    ("fact_leave", "employee_key", "dim_employee", "employee_key", False),
    ("fact_leave", "leave_type_code", "dim_leave_type", "leave_type_code", False),
    ("fact_leave", "start_date_key", "dim_date", "date_key", False),
    ("fact_leave", "end_date_key", "dim_date", "date_key", False),
    ("fact_headcount_monthly", "department_id", "dim_department", "department_id", False),
    ("fact_headcount_monthly", "date_key", "dim_date", "date_key", False),
    ("fact_ledger", "account_code", "dim_account", "account_code", False),
    ("fact_ledger", "cost_center_department_id", "dim_department", "department_id", True),
    ("fact_ledger", "date_key", "dim_date", "date_key", False),
    ("fact_invoice", "client_id", "dim_client", "client_id", False),
    ("fact_invoice", "service_line_account", "dim_account", "account_code", False),
    ("fact_invoice", "invoice_date_key", "dim_date", "date_key", False),
    ("fact_invoice_payment", "invoice_id", "fact_invoice", "invoice_id", False),
    ("fact_invoice_payment", "payment_date_key", "dim_date", "date_key", False),
    ("fact_expense", "vendor_id", "dim_vendor", "vendor_id", False),
    ("fact_expense", "category_account", "dim_account", "account_code", False),
    ("fact_expense", "department_id", "dim_department", "department_id", False),
    ("fact_expense", "expense_date_key", "dim_date", "date_key", False),
    ("fact_task", "team_id", "dim_team", "team_id", False),
    ("fact_task", "sprint_id", "dim_sprint", "sprint_id", False),
    ("fact_task", "assignee_employee_key", "dim_employee", "employee_key", False),
    ("fact_anomaly", "employee_key", "dim_employee", "employee_key", False),
    ("fact_anomaly", "period_key", "dim_date", "date_key", False),
    ("fact_attrition_risk", "employee_key", "dim_employee", "employee_key", False),
    ("fact_attrition_risk", "department_id", "dim_department", "department_id", False),
    ("fact_attrition_risk", "as_of_date_key", "dim_date", "date_key", False),
    ("fact_forecast", "forecast_month_key", "dim_date", "date_key", False),
    ("fact_forecast", "department_id", "dim_department", "department_id", False),
]

# Orphans we know and can explain. Key = (fact, column).
# "when" picks WHICH orphans the explanation covers. Any other orphan stays UNEXPLAINED.
EXPLANATIONS = {
    ("fact_payroll", "employee_key"): (
        "employee_key = -1",
        "employee_key is -1: these payroll rows belong to employee ids that do not exist in dim_employee "
        "(EMP-9001, EMP-9002). They are planted bad rows. They stay visible; the Task 3 gatekeeper will block them.",
    ),
    ("fact_forecast", "forecast_month_key"): (
        "forecast_month_key > (SELECT max(date_key) FROM wh.dim_date)",
        "Forecast months are in the future, after the last date in dim_date (31 Dec 2026). By design.",
    ),
    ("fact_forecast", "department_id"): (
        "department_id = 'ALL'",
        "ALL means the whole company, not one department. By design.",
    ),
}


# ---------------------------------------------------------------------------
# 2. STEP 0: make sure all tables are loaded
# ---------------------------------------------------------------------------

def table_exists(con, schema, table):
    n = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, table]).fetchone()[0]
    return n > 0


def ensure_tables(workbook_arg):
    """Load missing raw tables from the workbook, and refresh schema clean with etl.py."""
    con = duckdb.connect(DB_FILE)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    needed = list(DIMENSIONS) + list(FACTS)
    missing_raw = [t for t in needed if not table_exists(con, "raw", t)]

    if missing_raw:
        workbook = Path(workbook_arg) if workbook_arg else DEFAULT_WORKBOOK
        print("Tables missing from schema raw: " + ", ".join(missing_raw))
        if not workbook.exists():
            con.close()
            sys.exit(
                "\nSTOP: I need the Excel workbook to load them.\n"
                f"Copy PulseIQ_Dataset.xlsx to:  {DEFAULT_WORKBOOK}\n"
                "then run this script again (or give the path:  python build_warehouse.py <path to xlsx>)."
            )
        try:
            import pandas as pd
            sheets = pd.ExcelFile(workbook)
        except ImportError:
            con.close()
            sys.exit("STOP: run  pip install pandas openpyxl  and try again.")
        for t in missing_raw:
            if t not in sheets.sheet_names:
                con.close()
                sys.exit(f"STOP: sheet {t} is not in {workbook.name}")
            df = sheets.parse(t)
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = Path(tmp) / f"{t}.csv"
                df.to_csv(csv_path, index=False)
                con.execute(f"CREATE OR REPLACE TABLE raw.{t} AS SELECT * FROM read_csv_auto('{csv_path.as_posix()}')")
            print(f"  loaded raw.{t} from the workbook ({len(df)} rows)")

    need_etl = bool(missing_raw) or any(not table_exists(con, "clean", t) for t in needed) \
        or not table_exists(con, "etl", "summary")
    con.close()

    if need_etl:
        print("\nRunning etl.py so that schema clean has every table ...")
        import etl  # same folder
        etl.main()
        print()


# ---------------------------------------------------------------------------
# 3. STEP 1: build the warehouse tables
# ---------------------------------------------------------------------------

def q(name):
    return '"' + name + '"'


def create_with_primary_key(con, name, select_sql, pk_cols):
    """Create wh.<name> with a PRIMARY KEY and fill it from a SELECT."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE _stage AS {select_sql}")
    cols = con.execute("DESCRIBE _stage").fetchall()
    column_defs = ", ".join(f"{q(c[0])} {c[1]}" for c in cols)
    pk = ", ".join(q(c) for c in pk_cols)
    con.execute(f"CREATE OR REPLACE TABLE wh.{name} ({column_defs}, PRIMARY KEY ({pk}))")
    try:
        con.execute(f"INSERT INTO wh.{name} SELECT * FROM _stage")
    except duckdb.ConstraintException as e:
        sys.exit(f"STOP: primary key ({pk}) is not unique or has blanks in {name}: {e}")


def fact_select(table):
    """The SELECT that builds a warehouse fact table from schema clean."""
    if table not in ADD_EMPLOYEE_KEY:
        return f"SELECT * FROM clean.{table}"
    c = ADD_EMPLOYEE_KEY[table]
    return f"""
        WITH t AS (SELECT *, {c['date_expr']} AS _ref FROM clean.{table}),
             e AS (SELECT employee_id, employee_key, TRY_CAST(valid_from AS DATE) AS _from FROM clean.dim_employee)
        SELECT t.* EXCLUDE (_ref), e.employee_key AS {c['new_col']}
        FROM t ASOF LEFT JOIN e
          ON t.{q(c['employee_col'])} = e.employee_id AND t._ref >= e._from
    """


def build_warehouse(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS wh")
    for name, pk in DIMENSIONS.items():
        create_with_primary_key(con, name, f"SELECT * FROM clean.{name}", pk)
    for name, pk in FACTS.items():
        create_with_primary_key(con, name, fact_select(name), pk)


# ---------------------------------------------------------------------------
# 4. STEP 2: orphan check
# ---------------------------------------------------------------------------

def orphan_check(con):
    con.execute("""
        CREATE OR REPLACE TABLE wh.orphan_check (
            fact_table VARCHAR, fk_column VARCHAR, dimension VARCHAR,
            rows_checked BIGINT, blank_rows BIGINT, orphan_rows BIGINT, explained_rows BIGINT,
            status VARCHAR, explanation VARCHAR)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE wh.orphan_rows (
            fact_table VARCHAR, fk_column VARCHAR, row_id VARCHAR, key_value VARCHAR, explanation VARCHAR)
    """)
    pk_of = {**DIMENSIONS, **FACTS}

    for fact, col, dim, dim_key, blank_ok in LINKS:
        is_orphan = (
            f"CASE WHEN {q(col)} IS NULL THEN {'FALSE' if blank_ok else 'TRUE'} "
            f"ELSE CAST({q(col)} AS VARCHAR) NOT IN (SELECT CAST({q(dim_key)} AS VARCHAR) FROM wh.{dim}) END"
        )
        when, text = EXPLANATIONS.get((fact, col), ("FALSE", ""))
        checked, blanks, orphans, explained = con.execute(f"""
            SELECT count(*),
                   count(*) FILTER (WHERE {q(col)} IS NULL),
                   count(*) FILTER (WHERE _orphan),
                   count(*) FILTER (WHERE _orphan AND ({when}))
            FROM (SELECT f.*, ({is_orphan}) AS _orphan FROM wh.{fact} f) x
        """).fetchone()

        if orphans == 0:
            status = "OK"
        elif explained == orphans:
            status = "EXPLAINED"
        else:
            status = "UNEXPLAINED"
        con.execute("INSERT INTO wh.orphan_check VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [fact, col, dim, checked, blanks, orphans, explained, status, text if orphans else ""])

        if orphans:
            row_id = " || '|' || ".join(f"CAST({q(c)} AS VARCHAR)" for c in pk_of[fact])
            con.execute(f"""
                INSERT INTO wh.orphan_rows
                SELECT '{fact}', '{col}', {row_id}, CAST({q(col)} AS VARCHAR),
                       CASE WHEN ({when}) THEN '{text.replace("'", "''")}' ELSE 'UNEXPLAINED' END
                FROM (SELECT f.*, ({is_orphan}) AS _orphan FROM wh.{fact} f) x
                WHERE _orphan
            """)


# ---------------------------------------------------------------------------
# 5. STEP 3: history check (is employee_key the right version?)
# ---------------------------------------------------------------------------

def history_checks(con):
    results = []
    for fact in ("fact_payroll", "fact_attendance_monthly"):
        # the version that started on or before the period end date = the version we expect
        wrong_version = con.execute(f"""
            WITH f AS (SELECT employee_id, employee_key, TRY_CAST(period_end_date AS DATE) AS _ref
                       FROM wh.{fact}
                       WHERE employee_key IN (SELECT employee_key FROM wh.dim_employee)),
                 e AS (SELECT employee_id, employee_key AS _expected, TRY_CAST(valid_from AS DATE) AS _from
                       FROM wh.dim_employee)
            SELECT count(*) FROM (
                SELECT f.employee_key, e._expected
                FROM f ASOF LEFT JOIN e ON f.employee_id = e.employee_id AND f._ref >= e._from
            ) WHERE employee_key IS DISTINCT FROM _expected
        """).fetchone()[0]

        wrong_dept = con.execute(f"""
            SELECT count(*) FROM wh.{fact} f JOIN wh.dim_employee e USING (employee_key)
            WHERE f.department_id <> e.department_id
        """).fetchone()[0]

        left_early = con.execute(f"""
            SELECT count(*) FROM wh.{fact} f JOIN wh.dim_employee e USING (employee_key)
            WHERE TRY_CAST(f.period_end_date AS DATE) > TRY_CAST(e.valid_to AS DATE)
        """).fetchone()[0]
        results.append((fact, wrong_version, wrong_dept, left_early))
    return results


def employee_key_example(con):
    """Find one person who changed department and show their payroll before and after the move."""
    move = con.execute("""
        WITH m AS (
            SELECT DISTINCT p.employee_id, TRY_CAST(p.period_end_date AS DATE) AS month_end, e.department_id AS dept
            FROM wh.fact_payroll p JOIN wh.dim_employee e USING (employee_key)),
        j AS (
            SELECT employee_id, month_end, dept,
                   lag(dept) OVER (PARTITION BY employee_id ORDER BY month_end) AS prev_dept
            FROM m)
        SELECT employee_id, month_end FROM j
        WHERE prev_dept IS NOT NULL AND dept <> prev_dept
        ORDER BY employee_id, month_end LIMIT 1
    """).fetchone()
    if not move:
        return None, []
    emp, month_end = move
    rows = con.execute("""
        SELECT DISTINCT p.employee_id, TRY_CAST(p.period_end_date AS DATE) AS month_end, p.employee_key,
               e.department_id AS dept_by_employee_key,
               (SELECT c.department_id FROM wh.dim_employee c
                WHERE c.employee_id = p.employee_id AND c.is_current = 'Y' LIMIT 1) AS dept_by_employee_id
        FROM wh.fact_payroll p JOIN wh.dim_employee e USING (employee_key)
        WHERE p.employee_id = ? AND TRY_CAST(p.period_end_date AS DATE)
              BETWEEN (? - INTERVAL 40 DAY) AND ?
        ORDER BY month_end
    """, [emp, month_end, month_end]).fetchall()
    return emp, rows


# ---------------------------------------------------------------------------
# 6. STEP 4: reconciliation with Task 1
# ---------------------------------------------------------------------------

def reconcile(con):
    con.execute("""
        CREATE OR REPLACE TABLE wh.reconciliation (
            table_name VARCHAR, table_kind VARCHAR, rows_passed_task1 BIGINT, rows_in_warehouse BIGINT, matches BOOLEAN)
    """)
    out = []
    for kind, tables in (("dimension", DIMENSIONS), ("fact", FACTS)):
        for t in tables:
            passed = con.execute("SELECT rows_passed FROM etl.summary WHERE table_name = ?", [t]).fetchone()[0]
            in_wh = con.execute(f"SELECT count(*) FROM wh.{t}").fetchone()[0]
            con.execute("INSERT INTO wh.reconciliation VALUES (?, ?, ?, ?, ?)", [t, kind, passed, in_wh, passed == in_wh])
            out.append((t, kind, passed, in_wh, passed == in_wh))
    return out


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def main():
    workbook_arg = sys.argv[1] if len(sys.argv) > 1 else None
    ensure_tables(workbook_arg)

    con = duckdb.connect(DB_FILE)
    build_warehouse(con)
    orphan_check(con)
    history = history_checks(con)
    example_emp, example_rows = employee_key_example(con)
    recon = reconcile(con)

    line = "-" * 96
    print("\nPulseIQ star-schema warehouse (schema wh)")
    print(line)
    print(f"{'table':28}{'kind':12}{'passed in Task 1':>18}{'in warehouse':>14}  {'match'}")
    print(line)
    for t, kind, passed, in_wh, ok in recon:
        print(f"{t:28}{kind:12}{passed:>18}{in_wh:>14}  {'OK' if ok else 'ERROR'}")
    print(line)

    print("\nORPHAN CHECK: fact rows whose key has no dimension row")
    print(line)
    print(f"{'fact column -> dimension':66}{'checked':>9}{'orphans':>9}  status")
    print(line)
    rows = con.execute("""
        SELECT fact_table, fk_column, dimension, rows_checked, blank_rows, orphan_rows, explained_rows, status
        FROM wh.orphan_check""").fetchall()
    for fact, col, dim, checked, blanks, orphans, explained, status in rows:
        label = f"{fact}.{col} -> {dim}"
        note = f"   ({blanks} blank, allowed)" if blanks and status == "OK" else ""
        print(f"{label:66}{checked:>9}{orphans:>9}  {status}{note}")
    print(line)

    explained_groups = con.execute("""
        SELECT fact_table, fk_column, orphan_rows, explanation FROM wh.orphan_check
        WHERE status = 'EXPLAINED'""").fetchall()
    unexplained = con.execute("""
        SELECT fact_table, fk_column, orphan_rows - explained_rows FROM wh.orphan_check
        WHERE status = 'UNEXPLAINED'""").fetchall()
    if explained_groups:
        print("\nExplained orphans:")
        for fact, col, n, text in explained_groups:
            print(f"  {fact}.{col} ({n} rows): {text}")
    if unexplained:
        print("\nATTENTION - unexplained orphans:")
        for fact, col, n in unexplained:
            print(f"  {fact}.{col}: {n} rows. See wh.orphan_rows.")

    print("\nHISTORY CHECK: is employee_key the right version of the person?")
    print(line)
    for fact, wrong_version, wrong_dept, left_early in history:
        print(f"{fact}")
        print(f"  rows where employee_key is NOT the version valid at the period end : {wrong_version}")
        print(f"  rows where the fact's department differs from that version's       : {wrong_dept}")
        print(f"  rows in the month the person left (last version ended before the month end; allowed): {left_early}")
    if example_emp:
        print(f"\nExample: {example_emp} changed department. Their payroll around the move:")
        print(f"  {'month end':12}{'employee_key':>14}{'department by employee_key':>30}{'department by employee_id (today)':>36}")
        for emp, month_end, key, dept_key, dept_id in example_rows:
            print(f"  {str(month_end):12}{key:>14}{dept_key:>30}{dept_id:>36}")
        print("  -> employee_key puts each month's pay in the department the person was in that month.")
    print(line)

    bad_recon = [r for r in recon if not r[4]]
    if bad_recon:
        con.close()
        sys.exit("ERROR: row counts do not reconcile for: " + ", ".join(r[0] for r in bad_recon))
    if unexplained:
        print("\nDONE, but check the unexplained orphans above.")
    else:
        print("\nOK: row counts reconcile to Task 1, and every orphan is explained.")
    con.close()


if __name__ == "__main__":
    main()

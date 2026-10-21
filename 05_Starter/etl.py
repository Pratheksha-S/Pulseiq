"""PulseIQ ETL: load -> validate against data contracts -> clean / quarantine -> count summary.

Run from this folder with ONE command:   python etl.py

What it does
  1. Runs load_to_duckdb.py, which copies every CSV into schema raw (raw is never edited).
  2. For every raw table that has a contract in contracts.py, checks each column (type, empty, allowed
     values, range, format, unique) and each row rule (cross-column and cross-table checks).
  3. Rows that pass go to clean.<table>. Rows that fail go to quarantine.<table> with a written reason
     in the _reasons column. A row is in exactly one of the two, so nothing disappears silently.
  4. Writes audit.pipeline_summary (rows in / passed / quarantined per table) and audit.quarantine_reasons
     (how many rows each rule caught), then prints the summary.

Safe to re-run: every output table is rebuilt with CREATE OR REPLACE, so a second run gives the same counts.

Unexpected new column in a feed: WARN and keep going (the column is carried into clean and named in the
summary notes). A column the contract requires that is missing: STOP that table (all its rows go to
quarantine with the reason) and the run exits with an error.
"""
import runpy
import sys

import duckdb

from contracts import CONTRACTS

DB = "pulseiq.duckdb"
# Documentation sheets exported as CSV: loaded into raw, but they describe the data, they are not data.
DOCUMENTATION = {"data_dictionary", "data_contracts", "dq_checks_catalog", "metric_dictionary"}


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def ident(c):
    return '"' + c.replace('"', '""') + '"'


def column_checks(name, c, spec):
    """Return a list of (rule_name, SQL expression giving a reason string when the value breaks the promise)."""
    x = f"t.{ident(name)}"
    xs = f"CAST({x} AS VARCHAR)"
    out = []
    if not spec["null"]:
        out.append(("empty_not_allowed", f"CASE WHEN {x} IS NULL THEN {q(name + ': empty, but the contract says it cannot be empty')} END"))
    if spec["type"] != "VARCHAR":
        out.append(("wrong_type", f"CASE WHEN {x} IS NOT NULL AND TRY_CAST({xs} AS {spec['type']}) IS NULL "
                                  f"THEN {q(name + ': value is not a valid ' + spec['type'])} END"))
    if spec["allowed"] is not None:
        vals = ", ".join(q(v) for v in spec["allowed"])
        out.append(("not_allowed_value", f"CASE WHEN {x} IS NOT NULL AND {xs} NOT IN ({vals}) "
                                         f"THEN {q(name + ': value not in the allowed list')} END"))
    cast = f"TRY_CAST({xs} AS {spec['type']})"
    if spec["min"] is not None:
        out.append(("below_min", f"CASE WHEN {cast} < {spec['min']} THEN {q(name + ': below the minimum of ' + str(spec['min']))} END"))
    if spec["max"] is not None:
        out.append(("above_max", f"CASE WHEN {cast} > {spec['max']} THEN {q(name + ': above the maximum of ' + str(spec['max']))} END"))
    if spec["pattern"]:
        out.append(("bad_format", f"CASE WHEN {x} IS NOT NULL AND NOT regexp_full_match({xs}, {q(spec['pattern'])}) "
                                  f"THEN {q(name + ': does not match the format ' + spec['pattern'])} END"))
    if spec["unique"]:
        out.append(("duplicate_value", f"CASE WHEN {x} IS NOT NULL AND count(*) OVER (PARTITION BY {x}) > 1 "
                                       f"THEN {q(name + ': value appears more than once')} END"))
    return out


def process_table(con, table, summary, reasons, problems):
    cols = [r[0] for r in con.execute(f"DESCRIBE raw.{ident(table)}").fetchall()]
    rows_in = con.execute(f"SELECT count(*) FROM raw.{ident(table)}").fetchone()[0]
    col_list = ", ".join(f"t.{ident(c)}" for c in cols)
    bare_list = ", ".join(ident(c) for c in cols)
    notes = []

    if table in DOCUMENTATION:
        summary.append((table, rows_in, None, None, "SKIPPED", "documentation sheet, not data"))
        return
    if table not in CONTRACTS:
        con.execute(f"CREATE OR REPLACE TABLE clean.{ident(table)} AS SELECT {bare_list} FROM raw.{ident(table)}")
        con.execute(f"CREATE OR REPLACE TABLE quarantine.{ident(table)} AS SELECT {bare_list}, CAST(NULL AS VARCHAR) AS _reasons "
                    f"FROM raw.{ident(table)} WHERE false")
        summary.append((table, rows_in, rows_in, 0, "NO CONTRACT", "passed through UNCHECKED, no contract written yet"))
        print(f"  WARN  {table}: no contract, rows passed through unchecked")
        return

    contract = CONTRACTS[table]
    missing = [c for c in contract["columns"] if c not in cols]
    extra = [c for c in cols if c not in contract["columns"]]
    if extra:
        notes.append("new column(s) not in contract: " + ", ".join(extra))
        print(f"  WARN  {table}: new column(s) not in the contract: {', '.join(extra)} (kept, not validated)")

    if missing:
        # Required column is gone: the feed cannot be trusted at all. Stop this table, keep every row visible.
        reason = "STOPPED: contract column(s) missing from feed: " + ", ".join(missing)
        con.execute(f"CREATE OR REPLACE TABLE clean.{ident(table)} AS SELECT {bare_list} FROM raw.{ident(table)} WHERE false")
        con.execute(f"CREATE OR REPLACE TABLE quarantine.{ident(table)} AS SELECT {bare_list}, {q(reason)} AS _reasons FROM raw.{ident(table)}")
        con.execute("INSERT INTO audit.quarantine_reasons VALUES (?, ?, ?)", [table, "missing_column", rows_in])
        summary.append((table, rows_in, 0, rows_in, "STOPPED", reason))
        problems.append(f"{table}: {reason}")
        print(f"  STOP  {table}: {reason}")
        return

    checks = []  # (rule_name, SQL)
    for name, spec in contract["columns"].items():
        checks += column_checks(name, cols, spec)
    for rule, reason, cond in contract["row_rules"]:
        checks.append((rule, f"CASE WHEN ({cond}) THEN {q(rule + ': ' + reason)} END"))

    exprs = ",\n      ".join(sql for _, sql in checks)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _stage AS
        SELECT {col_list},
               list_filter([{exprs}], x -> x IS NOT NULL) AS _r
        FROM raw.{ident(table)} t""")
    con.execute(f"CREATE OR REPLACE TABLE clean.{ident(table)} AS SELECT {bare_list} FROM _stage WHERE len(_r) = 0")
    con.execute(f"CREATE OR REPLACE TABLE quarantine.{ident(table)} AS "
                f"SELECT {bare_list}, array_to_string(_r, ' | ') AS _reasons FROM _stage WHERE len(_r) > 0")
    con.execute("""INSERT INTO audit.quarantine_reasons
                   SELECT ?, split_part(r, ':', 1), count(*) FROM (SELECT unnest(_r) AS r FROM _stage) GROUP BY 1, 2""",
                [table])
    passed = con.execute(f"SELECT count(*) FROM clean.{ident(table)}").fetchone()[0]
    quar = con.execute(f"SELECT count(*) FROM quarantine.{ident(table)}").fetchone()[0]
    status = "OK" if quar == 0 else "QUARANTINED"
    summary.append((table, rows_in, passed, quar, status, "; ".join(notes)))


def main():
    print("== 1. Loading raw tables ==")
    runpy.run_path("load_to_duckdb.py")

    con = duckdb.connect(DB)
    for s in ("clean", "quarantine", "audit"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    con.execute("CREATE OR REPLACE TABLE audit.quarantine_reasons (table_name VARCHAR, rule_or_column VARCHAR, rows_hit BIGINT)")

    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw' ORDER BY 1").fetchall()]
    summary, reasons, problems = [], [], []

    print("\n== 2. Validating against data contracts ==")
    for t in tables:
        process_table(con, t, summary, reasons, problems)
    for t in CONTRACTS:
        if t not in tables:
            problems.append(f"{t}: contract exists but no raw table was loaded")
            print(f"  STOP  {t}: contract exists but no raw table was loaded")

    con.execute("""CREATE OR REPLACE TABLE audit.pipeline_summary (
        table_name VARCHAR, rows_in BIGINT, rows_passed BIGINT, rows_quarantined BIGINT,
        balanced VARCHAR, status VARCHAR, notes VARCHAR)""")
    for t, rin, rp, rq, status, notes in summary:
        if rp is None:
            balanced = "n/a"
        else:
            balanced = "yes" if rin == rp + rq else "NO"
            if balanced == "NO":
                problems.append(f"{t}: rows in ({rin}) != passed ({rp}) + quarantined ({rq})")
        con.execute("INSERT INTO audit.pipeline_summary VALUES (?,?,?,?,?,?,?)", [t, rin, rp, rq, balanced, status, notes])

    print("\n== 3. Count summary ==")
    print(f"{'table':<28}{'rows_in':>9}{'passed':>9}{'quarantined':>13}  {'in=pass+quar':<13}status")
    tot = [0, 0, 0]
    for t, rin, rp, rq, balanced, status, notes in con.execute(
            "SELECT table_name, rows_in, rows_passed, rows_quarantined, balanced, status, notes "
            "FROM audit.pipeline_summary ORDER BY table_name").fetchall():
        f = lambda v: "-" if v is None else str(v)
        print(f"{t:<28}{rin:>9}{f(rp):>9}{f(rq):>13}  {balanced:<13}{status}" + (f"  ({notes})" if notes else ""))
        if rp is not None:
            tot = [tot[0] + rin, tot[1] + rp, tot[2] + rq]
    print(f"{'TOTAL (validated tables)':<28}{tot[0]:>9}{tot[1]:>9}{tot[2]:>13}")

    print("\n== 4. Why rows were quarantined ==")
    for t, rule, n in con.execute(
            "SELECT table_name, rule_or_column, rows_hit FROM audit.quarantine_reasons ORDER BY table_name, rows_hit DESC, rule_or_column").fetchall():
        print(f"  {t:<28}{rule:<30}{n:>6} rows")

    con.close()
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nAll tables balanced: rows in = passed + quarantined.")


if __name__ == "__main__":
    main()

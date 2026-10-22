# Daily Log

**Date:** 22 Oct 2026
**Done today:** Set up the repo and virtual environment. Loaded 25 tables into DuckDB. Wrote contract notes for 3 tables. Built etl.py, which checks 4 feeds against their contracts, quarantines bad rows with reasons, and writes a summary table. Ran it twice with the same result and pushed it to GitHub.
**Doing next:** Task 2, the star schema.
**Blocked by:** Nothing now. Earlier, the virtual environment and the terminal folder caused errors, and both are solved.
**One thing I learned:** Rows in must always equal passed plus quarantined, so no row can disappear silently.

**Date:** 22 Oct 2026
**Done today:** Read the join map and designed the star schema: 11 dimension tables and 12 fact tables. Loaded 6 missing tables from the workbook. Built the warehouse (schema wh) with a primary key on every table, using build_warehouse.py. Added employee_key to fact_task and fact_anomaly. Ran the orphan check and explained every orphan: 2 payroll rows with employee_key -1 (planted bad rows), 36 forecast rows for future months, and 12 forecast rows for department ALL. Proved employee_key is the right version of each person (0 wrong versions, 0 wrong departments). Reconciled every warehouse table to the rows that passed in Task 1. Saved the ER diagram in Mermaid in docs/star_schema.md. Ran the script twice with the same result and pushed it to GitHub.
**Doing next:** Task 3, the data-quality gatekeeper.
**Blocked by:** Nothing now. Earlier, 6 tables were missing from the database, and the terminal was in the wrong folder. Both are solved.
**One thing I learned:** employee_key identifies one version of a person, not the person. A June pay row must use the version valid at the end of June, so June pay stays in the old department.

"""Data contracts: one written promise per column.

SOURCE: fact_anomaly, fact_attrition_risk, fact_forecast and fact_task follow the data_contracts sheet.
The other tables have no row in that sheet, so their contracts here were written from the data_dictionary
sheet plus profiling the CSVs (marked "inferred"). Edit this file when a contract changes; etl.py needs no change.

col(type, null=False, allowed=None, min=None, max=None, pattern=None, unique=False)
  type    : VARCHAR, BIGINT, DOUBLE or DATE
  null    : True if the column may be empty
  allowed : closed list of values       min/max : numeric/date bounds (inclusive)
  pattern : regex the whole value must match      unique : no repeated values
ROW_RULES: (rule_name, reason, SQL condition that is TRUE when the row is BAD). Use alias t for the row.
"""

def col(type, null=False, allowed=None, min=None, max=None, pattern=None, unique=False):
    return dict(type=type, null=null, allowed=allowed, min=min, max=max, pattern=pattern, unique=unique)

S, I, F, D = "VARCHAR", "BIGINT", "DOUBLE", "DATE"
YN = ["Y", "N"]
DEPTS = [f"D{n:02d}" for n in range(11)]
EMP = r"EMP-\d{4}"

CONTRACTS = {
 "dim_date": {
  "columns": {
   "date": col(D, unique=True), "date_key": col(I, unique=True, min=20200101, max=20261231),
   "year": col(I, min=2020, max=2026), "quarter": col(I, min=1, max=4), "month_number": col(I, min=1, max=12),
   "month_name": col(S), "year_month": col(S, pattern=r"\d{4}-\d{2}"), "week_of_year": col(I, min=1, max=53),
   "day_of_week": col(S, allowed=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]),
   "is_weekend": col(S, allowed=YN), "is_holiday": col(S, allowed=YN), "is_working_day": col(S, allowed=YN),
   "financial_year": col(S, pattern=r"FY\d{4}-\d{2}"), "fiscal_quarter": col(S, allowed=["Q1","Q2","Q3","Q4"]),
   "fiscal_month_number": col(I, min=1, max=12), "is_month_end": col(S, allowed=YN),
   "month_start_date": col(D), "month_end_date": col(D)},
  "row_rules": []},

 "dim_employee": {
  "columns": {
   "employee_key": col(I, unique=True), "employee_id": col(S, pattern=EMP), "full_name": col(S),
   "gender": col(S, allowed=["Male","Female","Other"]), "date_of_birth": col(D),
   "hire_date": col(D), "exit_date": col(D, null=True),
   "employment_status": col(S, allowed=["Active","Resigned","Terminated","Contract Ended"]),
   "employment_type": col(S, allowed=["Permanent","Fixed-Term Contract"]),
   "department_id": col(S, allowed=DEPTS), "role_id": col(S, pattern=r"R\d{3}"), "role_title": col(S),
   "job_level": col(I, min=1, max=7), "manager_id": col(S, null=True, pattern=EMP), "work_location": col(S),
   "work_mode": col(S, allowed=["Onsite","Hybrid","Remote"]), "highest_education": col(S),
   "valid_from": col(D), "valid_to": col(D), "is_current": col(S, allowed=YN),
   "change_reason": col(S, allowed=["Joining","Promotion","Transfer","Manager Change","Manager Exit / Reassignment"])},
  "row_rules": [
   ("exit_before_hire", "exit_date is earlier than hire_date", "t.exit_date < t.hire_date"),
   ("valid_range", "valid_to is earlier than valid_from", "t.valid_to < t.valid_from"),
   ("status_vs_exit", "status is Active but exit_date is filled",
    "(t.employment_status = 'Active' AND t.exit_date IS NOT NULL)")]},

 "fact_attendance_monthly": {
  "columns": {
   "attendance_fact_id": col(S, unique=True, pattern=r"FA-\d{5}"), "employee_id": col(S, pattern=EMP),
   "date_key": col(I, min=20250101, max=20261231), "employee_key": col(I), "department_id": col(S, allowed=DEPTS),
   "scheduled_days": col(I, min=0, max=31), "present_days": col(I, min=0, max=31), "wfh_days": col(I, min=0, max=31),
   "half_days_unpaid": col(I, min=0, max=31), "paid_leave_days": col(I, min=0, max=31),
   "unpaid_absence_days": col(I, min=0, max=31), "late_arrivals": col(I, min=0, max=31),
   "avg_hours_worked": col(F, min=0, max=24), "overtime_hours": col(F, min=0, max=200),
   "attendance_rate": col(F, min=0, max=1), "period_end_date": col(D)},
  "row_rules": [
   ("present_gt_scheduled", "present_days is more than scheduled_days", "t.present_days > t.scheduled_days"),
   ("wfh_gt_present", "wfh_days is more than present_days", "t.wfh_days > t.present_days"),
   ("date_key_mismatch", "date_key does not match period_end_date",
    "t.date_key <> CAST(strftime(t.period_end_date, '%Y%m%d') AS BIGINT)"),
   ("unknown_employee", "employee_id not found in dim_employee",
    "NOT EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.employee_id)"),
   ("after_exit", "attendance recorded for a month that starts after the employee's exit_date",
    "EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.employee_id AND e.exit_date IS NOT NULL "
    "AND date_trunc('month', t.period_end_date) > e.exit_date)")]},

 "fact_attrition_risk": {
  "columns": {
   "prediction_id": col(S, unique=True, pattern=r"AR-\d{4}"), "employee_id": col(S, pattern=EMP),
   "as_of_date_key": col(I), "department_id": col(S, allowed=DEPTS[1:]), "as_of_date": col(D),
   "risk_score_12m": col(F, min=0, max=1), "risk_band": col(S, allowed=["Low","Medium","High"]),
   "top_reason_1": col(S, null=True), "top_reason_2": col(S, null=True), "top_reason_3": col(S, null=True),
   "model_version": col(S), "model_note": col(S), "employee_key": col(I)},
  "row_rules": [
   ("unknown_employee", "employee_id not found in dim_employee",
    "NOT EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.employee_id)"),
   ("employee_not_active", "employee is not Active in dim_employee (contract: exists and active)",
    "NOT EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.employee_id "
    "AND e.is_current = 'Y' AND e.employment_status = 'Active')")]},

 "fact_anomaly": {
  "columns": {
   "result_id": col(S, unique=True, pattern=r"AN-\d{4}"),
   "source_dataset": col(S, allowed=["attendance_monthly_features","payroll_monthly_features"]),
   "record_id": col(S, unique=True), "employee_id": col(S, pattern=EMP), "period": col(D),
   "anomaly_score": col(F, min=0, max=1), "flagged": col(S, allowed=YN), "reason_code": col(S),
   "reason_text": col(S), "money_at_risk": col(F, null=True, min=0), "model_version": col(S),
   "scored_at": col(S), "period_key": col(I)},
  "row_rules": [
   ("unknown_employee", "employee_id not found in dim_employee",
    "NOT EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.employee_id)"),
   ("period_not_month_edge", "period is neither the first nor the last day of a month",
    "t.period <> date_trunc('month', t.period) AND t.period <> last_day(t.period)"),
   ("money_on_attendance_row", "money_at_risk must be blank for attendance rows",
    "t.source_dataset = 'attendance_monthly_features' AND t.money_at_risk IS NOT NULL"),
   ("money_missing_on_payroll_row", "money_at_risk is required for payroll rows",
    "t.source_dataset = 'payroll_monthly_features' AND t.money_at_risk IS NULL")]},

 "fact_expense": {
  "columns": {
   "expense_id": col(S, unique=True, pattern=r"EXP-\d{5}"), "expense_date": col(D), "expense_date_key": col(I),
   "category_account": col(I, min=5100, max=5199), "vendor_id": col(S, pattern=r"V-\d{3}"),
   "bill_reference": col(S, unique=True), "description": col(S), "department_id": col(S, allowed=DEPTS),
   "amount_excl_gst": col(F, min=0), "gst_rate_percent": col(I, allowed=[0, 5, 12, 18, 28]),
   "gst_amount": col(F, min=0), "total_amount": col(F, min=0), "due_date": col(D), "paid_date": col(D, null=True),
   "status": col(S, allowed=["PAID","UNPAID"]), "payment_mode": col(S, null=True),
   "submitted_by": col(S, pattern=EMP), "approved_by": col(S, pattern=EMP),
   "ledger_entry_id": col(S, unique=True, pattern=r"JE-\d{6}"),
   "payment_ledger_entry_id": col(S, null=True, pattern=r"JE-\d{6}")},
  "row_rules": [
   ("total_mismatch", "total_amount is not amount_excl_gst + gst_amount",
    "abs(t.total_amount - t.amount_excl_gst - t.gst_amount) > 0.01"),
   ("gst_mismatch", "gst_amount is not amount_excl_gst x gst_rate_percent",
    "abs(t.gst_amount - t.amount_excl_gst * t.gst_rate_percent / 100.0) > 0.01"),
   ("paid_without_date", "status is PAID but paid_date is empty", "t.status = 'PAID' AND t.paid_date IS NULL"),
   ("unpaid_with_date", "status is UNPAID but paid_date is filled", "t.status = 'UNPAID' AND t.paid_date IS NOT NULL")]},

 "fact_headcount_monthly": {
  "columns": {
   "date_key": col(I), "month_start_date": col(D), "department_id": col(S, allowed=DEPTS),
   "headcount_start": col(I, min=0), "hires": col(I, min=0), "exits": col(I, min=0),
   "voluntary_exits": col(I, min=0), "transfers_in": col(I, min=0), "transfers_out": col(I, min=0),
   "headcount_end": col(I, min=0), "attrition_rate_monthly_pct": col(F, min=0, max=100)},
  "row_rules": [
   ("roll_forward", "headcount_end is not start + hires - exits + transfers_in - transfers_out",
    "t.headcount_end <> t.headcount_start + t.hires - t.exits + t.transfers_in - t.transfers_out"),
   ("voluntary_gt_exits", "voluntary_exits is more than exits", "t.voluntary_exits > t.exits"),
   ("duplicate_grain", "more than one row for the same month and department",
    "(SELECT count(*) FROM raw.fact_headcount_monthly x WHERE x.date_key = t.date_key AND x.department_id = t.department_id) > 1")]},

 "fact_invoice": {
  "columns": {
   "invoice_id": col(S, unique=True, pattern=r"INV-\d{5}"), "invoice_number": col(S, unique=True),
   "client_id": col(S, pattern=r"CL-\d{3}"), "invoice_date_key": col(I), "invoice_date": col(D), "due_date": col(D),
   "service_line_account": col(I, min=4000, max=4099), "subtotal": col(F, min=0),
   "gst_rate_percent": col(I, allowed=[0, 5, 12, 18, 28]), "gst_amount": col(F, min=0), "total_amount": col(F, min=0),
   "amount_paid": col(F, min=0), "balance_due": col(F),
   "status": col(S, allowed=["DRAFT","SENT","PARTIALLY_PAID","PAID","OVERDUE","CANCELLED"]),
   "last_payment_date": col(D, null=True), "days_to_pay": col(F, null=True, min=0),
   "days_past_due_at_cutoff": col(I, min=0), "ageing_bucket_at_cutoff": col(S),
   "carried_in_from_prior_period": col(S, allowed=YN), "ledger_entry_id": col(S, null=True, pattern=r"JE-\d{6}")},
  "row_rules": [
   ("total_mismatch", "total_amount is not subtotal + gst_amount", "t.total_amount <> t.subtotal + t.gst_amount"),
   ("negative_balance", "balance_due is negative on an invoice that is not cancelled (overpaid)",
    "t.balance_due < 0 AND t.status <> 'CANCELLED'"),
   ("balance_mismatch", "balance_due is not total_amount - amount_paid on an invoice that is not cancelled",
    "t.status <> 'CANCELLED' AND t.balance_due <> t.total_amount - t.amount_paid"),
   ("due_before_invoice", "due_date is earlier than invoice_date", "t.due_date < t.invoice_date")]},

 "fact_invoice_payment": {
  "columns": {
   "payment_id": col(S, unique=True, pattern=r"PAY-\d{5}"), "invoice_id": col(S, pattern=r"INV-\d{5}"),
   "payment_date_key": col(I), "payment_date": col(D), "amount": col(F, min=0.01),
   "payment_mode": col(S, allowed=["NEFT","RTGS","Wire Transfer","UPI Business"]),
   "reference_no": col(S, unique=True), "received_by": col(S, pattern=r"U-\d{4}"),
   "ledger_entry_id": col(S, unique=True, pattern=r"JE-\d{6}")},
  "row_rules": [
   ("unknown_invoice", "invoice_id not found in fact_invoice",
    "NOT EXISTS (SELECT 1 FROM raw.fact_invoice i WHERE i.invoice_id = t.invoice_id)"),
   ("paid_before_invoice", "payment_date is earlier than the invoice_date",
    "EXISTS (SELECT 1 FROM raw.fact_invoice i WHERE i.invoice_id = t.invoice_id AND t.payment_date < i.invoice_date)")]},

 "fact_task": {
  "columns": {
   "task_id": col(S, unique=True, pattern=r"TASK-\d{5}"), "team_id": col(S, pattern=r"T\d{2}"),
   "sprint_id": col(S, pattern=r"SP-\d{2}"), "assignee_employee_id": col(S, pattern=EMP),
   "task_type": col(S, allowed=["Bug","Feature","Spike","Chore"]), "priority": col(S, allowed=["P1","P2","P3","P4"]),
   "story_points": col(I, allowed=[1, 2, 3, 5, 8, 13]), "created_date": col(D), "started_date": col(D, null=True),
   "due_date": col(D), "completed_date": col(D, null=True),
   "status": col(S, allowed=["To Do","In Progress","Blocked","Done","Cancelled"]),
   "reopened_count": col(I, min=0, max=10), "cycle_time_days": col(F, null=True, min=0),
   "is_overdue_at_cutoff": col(S, allowed=YN), "completed_on_time": col(S, null=True, allowed=YN)},
  "row_rules": [
   ("completed_before_started", "completed_date is earlier than started_date", "t.completed_date < t.started_date"),
   ("done_without_date", "status is Done but completed_date is empty", "t.status = 'Done' AND t.completed_date IS NULL"),
   ("started_before_created", "created_date is later than started_date", "t.created_date > t.started_date"),
   ("started_blank_not_todo", "started_date is blank but status is not To Do", "t.started_date IS NULL AND t.status <> 'To Do'"),
   ("due_not_sprint_end", "due_date is not the sprint end date",
    "NOT EXISTS (SELECT 1 FROM raw.dim_sprint s WHERE s.sprint_id = t.sprint_id AND s.end_date = t.due_date)"),
   ("unknown_team", "team_id not found in dim_team",
    "NOT EXISTS (SELECT 1 FROM raw.dim_team x WHERE x.team_id = t.team_id)"),
   ("unknown_sprint", "sprint_id not found in dim_sprint",
    "NOT EXISTS (SELECT 1 FROM raw.dim_sprint x WHERE x.sprint_id = t.sprint_id)"),
   ("unknown_employee", "assignee_employee_id not found in dim_employee",
    "NOT EXISTS (SELECT 1 FROM raw.dim_employee e WHERE e.employee_id = t.assignee_employee_id)")]},
 "fact_forecast": {
  "columns": {
   "forecast_id": col(S, unique=True, pattern=r"FC-\d{4}"),
   "metric": col(S, allowed=["headcount_end","attrition_rate_monthly_pct"]),
   "department_id": col(S, allowed=["ALL"] + DEPTS[1:]),
   "forecast_month": col(D), "forecast_value": col(F, min=0), "lower_80": col(F), "upper_80": col(F),
   "method": col(S), "trained_through": col(S, pattern=r"\d{4}-\d{2}"), "model_version": col(S),
   "forecast_month_key": col(I)},
  "row_rules": [
   ("not_first_of_month", "forecast_month is not the first day of a month", "t.forecast_month <> date_trunc('month', t.forecast_month)"),
   ("lower_above_value", "lower_80 is above forecast_value", "t.lower_80 > t.forecast_value"),
   ("upper_below_value", "upper_80 is below forecast_value", "t.upper_80 < t.forecast_value")]},
}

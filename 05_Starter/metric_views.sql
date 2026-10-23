-- =====================================================================
-- PulseIQ Task 4: governed metric layer (28 metrics, one view each)
-- Source: schema wh (validated warehouse from Tasks 1-2).
-- Pattern: one view per metric, named m<NN>_<name>. Every view carries its
-- date (month_start_date / sprint / as_of_date) and its group (department, team ...).
-- SMALL-GROUP RULE: a group smaller than the metric's minimum_group_size is
-- NOT returned (filtered with WHERE/HAVING). Metrics with no minimum are not filtered.
-- Run order: build_metrics.py runs this whole file; it is safe to re-run.
-- =====================================================================

-- ---------- helper views (not metrics) ----------------------------------
-- Report date = last day of data (the cutoff used by fact_invoice).
CREATE OR REPLACE VIEW ref_report_params AS
SELECT DATE '2026-09-30' AS report_date;

-- Payroll rows we trust. DECISION: exclude (a) ghost rows whose employee is not in
-- dim_employee (employee_key = -1, e.g. EMP-9001/9002) and (b) duplicate rows for the
-- same employee and month (keep the first payroll_item_id). Reason: they inflate cost and
-- headcount and do not reconcile to the ledger.
CREATE OR REPLACE VIEW ref_payroll_clean AS
SELECT *
FROM (
    SELECT p.*, d.month_start_date,
           ROW_NUMBER() OVER (PARTITION BY p.employee_id, p.date_key ORDER BY p.payroll_item_id) AS rn
    FROM wh.fact_payroll p
    JOIN wh.dim_date d ON d.date_key = p.date_key
    WHERE p.employee_key <> -1
) x
WHERE rn = 1;

CREATE OR REPLACE VIEW ref_attendance AS
SELECT a.*, d.month_start_date
FROM wh.fact_attendance_monthly a
JOIN wh.dim_date d ON d.date_key = a.date_key;

CREATE OR REPLACE VIEW ref_ledger AS
SELECT l.*, d.month_start_date
FROM wh.fact_ledger l
JOIN wh.dim_date d ON d.date_key = l.date_key;

-- ---------- HEADCOUNT & ATTRITION (min group 5 / 10) ---------------------
-- M01 Headcount. Group size = people in the department. Min 5.
CREATE OR REPLACE VIEW m01_headcount AS
SELECT month_start_date, department_id, headcount_end AS headcount
FROM wh.fact_headcount_monthly
WHERE headcount_end >= 5;

-- M02 Hires. Group = department population (headcount_end). Min 5.
CREATE OR REPLACE VIEW m02_hires AS
SELECT month_start_date, department_id, hires
FROM wh.fact_headcount_monthly
WHERE headcount_end >= 5;

-- M03 Exits
CREATE OR REPLACE VIEW m03_exits AS
SELECT month_start_date, department_id, exits
FROM wh.fact_headcount_monthly
WHERE headcount_end >= 5;

-- M04 Attrition rate (monthly) = exits / headcount_START x 100. Min 10.
-- (Same formula as the source column attrition_rate_monthly_pct.)
CREATE OR REPLACE VIEW m04_attrition_monthly AS
SELECT month_start_date, department_id, exits, headcount_start,
       ROUND(100.0 * exits / NULLIF(headcount_start, 0), 2) AS attrition_pct
FROM wh.fact_headcount_monthly
WHERE headcount_start >= 10;

-- Evidence for the "think for yourself" question: same month, three denominators.
CREATE OR REPLACE VIEW m04_denominator_compare AS
SELECT month_start_date, department_id, exits, headcount_start, headcount_end,
       ROUND(100.0 * exits / NULLIF(headcount_start, 0), 2) AS pct_on_start,
       ROUND(100.0 * exits / NULLIF(headcount_end, 0), 2) AS pct_on_end,
       ROUND(100.0 * exits / NULLIF((headcount_start + headcount_end) / 2.0, 0), 2) AS pct_on_average
FROM wh.fact_headcount_monthly
WHERE headcount_start >= 10;

-- M05 Attrition rate (rolling 12 months) = SUM(exits 12m) / AVG(headcount_end 12m) x 100. Min 10.
-- Only months that have a full 12-month window are returned.
CREATE OR REPLACE VIEW m05_attrition_rolling_12m AS
SELECT month_start_date, department_id, exits_12m, avg_headcount_12m,
       ROUND(100.0 * exits_12m / NULLIF(avg_headcount_12m, 0), 2) AS attrition_rolling_12m_pct
FROM (
    SELECT month_start_date, department_id,
           SUM(exits) OVER w AS exits_12m,
           AVG(headcount_end) OVER w AS avg_headcount_12m,
           COUNT(*) OVER w AS months_in_window
    FROM wh.fact_headcount_monthly
    WINDOW w AS (PARTITION BY department_id ORDER BY month_start_date
                 ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
) t
WHERE months_in_window = 12 AND avg_headcount_12m >= 10;

-- M06 Voluntary attrition rate (rolling 12 months, resignations only). Min 10.
CREATE OR REPLACE VIEW m06_voluntary_attrition_12m AS
SELECT month_start_date, department_id, voluntary_exits_12m, avg_headcount_12m,
       ROUND(100.0 * voluntary_exits_12m / NULLIF(avg_headcount_12m, 0), 2) AS voluntary_attrition_12m_pct
FROM (
    SELECT month_start_date, department_id,
           SUM(voluntary_exits) OVER w AS voluntary_exits_12m,
           AVG(headcount_end) OVER w AS avg_headcount_12m,
           COUNT(*) OVER w AS months_in_window
    FROM wh.fact_headcount_monthly
    WINDOW w AS (PARTITION BY department_id ORDER BY month_start_date
                 ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
) t
WHERE months_in_window = 12 AND avg_headcount_12m >= 10;

-- ---------- PAYROLL (dept x month; min group 5 paid employees) ----------
-- NOTE: the dictionary says "Employee x month", but a minimum group size of 5 means
-- individual rows may never be shown, so these views are department x month.
-- M07 Gross pay
CREATE OR REPLACE VIEW m07_gross_pay AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(SUM(gross_pay), 2) AS gross_pay
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M08 Net pay
CREATE OR REPLACE VIEW m08_net_pay AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(SUM(net_pay), 2) AS net_pay
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M09 Payroll cost (employer cost)
CREATE OR REPLACE VIEW m09_payroll_cost AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(SUM(employer_cost), 2) AS employer_cost
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M10 Cost per head
CREATE OR REPLACE VIEW m10_cost_per_head AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(SUM(employer_cost) / COUNT(DISTINCT employee_id), 2) AS cost_per_head
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M11 Overtime share (% of employer cost)
CREATE OR REPLACE VIEW m11_overtime_share AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(100.0 * SUM(overtime_pay) / NULLIF(SUM(employer_cost), 0), 2) AS overtime_share_pct
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M12 Variable pay share (% of gross pay)
CREATE OR REPLACE VIEW m12_variable_pay_share AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(100.0 * SUM(variable_pay) / NULLIF(SUM(gross_pay), 0), 2) AS variable_pay_share_pct
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- ---------- ATTENDANCE (dept x month; min group 5 employees) ------------
-- M13 Attendance rate = (present + 0.5 x half days) / scheduled days
CREATE OR REPLACE VIEW m13_attendance_rate AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees,
       ROUND((SUM(present_days) + 0.5 * SUM(half_days_unpaid)) / NULLIF(SUM(scheduled_days), 0), 4) AS attendance_rate
FROM ref_attendance
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M14 Late arrival rate = late arrivals per present day
CREATE OR REPLACE VIEW m14_late_arrival_rate AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees,
       ROUND(1.0 * SUM(late_arrivals) / NULLIF(SUM(present_days), 0), 4) AS late_arrival_rate
FROM ref_attendance
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- M15 Leave without pay days (payroll lop_days)
CREATE OR REPLACE VIEW m15_lop_days AS
SELECT month_start_date, department_id, COUNT(DISTINCT employee_id) AS employees_paid,
       ROUND(SUM(lop_days), 1) AS lop_days
FROM ref_payroll_clean
GROUP BY 1, 2
HAVING COUNT(DISTINCT employee_id) >= 5;

-- ---------- FINANCE (ledger; no minimum group size) ---------------------
-- Ledger sign: signed_amount_debit_positive is debit-positive, so income is negative.
-- M16 Revenue (accounts 4001-4004), account x month
CREATE OR REPLACE VIEW m16_revenue AS
SELECT month_start_date, account_code, ROUND(-SUM(signed_amount_debit_positive), 2) AS revenue
FROM ref_ledger
WHERE account_code BETWEEN 4001 AND 4004
GROUP BY 1, 2;

-- Company revenue and net profit by month (helper for M17-M19)
CREATE OR REPLACE VIEW ref_finance_month AS
SELECT month_start_date,
       -SUM(CASE WHEN account_code BETWEEN 4001 AND 4004 THEN signed_amount_debit_positive ELSE 0 END) AS revenue,
       -SUM(CASE WHEN account_code BETWEEN 4001 AND 5999 THEN signed_amount_debit_positive ELSE 0 END) AS net_profit,
        SUM(CASE WHEN account_code BETWEEN 5001 AND 5005 THEN signed_amount_debit_positive ELSE 0 END) AS payroll_cost_ledger
FROM ref_ledger
GROUP BY 1;

-- M17 Net profit (4xxx less 5xxx, includes interest income 4900)
CREATE OR REPLACE VIEW m17_net_profit AS
SELECT month_start_date, ROUND(net_profit, 2) AS net_profit
FROM ref_finance_month;

-- M18 Operating margin (%)
CREATE OR REPLACE VIEW m18_operating_margin AS
SELECT month_start_date, ROUND(100.0 * net_profit / NULLIF(revenue, 0), 2) AS operating_margin_pct
FROM ref_finance_month;

-- M19 Payroll as % of revenue (uses LEDGER payroll accounts 5001-5005, per the caveat)
CREATE OR REPLACE VIEW m19_payroll_pct_revenue AS
SELECT month_start_date, ROUND(100.0 * payroll_cost_ledger / NULLIF(revenue, 0), 2) AS payroll_pct_of_revenue
FROM ref_finance_month;

-- M20 DSO = closing receivables (ledger 1100, cumulative) / invoiced incl. GST in the last 90 days x 90
CREATE OR REPLACE VIEW m20_dso AS
WITH months AS (
    SELECT DISTINCT month_start_date, month_end_date FROM wh.dim_date
    WHERE month_start_date BETWEEN DATE '2025-04-01' AND DATE '2026-09-01'
),
recv AS (
    SELECT m.month_start_date,
           SUM(l.signed_amount_debit_positive) AS closing_receivables
    FROM months m
    JOIN wh.fact_ledger l ON l.account_code = 1100 AND l.entry_date <= m.month_end_date
    GROUP BY 1
),
sales AS (
    SELECT m.month_start_date, SUM(i.total_amount) AS invoiced_90d
    FROM months m
    JOIN wh.fact_invoice i
      ON i.invoice_date <= m.month_end_date
     AND date_diff('day', i.invoice_date, m.month_end_date) <= 89
    WHERE i.status NOT IN ('DRAFT', 'CANCELLED')
    GROUP BY 1
)
SELECT r.month_start_date, ROUND(r.closing_receivables, 2) AS closing_receivables,
       s.invoiced_90d,
       ROUND(90.0 * r.closing_receivables / NULLIF(s.invoiced_90d, 0), 1) AS dso_days
FROM recv r JOIN sales s USING (month_start_date);

-- M21 Overdue receivables at the report date, by client (point in time)
CREATE OR REPLACE VIEW m21_overdue_receivables AS
SELECT p.report_date, i.client_id, COUNT(*) AS overdue_invoices,
       ROUND(SUM(i.balance_due), 2) AS overdue_amount
FROM wh.fact_invoice i
CROSS JOIN ref_report_params p
WHERE i.due_date < p.report_date
  AND i.status NOT IN ('DRAFT', 'CANCELLED')
  AND i.balance_due > 0
GROUP BY 1, 2;

-- ---------- ENGINEERING TASKS (team x sprint) ---------------------------
-- M22 Task completion rate. Min 5 tasks (cancelled excluded).
CREATE OR REPLACE VIEW m22_task_completion_rate AS
SELECT team_id, sprint_id, COUNT(*) AS tasks_planned,
       SUM(CASE WHEN status = 'Done' THEN 1 ELSE 0 END) AS tasks_done,
       ROUND(100.0 * SUM(CASE WHEN status = 'Done' THEN 1 ELSE 0 END) / COUNT(*), 2) AS completion_rate_pct
FROM wh.fact_task
WHERE status <> 'Cancelled'
GROUP BY 1, 2
HAVING COUNT(*) >= 5;

-- M23 On-time delivery rate (Done tasks only). Min 5 tasks.
CREATE OR REPLACE VIEW m23_on_time_delivery AS
SELECT team_id, sprint_id, COUNT(*) AS tasks_done,
       SUM(CASE WHEN completed_on_time = 'Y' THEN 1 ELSE 0 END) AS tasks_on_time,
       ROUND(100.0 * SUM(CASE WHEN completed_on_time = 'Y' THEN 1 ELSE 0 END) / COUNT(*), 2) AS on_time_pct
FROM wh.fact_task
WHERE status = 'Done'
GROUP BY 1, 2
HAVING COUNT(*) >= 5;

-- M24 Velocity (story points done). No minimum.
CREATE OR REPLACE VIEW m24_velocity AS
SELECT team_id, sprint_id, SUM(story_points) AS velocity_points
FROM wh.fact_task
WHERE status = 'Done'
GROUP BY 1, 2;

-- M25 Average cycle time (days, Done tasks). Min 5 tasks.
CREATE OR REPLACE VIEW m25_avg_cycle_time AS
SELECT team_id, sprint_id, COUNT(*) AS tasks_done,
       ROUND(AVG(date_diff('day', started_date, completed_date)), 2) AS avg_cycle_days
FROM wh.fact_task
WHERE status = 'Done' AND started_date IS NOT NULL AND completed_date IS NOT NULL
GROUP BY 1, 2
HAVING COUNT(*) >= 5;

-- ---------- AI METRICS ---------------------------------------------------
-- M26 Anomaly flag rate, source x month. No minimum.
CREATE OR REPLACE VIEW m26_anomaly_flag_rate AS
SELECT d.month_start_date, a.source_dataset, COUNT(*) AS records_scored,
       SUM(CASE WHEN a.flagged = 'Y' THEN 1 ELSE 0 END) AS records_flagged,
       ROUND(100.0 * SUM(CASE WHEN a.flagged = 'Y' THEN 1 ELSE 0 END) / COUNT(*), 2) AS flag_rate_pct
FROM wh.fact_anomaly a
JOIN wh.dim_date d ON d.date_key = a.period_key
GROUP BY 1, 2;

-- M27 High-risk share by department (never by person). Min 10 employees.
CREATE OR REPLACE VIEW m27_high_risk_share AS
SELECT as_of_date, department_id, COUNT(*) AS employees_scored,
       SUM(CASE WHEN risk_band = 'High' THEN 1 ELSE 0 END) AS high_risk,
       ROUND(100.0 * SUM(CASE WHEN risk_band = 'High' THEN 1 ELSE 0 END) / COUNT(*), 2) AS high_risk_share_pct
FROM wh.fact_attrition_risk
GROUP BY 1, 2
HAVING COUNT(*) >= 10;

-- M28 Forecast error (MAPE) for headcount forecasts vs actuals, department x month.
-- Forecast months (Oct 2026 onwards) have no actuals yet, so actual and error are NULL
-- and status says so. They fill in automatically once actuals arrive. 'ALL' = company total.
CREATE OR REPLACE VIEW m28_forecast_error AS
WITH actual AS (
    SELECT month_start_date, department_id, headcount_end AS actual FROM wh.fact_headcount_monthly
    UNION ALL
    SELECT month_start_date, 'ALL', SUM(headcount_end) FROM wh.fact_headcount_monthly GROUP BY 1
)
SELECT f.forecast_month AS month_start_date, f.department_id, f.forecast_value, a.actual,
       CASE WHEN a.actual IS NULL THEN 'Awaiting actuals' ELSE 'Scored' END AS status,
       ROUND(100.0 * ABS(a.actual - f.forecast_value) / NULLIF(a.actual, 0), 2) AS ape_pct
FROM wh.fact_forecast f
LEFT JOIN actual a ON a.month_start_date = f.forecast_month AND a.department_id = f.department_id
WHERE f.metric = 'headcount_end';

-- ---------- CATALOG ------------------------------------------------------
-- Needs the table ref_metric_dictionary (build_metrics.py loads it from metric_dictionary.csv).
CREATE OR REPLACE VIEW metric_catalog AS
SELECT d.metric_id, d.metric_name, m.view_name, d.business_definition, d.formula,
       d.source_table, d.grain, d.owner_team, d.unit, d.minimum_group_size, d.caveat
FROM ref_metric_dictionary d
JOIN (VALUES
 ('M01','m01_headcount'),('M02','m02_hires'),('M03','m03_exits'),('M04','m04_attrition_monthly'),
 ('M05','m05_attrition_rolling_12m'),('M06','m06_voluntary_attrition_12m'),('M07','m07_gross_pay'),
 ('M08','m08_net_pay'),('M09','m09_payroll_cost'),('M10','m10_cost_per_head'),('M11','m11_overtime_share'),
 ('M12','m12_variable_pay_share'),('M13','m13_attendance_rate'),('M14','m14_late_arrival_rate'),
 ('M15','m15_lop_days'),('M16','m16_revenue'),('M17','m17_net_profit'),('M18','m18_operating_margin'),
 ('M19','m19_payroll_pct_revenue'),('M20','m20_dso'),('M21','m21_overdue_receivables'),
 ('M22','m22_task_completion_rate'),('M23','m23_on_time_delivery'),('M24','m24_velocity'),
 ('M25','m25_avg_cycle_time'),('M26','m26_anomaly_flag_rate'),('M27','m27_high_risk_share'),
 ('M28','m28_forecast_error')
) AS m(metric_id, view_name) ON m.metric_id = d.metric_id
ORDER BY d.metric_id;

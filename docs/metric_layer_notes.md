# Task 4: Governed metric layer, notes

## What was built
- `05_Starter/metric_views.sql`: 28 metric views (`m01_headcount` ... `m28_forecast_error`), a few helper views (`ref_*`), `m04_denominator_compare` and `metric_catalog`.
- `05_Starter/build_metrics.py`: one command that loads the dictionary, builds the views, checks them and prints the evidence.
- Views read from schema `wh` (rows that passed Task 1 checks, with the star-schema keys).

## Decisions (write these in your own words)
1. Payroll exclusions. The payroll views leave out 4 rows: 2 ghost employees (EMP-9001, EMP-9002, `employee_key = -1`, not in dim_employee) and 2 duplicate employee-month rows (PI-X0001, PI-X0002). They inflate cost and do not match the ledger.
2. Department x month, not employee x month. The dictionary lists some payroll metrics at employee grain, but a minimum group size of 5 means a single person may never be shown. So they are department x month.
3. Small-group rule. A group below its minimum is not returned. Group size = people in the department (headcount, employees paid or scored) or tasks in the team-sprint. Metrics with a blank minimum (finance, M24, M26, M28) are not filtered.
4. M28 forecast error. Forecasts start in Oct 2026 and actuals stop in Sep 2026, so no month can be scored yet. The view returns the forecasts with status "Awaiting actuals" and fills in the error when actuals arrive.
5. M09 vs ledger. Payroll cost matches ledger accounts 5001-5005 in every month except Jun 2026, where the ledger is 18,450 higher. This is a finding to report, not a view error.

## Think for yourself: attrition denominator
Chosen: **headcount at the start of the month** for M04 (monthly), and **average headcount** for the 12-month metrics M05 and M06.

Why: the dictionary and the source column `attrition_rate_monthly_pct` both use start headcount, so using the same denominator keeps one definition everywhere. The 12-month metrics use the average because headcount changes a lot over a year.

How the number changes (exits / denominator x 100):

| Dept, month | Exits | Start | End | On start | On end | On average |
|---|---|---|---|---|---|---|
| D01, Mar 2020 | 3 | 58 | 55 | 5.17 | 5.45 | 5.31 |
| D01, Sep 2026 | 2 | 160 | 167 | 1.25 | 1.20 | 1.22 |

Start gives the lowest rate when a department is shrinking and the highest when it is growing. The gap is small for big departments and large for small ones, which is why M04 has a minimum group size of 10.

## Hand-checks (calculator vs view)
| Metric | Inputs | Calculation | Calculator | View | Match |
|---|---|---|---|---|---|
| M04 attrition D01 Sep 2026 | exits 2, start headcount 160 | 2 / 160 x 100 | 1.25 | 1.25 | Yes |
| M10 cost per head D01 Aug 2025 | employer cost 16,923,386.93, 148 people | 16,923,386.93 / 148 | 114,347.21 | 114,347.21 | Yes |
| M13 attendance D01 Aug 2025 | 2618 present, 8 half-days (8 x 0.5 = 4), 2775 scheduled | (2618 + 4) / 2775 | 0.9449 | 0.9449 | Yes |
| M22 completion T02 SP-03 | 48 done, 52 not cancelled | 48 / 52 x 100 | 92.31 | 92.31 | Yes |
| M18 margin Sep 2026 | net profit 14,985,979.13, revenue 80,268,200 | 14,985,979.13 / 80,268,200 x 100 | 18.67 | 18.67 | Yes |

All five calculator results were worked out by hand and equal the view values. 

# Contract notes: 3 tables

These come from the `data_contracts` sheet. I picked three feeds that each have a different kind of promise: a task board with date logic, an AI risk feed with an "active employee" rule, and an AI anomaly feed where one column depends on another.

## 1. fact_task (task board)

| Column | Type | Can it be empty? | Allowed values / rule |
|---|---|---|---|
| task_id | string | no | unique, format TASK-nnnnn |
| team_id | string | no | must exist in dim_team |
| sprint_id | string | no | must exist in dim_sprint |
| assignee_employee_id | string | no | must exist in dim_employee |
| task_type | string | no | Feature, Bug, Chore or Spike |
| priority | string | no | P1 to P4 |
| story_points | int | no | 1, 2, 3, 5, 8 or 13 |
| created_date | date | no | not later than started_date |
| started_date | date | yes | blank only when status is To Do |
| due_date | date | no | equals the sprint end date |
| completed_date | date | yes | required when status is Done, and not before started_date |
| status | string | no | Done, In Progress, Blocked, To Do or Cancelled |
| reopened_count | int | no | 0 or more |

Not in the sheet but checked anyway: `cycle_time_days` can't be negative. It is derived from the two dates, so a negative value means the dates are wrong.

**What it caught:** 12 tasks where completed_date is before started_date (which also gives the negative cycle time) and 8 Done tasks with no completed_date. 20 rows in total.

## 2. fact_attrition_risk (AI attrition prediction)

| Column | Type | Can it be empty? | Allowed values / rule |
|---|---|---|---|
| prediction_id | string | no | unique, format AR-nnnn |
| employee_id | string | no | exists in dim_employee and is active |
| department_id | string | no | exists in dim_department |
| as_of_date | date | no | same for the whole batch |
| risk_score_12m | decimal(4,3) | no | 0 to 1 (probability of leaving within 12 months) |
| risk_band | string | no | Low, Medium or High (High = top 10%, Medium = next 20%) |
| top_reason_1..3 | string | yes | plain English |
| model_version | string | no | text |

**What it caught:** nothing, all 430 rows passed. The employee-is-active rule is checked against the current row in dim_employee.

## 3. fact_anomaly (AI anomaly results)

| Column | Type | Can it be empty? | Allowed values / rule |
|---|---|---|---|
| result_id | string | no | unique, format AN-nnnn |
| source_dataset | string | no | attendance_monthly_features or payroll_monthly_features |
| record_id | string | no | exists in the source dataset |
| employee_id | string | no | exists in dim_employee |
| period | date | no | a month start or month end date |
| anomaly_score | decimal(4,3) | no | 0 to 1, higher is more unusual |
| flagged | char(1) | no | Y or N |
| reason_code | string | no | one of the published codes |
| reason_text | string | no | plain English |
| money_at_risk | decimal(14,2) | yes | blank for attendance rows, filled for payroll rows |
| model_version | string | no | semantic version text |
| scored_at | timestamp | no | UTC |

**What it caught:** nothing, all 405 rows passed. One promise I could not check: "record_id exists in the source dataset", because the payroll source table (fact_payroll) was not in the files I had. It is a known gap, not a pass.

## A note on where these came from

The `data_contracts` sheet only covers four feeds (anomaly, attrition risk, forecast, task). For the other fact tables (attendance, headcount, invoice, invoice payment, expense) and dim_employee / dim_date I wrote contracts myself from the data dictionary and by profiling the data, and `contracts.py` says so. The small lookup tables (dim_team, dim_sprint, dim_role, dim_client, dim_vendor, dim_account, kpi_targets, budget) have no contract yet and pass through unchecked, flagged as `NO CONTRACT` in the summary.

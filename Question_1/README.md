# Annapurna Stores – Data Engineering Exam

## Question 1 – Data Engineering Pipeline

This repository contains the implementation and evidence for Question 1 of the Annapurna Stores data engineering examination.

## 1(a) Object Store and Partitioning

MinIO is used as the object store.

Sales files are partitioned by store, year and month:

```text
data-lake/
└── sales/
    └── store=S01/
        └── year=2024/
            └── month=01/
                └── SALES_S01_20240101.csv
```

Partitioning strategy:

```text
store → year → month
```

Verified result:

```text
12 MiB   653 objects   data-lake/sales
```

This layout allows a query for a particular store and month to target the corresponding partition.

## 1(b) Idempotent Loading

The `sales_stage` table was verified using row count, unique-line, resend and checksum checks.

### Row Count

```text
1,120,924
```

### Unique Lines

```text
rows          = 1,120,924
unique_lines  = 1,120,924
```

### Resend Check

```text
resend_no = 0
rows      = 1,120,924
```

### Checksum

```text
37b1619d4b1d779fd505311086cfe4e4
```

**Evidence note:** The baseline and checksum were verified. Three complete ingestion reruns were not independently demonstrated in the recorded session.

## 1(c) Dimensional Dashboard Tables

The dimensional model contains:

- `dim_store`
- `dim_category`
- `dim_product`
- `fact_sales_final`

Verified dimensions:

```text
Stores              = 12
Categories          = 14
Product versions    = 1,224
Product codes       = 1,200
Product keys        = 1,224
Dates               = 366
```

Verified fact table:

```text
Fact rows           = 766,796
Stores              = 12
Products            = 1,224
Dates               = 366
Total revenue       = ₹52,813,595.13
```

Product surrogate keys and effective dates are used because product codes can be reused after retirement.

## 1(d) Historical Prices

Historical prices are obtained from `price_revisions` using effective dates.

Example product:

```text
Product code: P100621
Product: Catch Coriander Powder 500g
```

### March 15, 2024

```text
MRP            = ₹257.02
Selling price  = ₹229.48
Valid from     = 2024-03-08
Valid to       = 2024-03-22
```

### October 15, 2024

```text
Selling price  = ₹210.70
Valid from     = 2024-03-23
```

The same query structure is used for different reporting periods by changing the reporting date.

## 1(e) Federated Query

DuckDB was used as the analytical engine.

```text
MinIO       → Sales files
PostgreSQL  → Stores, Products, Categories
DuckDB      → Federated joins and aggregation
```

`EXPLAIN ANALYZE` evidence:

```text
HTTPFS input         = 2.3 MiB
HTTP GET requests    = 89
CSV files read       = 44

PostgreSQL:
Stores               = 12 rows
Products             = 1,224 rows
Categories           = 14 rows

HASH_GROUP_BY output = 28 rows
```

The sales data was read directly from MinIO and master data was read directly from PostgreSQL without copying the master tables into the object store.

## 1(f) Finance Reconciliation

Revenue definition from the billing notes:

```text
SALE       → Included
RETURN     → Included
DISCOUNT   → Included
VOID       → Included
TAX        → Excluded
TENDER     → Excluded
```

### Reconciliation Differences

| Month | Pipeline Revenue | Finance Revenue | Difference |
|---|---:|---:|---:|
| March 2024 | ₹41,971,649.09 | ₹42,457,899.09 | -₹486,250.00 |
| July 2024 | ₹40,295,160.11 | ₹40,527,291.81 | -₹232,131.70 |
| October 2024 | ₹56,359,195.92 | ₹56,359,195.92 | ₹0.00 |
| December 2024 | ₹50,745,259.48 | ₹50,745,209.00 | +₹50.48 |

All other months reconciled to zero difference.

### October Result

```text
Pipeline revenue = ₹56,359,195.92
Finance revenue  = ₹56,359,195.92
Difference       = ₹0.00
```

The three non-zero months should be taken to Finance for investigation.

## Repository Contents

```text
billing_notes.md
finance_monthly.csv
load_sales.ps1
masters.sql
sales_manifest.csv
Annapurna_Stores_Q1_All_Screenshots.docx
README.md
```

## Evidence

`Annapurna_Stores_Q1_All_Screenshots.docx` contains screenshot-style input/output evidence for:

- 1(a) Object Store and Partitioning
- 1(b) Idempotent Loading
- 1(c) Dimensional Dashboard Tables
- 1(d) Historical Prices
- 1(e) Federated Query
- 1(f) Finance Reconciliation

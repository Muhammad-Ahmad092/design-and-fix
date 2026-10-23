# Capacity Estimation

Used for the Capacity Estimates section of Mode 1 `01-system-design.md`. Back-of-envelope numbers that size the design; state every input as an assumption.

## Formulas

| Metric | Formula | Default when not stated |
|---|---|---|
| Daily requests | DAU × requests per user per day | — |
| Average QPS | daily requests / 86,400 | — |
| Peak QPS | 3× average QPS | 3× unless the user states a peak |
| Read / write QPS | QPS × read share, QPS × write share | 80% reads / 20% writes |
| Storage per year | writes/day × record size × 365 × replication factor | replication factor 3 |
| Bandwidth | QPS × avg response size (use peak QPS for sizing) | — |
| Cache size (hot set) | 20% of daily hot reads × object size | 80/20 rule: 20% of reads cover most traffic |

- Units are decimal: 1 KB = 1,000 bytes, 1 GB = 1,000 MB.
- Round results to 2–3 significant figures. Show the formula in the table, not just the value.
- Cache size is an upper bound: repeat reads of the same object need no extra memory.

## Worked Example

Inputs: 100k DAU, 20 requests/user/day, 80/20 read/write, record 1 KB, replication 3, avg response 10 KB, cached object 1 KB.

| Metric | Formula | Value |
|---|---|---|
| Daily requests | 100,000 × 20 | 2,000,000 |
| Average QPS | 2,000,000 / 86,400 | ≈ 23.1 |
| Peak QPS | 3 × 23.1 | ≈ 69 |
| Read / write QPS (peak) | 69 × 0.8 / 69 × 0.2 | ≈ 55 / 14 |
| Storage per year | 400,000 writes/day × 1 KB × 365 × 3 | ≈ 438 GB |
| Bandwidth (peak) | 69 × 10 KB | ≈ 0.69 MB/s (≈ 5.5 Mbit/s) |
| Cache size | 20% × 1,600,000 reads/day × 1 KB | ≈ 320 MB |

Reading: one PostgreSQL primary with a read replica and a single small Redis instance cover this load; the design does not need sharding.

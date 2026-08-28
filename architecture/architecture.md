# Retail Data Platform - Architecture

An online retailer receives customer, product and order feeds daily. This is the
platform that lands, catalogs, cleans, validates, governs and serves them.

## What is here

| | |
| --- | --- |
| `retail-data-platform.drawio` | editable source. Two tabs: **Architecture**, **Terraform Layers** |
| `architecture-diagram.png` | export of the Architecture tab, embedded in the README and required by the brief (section 8) |
| Mermaid blocks below | the Terraform layer split and the star schema, rendered inline by GitHub |

**The star schema has no draw.io tab.** It had one, and it would not open -
draw.io failed with `d.setId is not a function` on that tab alone, while the
other two loaded normally. Two rebuild attempts did not fix it, so the tab was
removed rather than committed broken: a diagram that errors when a reviewer
clicks it is worse than one that lives in Mermaid. The Mermaid `erDiagram`
below carries the same content and renders on GitHub with no tooling. This is
recorded in `docs/troubleshooting.md` as unresolved.

**Re-exporting.** PNG export in draw.io is **per-tab** - it renders whichever
tab you are viewing, and there is no "all pages" option (only PDF has that).
Select the Architecture tab, then *File -> Export as -> PNG*, Zoom 200%,
Border 10, transparent off, saving over `architecture-diagram.png`.

**Editing.** Open the `.drawio` at [app.diagrams.net](https://app.diagrams.net),
the desktop app, or the *Draw.io Integration* VS Code extension. The format is
XML, so a change is a readable diff rather than an opaque binary blob.

---

## Target platform

![Target platform architecture](architecture-diagram.png)

Out of scope, and labelled "Optional Advanced Exercise" by the brief itself:
Apache Iceberg, CDC/DMS, cross-account Lake Formation. Streaming is deferred.

---

## Terraform layers

Split by lifecycle, not by service - this is what makes a routine
`terraform destroy` safe to run every session.

```mermaid
flowchart LR
    SB["<b>State bucket</b><br/><i>created by the CLI, not Terraform</i>"]

    subgraph P["persistent/ - NEVER destroyed - ~$1/month"]
        direction TB
        P1["KMS CMK + alias + policy"]
        P2["S3 lake bucket<br/><i>versioned, SSE-KMS, TLS-only</i>"]
        P3["IAM: Glue + Redshift roles"]
        P4["IAM: 3 Lake Formation personas"]
        P5["Budgets: $5 daily, $50 monthly"]
    end

    subgraph T["training/ - destroyed every session - $0 idle"]
        direction TB
        T1["Glue Catalog database<br/><i>training_db</i>"]
        T2["Athena workgroup<br/><i>1 GiB scan ceiling</i>"]
        T3["CloudWatch log group"]
        T4["<i>later: crawlers, jobs, DQ,<br/>Redshift, Step Functions, SNS</i>"]
    end

    SB -.->|holds state| P
    SB -.->|holds state| T
    P -->|terraform_remote_state<br/>reads outputs| T
```

The training layer never creates buckets, keys or roles - it only reads them
from the persistent layer's outputs. That is precisely why destroying it is a
routine operation rather than a risky one.

---

## Star schema (Phase 7)

```mermaid
erDiagram
    dim_customer ||--o{ fact_orders : "customer_id"
    dim_product  ||--o{ fact_orders : "product_id"
    dim_date     ||--o{ fact_orders : "date_key"

    dim_customer {
        string customer_id PK
        string customer_name
        string email "restricted by Lake Formation"
        string country
        date   created_date
    }
    dim_product {
        string product_id PK
        string product_name
        string category
        double price
    }
    dim_date {
        int  date_key PK
        int  year
        int  month
        int  day
        bool is_weekend
    }
    fact_orders {
        string order_id PK
        string customer_id FK
        string product_id FK
        int    date_key FK
        int    quantity
        double order_total "quantity x price"
        string status
    }
```

**Distribution and sort.** `fact_orders` takes `DISTKEY(customer_id)` and
`SORTKEY(date_key)`; `dim_customer` shares the same distribution key so the most
common join is local rather than a broadcast. `dim_product` and `dim_date` are
small enough for `DISTSTYLE ALL`, replicated to every node.

The query this shape exists to answer:

```sql
SELECT c.country, SUM(o.order_total)
FROM fact_orders o
JOIN dim_customer c ON o.customer_id = c.customer_id
GROUP BY c.country;
```

No join is needed to reach `country` - it is denormalised into the dimension,
which is exactly how a warehouse model differs from the source model it came
from.

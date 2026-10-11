# sqlite_extract — Mason store's data pipeline

The Mason store runs the LifeSaver **desktop** app (Visual FoxPro tables), not
Lifesaver Cloud. Its history (June 2015 on) can't be scraped. Instead, the owner
uploads a copy of their LifeSaver folder, and this package turns it into:

- **`lifesaver.sqlite`** in `gs://mcps-507817-lifesaver-data/`: clean tables and
  views over the POS data. This is the live dataset for Mason's SQL MCP server and
  web report builder ([`mason_reports/`](../mason_reports/)). Both re-read it within
  10 minutes of a publish.
- **`mason.db`**: the same `core` KPI tables the main store has (`visits`,
  `customer_lifecycle`, `kpi_monthly`). These render the dashboard's Mason tab
  (`mason.html`).

```
owner's Drive upload ─▶ drive.py ─▶ extract.py ─▶ ingest.py checks ─▶ gs://…/lifesaver.sqlite
 (zip or folder)        (download    (9 .dbf/.fpt    (not older/smaller       │
                         newest)      → SQLite)       than live)              ▼
                                                     dashboard/publish.sh: importer.py ─▶ mason.db ─▶ mason.html
```

No lsscloud.com login and no raw/line-item layers: the extract *is* the raw
layer, and both outputs are rebuilt from scratch on every run.

## Modules

| File | Role |
|---|---|
| `drive.py` | Lists the shared Drive folder (recursively, including shared drives). It picks the newest **upload**, which is either any zip under the folder or any folder that holds `ticket.dbf` plus the other needed files. Downloads only those files from a folder upload. A zip is downloaded whole, because Drive can't look inside it. |
| `extract.py` | `.dbf`/`.fpt` files (a folder, or a zip) → `lifesaver.sqlite`. Of the 8k+ files in a LifeSaver install, only the 9 in `extract.NEEDED` are read. A zip is never unpacked wholesale; only those members are copied out, under flat lowercase names. |
| `ingest.py` | The two steps `ingest.sh` calls: `check` (is there a new, finished upload?) and `run` (download → extract → compare with live → write `state.json`). |
| `ingest.sh` | Wraps `ingest.py` with the GCS reads/writes. This is what [`mason-ingest.yml`](../.github/workflows/mason-ingest.yml) runs every 6 hours. |
| `importer.py` | `lifesaver.sqlite` → `mason.db` through the shared `core.visits.build_visits` + `core.kpis.compute_kpis`. [`dashboard/publish.sh`](../dashboard/publish.sh) runs it on every publish. |

## The ingest, step by step

1. **Find.** Take the newest upload under the Drive folder. An upload's time
   is the later of each file's `modifiedTime` and `createdTime`. Browser uploads
   can keep the file's old mtime, so `createdTime` is when it reached Drive.
2. **Wait until it settles.** If anything in it changed in the last 30 minutes
   (`ingest.SETTLE`), it may still be uploading. Exit and retry next run.
3. **Skip if already seen.** Each upload has a content key, a hash of its
   files' names + Drive MD5s. If the key matches `state.json`'s last key,
   nothing happens, whatever the last outcome was. Re-uploading the same bytes
   doesn't retrigger; changing any needed file does.
4. **Build.** Download → keep only the needed files (`raw/`) → `extract()` →
   `new.sqlite`. If the upload isn't usable (not a zip, no `ticket.dbf`, a
   missing table, or a corrupt file), the result is **rejected**.
5. **Compare with live.** Reject if, compared with the current
   `lifesaver.sqlite`, there are fewer work orders or fewer customers, or the
   latest order date is earlier. Also reject if there are no work orders at all.
   This stops an old backup or a partial copy from replacing good data.
6. **Publish** (`ingest.sh`): source tables → `raw/<ts>/`, current live copy →
   `archive/lifesaver-<ts>.sqlite`, new file → `lifesaver.sqlite`. Then the
   workflow runs `dashboard/publish.sh` to re-render the Mason tab.
7. **Record.** `ingest/state.json` is written last, so a failed GCS publish
   leaves the old state and the next run retries.

Exit status and what the workflow run shows:

| Situation | Run | `state.json` |
|---|---|---|
| no upload / nothing new / still uploading | ✅ green, dashboard step skipped | unchanged |
| published | ✅ green, dashboard republished | `outcome: published` |
| rejected (bad upload or failed checks) | ❌ **red once**, then green on later runs (same key is skipped) | `outcome: rejected`, `detail` says why |
| Drive/network/GCS error | ❌ red | unchanged, so the next run retries |

## Operating it

Setup (Drive API, bucket IAM, sharing the folder, the `MASON_DRIVE_FOLDER_ID`
repo variable) is in [`cloud/DEPLOY.md`](../cloud/DEPLOY.md) §8.

**What the owner does:** drag the LifeSaver folder, or a zip of it, into the
shared Drive folder. Old uploads can stay; the newest one wins. Don't keep
other zips in that folder. Any zip counts as an upload, so a newer unrelated
zip gets picked up and rejected.

**What happened last?**

```bash
gcloud storage cat gs://mcps-507817-lifesaver-data/ingest/state.json
```

`metrics` has the work-order/customer counts, the data-through date, and the
row count per table for the last upload processed.

**An upload was rejected but it's right** (e.g. the owner purged old work
orders, so the counts legitimately dropped): **Actions → mason data from drive →
Run workflow** with **force** ticked. This reprocesses the newest upload and
publishes it despite the checks.

**Roll back to an earlier dataset:**

```bash
B=gs://mcps-507817-lifesaver-data
gcloud storage ls $B/archive/
gcloud storage cp $B/archive/lifesaver-<ts>.sqlite $B/lifesaver.sqlite
```

Then **Actions → refresh dashboard data → Run workflow** to re-render the
Mason tab. Its `publish.sh` step rebuilds both pages. (**build & deploy**
won't work here: its dashboard job only runs on a push.) The SQL MCP server picks up the change by itself. The next upload
the owner makes is still compared against whatever is live.

**Run locally** (needs permission to impersonate `gha-deployer`): see the end
of DEPLOY.md §8. Or skip Drive and build from a file you have:

```bash
.venv/bin/python -m sqlite_extract.extract ~/Downloads/LifeSaver.zip lifesaver.sqlite
.venv/bin/python -m sqlite_extract.importer lifesaver.sqlite mason.db
```

## `lifesaver.sqlite` data model

Built by `extract.extract()`. Column names are LifeSaver's own, lowercased (e.g.
`fwkorderno`, `fcustno`), and blank strings become `NULL`.

| Table / view | Source | Grain / notes |
|---|---|---|
| `work_order_revisions` | `ticket.dbf` + `.fpt` | every save of a work order is a new row (`frevision` 1, 2, …); adds `is_latest`, `rev_count`, and the finished/opening size parsed from the `FULFIELDS` memo |
| `work_order_components` | `ticket` `FULFIELDS` memo | one row per component per revision: `kind` (Moulding, Mat, Fillet, Glazing, Mounting, Stretching, Special, …), name, vendor, feet, qty, list price, cost, discount |
| `customers` | `lifecust.dbf` | keyed by `cnumber` |
| `invoices`, `invoice_lines` | `billsum.dbf`, `bill.dbf` | |
| `payments`, `payment_detail` | `payment.dbf`, `paydetail.dbf` | card-processor fields (`PYMX*`, `PYEV*`, `PYCCACCTNM`) are dropped |
| `mouldings`, `mats` | `moulding.dbf`, `mat.dbf` | catalog |
| view `work_orders` | | latest revision of each work order |
| view **`wo_facts`** | | **start here**: one row per work order with readable names: salesperson initials normalized (`'ECP - Christ'` → `ECP`), customer name from the customer file, price breakdown, `known_cost`, `days_to_done`, `customer_type` New/Returning |
| view `comp_facts` | | `wo_facts` × the current revision's real components (zero-value placeholder rows and `ArtCondition` removed) |

Caveats:

- **Cost is only real for moulding, mat and fillet.** For glazing, mounting,
  stretching and specials, LifeSaver stores the list price in the cost field.
  So `known_cost` / `comp_cost` cover only those three kinds, and margins can only
  be computed for them.
- **Revenue** in the KPIs is `wo_facts.subtotal`: pre-tax and net of discount.
  This is the same "Sales" figure the SQL MCP server reports.
- **Customer identity** is LifeSaver's customer number, so no name matching is
  needed (unlike the main store's `cloud/warehouse/identity.py`).
- **"Today"** for the KPIs is the extract's latest order date, not the wall clock.
  The data is a snapshot, so the month it ends in is treated as the open month.
- The file holds customer names, phones and addresses. Keep it out of git
  (`*.sqlite`, `*.zip`, `*.dbf` and `Extractor/` are all gitignored) and keep the
  bucket private.

## Tests

[`../tests/sqlite_extract/`](../tests/sqlite_extract/), offline. `test_ingest.py`
builds tiny `.dbf` files and fakes the Drive API. It covers the folder and zip
uploads, the settle window, content keys, the older/smaller checks, `--force`,
and corrupt uploads. `test_sqlite_import.py` covers the KPI import.

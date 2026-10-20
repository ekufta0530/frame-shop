"""The views over an extracted lifesaver.sqlite (``wo_facts``, ``comp_facts``, ...).

Apart from extract.py so the website can re-apply them to a published copy without dbfread,
which only the extractor needs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

VIEWS = """
DROP VIEW IF EXISTS work_orders;
CREATE VIEW work_orders AS
  SELECT * FROM work_order_revisions WHERE is_latest = 1;
DROP VIEW IF EXISTS work_order_components_current;
CREATE VIEW work_order_components_current AS
  SELECT c.* FROM work_order_components c
  JOIN work_orders w ON w.fwkorderno = c.wo_no AND w.frevision = c.revision;
DROP VIEW IF EXISTS wo_facts;
CREATE VIEW wo_facts AS
  SELECT w.fwkorderno AS wo_no, w.finvoiceno AS invoice_no, w.fcustno AS cust_no,
    w.fdate AS order_date, w.fdatepr AS promised_date, w.fdonedate AS done_date,
    w.fdelivdate AS delivered_date, w.fstatustxt AS status,
    -- salesperson is free text ('ECP', 'ECP - Christ', 'bc - bernie'); keep the initials
    CASE WHEN trim(coalesce(w.fsp, '')) = '' THEN '(none)'
         ELSE upper(trim(CASE WHEN instr(w.fsp, ' - ') > 0
                              THEN substr(w.fsp, 1, instr(w.fsp, ' - ') - 1) ELSE w.fsp END)) END AS salesperson,
    -- account name from the customer file; the ticket's name is just the contact person
    coalesce(nullif(trim(coalesce(c.fname, '') || ' ' || coalesce(c.lname, '')), ''), c.company,
             trim(coalesce(w.ffname, '') || ' ' || coalesce(w.flname, ''))) AS customer_name,
    coalesce(c.company, w.fcompany) AS company, c.city AS customer_city, c.zip5 AS customer_zip,
    w.fdesc AS description, w.fcomment AS comment,
    w.fsubt AS subtotal, w.ftax AS tax, w.ftotal AS total,
    -- the discount is what the ticket actually came off list: fdiscamt often disagrees with it
    -- (WO 12486: list 348.43, sold 250, fdiscamt 492.15); work_orders.fdiscamt keeps the raw value
    w.flistsubt AS list_subtotal, round(w.flistsubt - w.fsubt, 2) AS discount_amt, w.fdisc AS discount_code,
    w.fmouldpr AS moulding_price, w.fmatspr AS mat_price, w.fglasspr AS glass_price,
    w.fmountpr AS mount_price, w.flaborpr AS labor_price, w.fspecialpr AS special_price,
    -- LifeSaver only records true cost for moulding/mat/fillet; glazing, specials etc. store list price
    -- as "cost" (and the ticket-level cost fields inherit that), so only these three are trusted.
    coalesce(k.known_cost, 0) AS known_cost, coalesce(k.known_net, 0) AS known_cost_sales,
    w.finished_w, w.finished_h, w.rev_count,
    julianday(w.fdonedate) - julianday(w.fdate) AS days_to_done,
    CASE WHEN w.fdate = min(w.fdate) OVER (PARTITION BY w.fcustno) THEN 'New' ELSE 'Returning' END
      AS customer_type
  FROM work_orders w
  LEFT JOIN customers c ON c.cnumber = w.fcustno
  LEFT JOIN (SELECT wo_no, SUM(cost) AS known_cost, SUM(list_price - coalesce(discount, 0)) AS known_net
             FROM work_order_components_current WHERE kind IN ('Moulding', 'Mat', 'Fillet')
             GROUP BY wo_no) k ON k.wo_no = w.fwkorderno;
DROP VIEW IF EXISTS comp_facts;
CREATE VIEW comp_facts AS
  SELECT f.*, c.kind AS comp_kind, c.name AS comp_name, coalesce(c.vendor, '(none)') AS comp_vendor,
    c.feet AS comp_feet, c.quantity AS comp_qty, c.list_price AS comp_list_price,
    CASE WHEN c.kind IN ('Moulding', 'Mat', 'Fillet') THEN c.cost END AS comp_cost,
    c.list_price - coalesce(c.discount, 0) AS comp_net_price
  FROM work_order_components_current c JOIN wo_facts f ON f.wo_no = c.wo_no
  -- Glazing/Mounting/Stretching/ArtCondition rows exist on every WO even when unused
  WHERE c.kind != 'ArtCondition' AND coalesce(c.list_price, 0) + coalesce(c.cost, 0) > 0;
CREATE INDEX IF NOT EXISTS ix_rev_wo ON work_order_revisions(fwkorderno, frevision);
CREATE INDEX IF NOT EXISTS ix_comp_wo ON work_order_components(wo_no, revision);
"""


def refresh_views(db: str | Path) -> None:
    """Re-create the views in an already-extracted ``db``, so a fix to them reaches a copy that was
    published before it (the site applies this to its download of the owner's latest upload)."""
    con = sqlite3.connect(db)
    try:
        con.executescript(VIEWS)
        con.commit()
    finally:
        con.close()

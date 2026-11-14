"""SQLite-opslag voor prijshistorie, eigen prijswijzigingen en voorstellen."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observaties (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tijdstip      TEXT NOT NULL,          -- ISO 8601 UTC
    sku           TEXT NOT NULL,
    concurrent    TEXT NOT NULL,
    url           TEXT,
    prijs_ruw     REAL,                   -- zoals op de site
    prijs_incl    REAL,                   -- genormaliseerd: incl. btw, per eigen eenheid
    adviesprijs   REAL,                   -- doorgestreepte 'van'-prijs indien gevonden
    op_voorraad   INTEGER,                -- 1/0/NULL (onbekend)
    status        TEXT NOT NULL,          -- ok | geen_prijs | fout | robots
    melding       TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_sku ON observaties (sku, concurrent, tijdstip);

CREATE TABLE IF NOT EXISTS voorstellen (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    datum            TEXT NOT NULL,
    sku              TEXT NOT NULL,
    huidige_prijs    REAL NOT NULL,
    voorgestelde_prijs REAL NOT NULL,
    richting         TEXT NOT NULL,       -- omhoog | omlaag
    score            REAL,
    brutowinst_delta_week REAL,
    reden            TEXT,
    status           TEXT NOT NULL DEFAULT 'open'   -- open | akkoord | afgewezen | doorgevoerd
);
CREATE INDEX IF NOT EXISTS ix_vs ON voorstellen (sku, datum);

CREATE TABLE IF NOT EXISTS eigen_prijzen (
    tijdstip  TEXT NOT NULL,
    sku       TEXT NOT NULL,
    prijs     REAL NOT NULL,
    bron      TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def nu() -> str:
    return utcnow().replace(microsecond=0).isoformat()


class DB:
    def __init__(self, pad: str | Path = "data/prijzen.db"):
        Path(pad).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(pad))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.close()

    # --- observaties ---------------------------------------------------
    def voeg_observatie_toe(self, *, sku, concurrent, url, prijs_ruw, prijs_incl,
                            adviesprijs=None, op_voorraad=None, status="ok",
                            melding=None, tijdstip=None):
        self.con.execute(
            "INSERT INTO observaties (tijdstip, sku, concurrent, url, prijs_ruw, prijs_incl,"
            " adviesprijs, op_voorraad, status, melding) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tijdstip or nu(), sku, concurrent, url, prijs_ruw, prijs_incl, adviesprijs,
             None if op_voorraad is None else int(op_voorraad), status, melding),
        )

    def commit(self):
        self.con.commit()

    def laatste_observaties(self, sku: str, max_leeftijd_uur: float, referentie: datetime | None = None):
        """Meest recente geldige observatie per concurrent voor een sku."""
        ref = referentie or utcnow()
        grens = (ref - timedelta(hours=max_leeftijd_uur)).isoformat()
        rows = self.con.execute(
            """SELECT o.* FROM observaties o
               JOIN (SELECT concurrent, MAX(tijdstip) t FROM observaties
                     WHERE sku=? AND status='ok' AND tijdstip>=? AND tijdstip<=?
                     GROUP BY concurrent) m
               ON o.concurrent=m.concurrent AND o.tijdstip=m.t
               WHERE o.sku=? AND o.status='ok'""",
            (sku, grens, ref.isoformat(), sku),
        ).fetchall()
        return [dict(r) for r in rows]

    def historie(self, sku: str, concurrent: str, dagen: int, referentie: datetime | None = None):
        ref = referentie or utcnow()
        grens = (ref - timedelta(days=dagen)).isoformat()
        rows = self.con.execute(
            "SELECT tijdstip, prijs_incl FROM observaties WHERE sku=? AND concurrent=? "
            "AND status='ok' AND tijdstip>=? AND tijdstip<=? ORDER BY tijdstip",
            (sku, concurrent, grens, ref.isoformat()),
        ).fetchall()
        return [(r["tijdstip"], r["prijs_incl"]) for r in rows]

    def scrape_status(self, sinds: str):
        return [dict(r) for r in self.con.execute(
            "SELECT concurrent, status, COUNT(*) n FROM observaties WHERE tijdstip>=? "
            "GROUP BY concurrent, status ORDER BY concurrent", (sinds,)).fetchall()]

    # --- eigen prijzen & voorstellen ----------------------------------
    def laatste_wijziging(self, sku: str) -> str | None:
        r = self.con.execute(
            "SELECT MAX(datum) d FROM voorstellen WHERE sku=? AND status IN ('open','akkoord','doorgevoerd')",
            (sku,)).fetchone()
        return r["d"] if r else None

    def bewaar_voorstel(self, datum: str, v) -> int:
        cur = self.con.execute(
            "INSERT INTO voorstellen (datum, sku, huidige_prijs, voorgestelde_prijs, richting, score,"
            " brutowinst_delta_week, reden) VALUES (?,?,?,?,?,?,?,?)",
            (datum, v.sku, v.huidige_prijs, v.voorgestelde_prijs, v.richting, v.score,
             v.brutowinst_delta_week, v.reden),
        )
        return cur.lastrowid

    def verwijder_open_voorstellen(self, datum: str):
        self.con.execute("DELETE FROM voorstellen WHERE datum=? AND status='open'", (datum,))

    def zet_status(self, voorstel_id: int, status: str):
        self.con.execute("UPDATE voorstellen SET status=? WHERE id=?", (status, voorstel_id))

    def voorstellen(self, datum: str | None = None, status: str | None = None):
        q, p = "SELECT * FROM voorstellen WHERE 1=1", []
        if datum:
            q += " AND datum=?"; p.append(datum)
        if status:
            q += " AND status=?"; p.append(status)
        return [dict(r) for r in self.con.execute(q + " ORDER BY score DESC", p).fetchall()]

    def registreer_eigen_prijs(self, sku: str, prijs: float, bron: str = "handmatig"):
        self.con.execute("INSERT INTO eigen_prijzen VALUES (?,?,?,?)", (nu(), sku, prijs, bron))

"""Utility condivise: HTTP, database SQLite, parsing date italiane, testo da PDF."""
from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import pdfplumber
import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dati" / "riserve.sqlite"
PDF_DIR = ROOT / "dati" / "pdf"

_session = requests.Session()
_session.headers["User-Agent"] = "riserve-idriche-veneto/0.1 (raccolta dati open)"



def get(url: str, *, timeout: int = 60, tentativi: int = 3, headers: dict | None = None) -> requests.Response:
    for i in range(tentativi):
        try:
            r = _session.get(url, timeout=timeout, headers=headers)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tentativi - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError("unreachable")


SCHEMA = """
CREATE TABLE IF NOT EXISTS stazioni (
    id          TEXT PRIMARY KEY,      -- '<rete>:<codice>' es. 'idro:6', 'falda:300003360'
    rete        TEXT NOT NULL,         -- 'idro' | 'falda'
    nome        TEXT,
    lat         REAL,
    lon         REAL,
    quota       REAL,
    provincia   TEXT,
    comune      TEXT,
    aggiornata  TEXT
);
CREATE TABLE IF NOT EXISTS misure (
    stazione_id TEXT NOT NULL,
    sensore     TEXT NOT NULL,         -- es. LIVIDRO, PORTATA, LFREAT, PRECCUM
    istante     TEXT NOT NULL,         -- ISO 8601
    valore      REAL,
    unita       TEXT,
    PRIMARY KEY (stazione_id, sensore, istante)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS invasi (
    data            TEXT NOT NULL,     -- data di riferimento del dato (YYYY-MM-DD)
    bacino          TEXT NOT NULL,
    invaso          TEXT NOT NULL,     -- nome invaso, oppure 'TOTALE'
    volume_utile    REAL,              -- milioni di m3 (capacita' utile di regolazione)
    volume_invasato REAL,              -- milioni di m3
    perc            REAL,              -- % di riempimento
    fonte_url       TEXT,
    PRIMARY KEY (data, bacino, invaso)
);
CREATE TABLE IF NOT EXISTS severita (
    data        TEXT NOT NULL,         -- data seduta / bollettino
    area        TEXT NOT NULL,
    comparto    TEXT NOT NULL,         -- 'superficiali' | 'sotterranee' | 'entrambi'
    classe      TEXT NOT NULL,         -- nulla | bassa | media | alta
    fonte_url   TEXT,
    PRIMARY KEY (data, area, comparto)
);
CREATE TABLE IF NOT EXISTS bollettini_arpav (
    numero              INTEGER PRIMARY KEY,
    data                TEXT,
    url                 TEXT,
    precip_mm           REAL,
    precip_media_mm     REAL,
    precip_scarto_pct   REAL,
    swe_piave_mmc       REAL,          -- risorsa idrica nivale (milioni m3), valore medio del range
    swe_cordevole_mmc   REAL,
    swe_brenta_mmc      REAL,
    sspi                REAL,
    serbatoi_piave_mmc  REAL,
    serbatoi_piave_pct  REAL,
    corlo_mmc           REAL,
    corlo_pct           REAL
);
CREATE TABLE IF NOT EXISTS bollettini_sezioni (
    numero  INTEGER NOT NULL,
    sezione TEXT NOT NULL,             -- Precipitazioni, Riserve nivali, Lago di Garda, Serbatoi, Falda, Portate...
    testo   TEXT,
    PRIMARY KEY (numero, sezione)
);
CREATE TABLE IF NOT EXISTS documenti (
    url         TEXT PRIMARY KEY,
    tipo        TEXT,
    sha256      TEXT,
    elaborato   TEXT,                  -- timestamp elaborazione
    esito       TEXT
);
CREATE TABLE IF NOT EXISTS raccolte (
    job             TEXT PRIMARY KEY,  -- fiumi | falde | invasi | bollettini
    ultimo_avvio    TEXT,              -- ISO, ora locale con offset
    ultima_fine     TEXT,
    esito           TEXT,              -- ok | invariato | errore
    messaggio       TEXT,
    ultimo_ok       TEXT               -- fine dell'ultima raccolta riuscita
);
CREATE TABLE IF NOT EXISTS meta (
    chiave  TEXT PRIMARY KEY,
    valore  TEXT
);
CREATE INDEX IF NOT EXISTS ix_misure_istante ON misure(istante);
"""

def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con

def segna_documento(con, url, tipo, contenuto: bytes | None, esito: str):
    sha = hashlib.sha256(contenuto).hexdigest() if contenuto else None
    con.execute(
        "INSERT OR REPLACE INTO documenti VALUES (?,?,?,datetime('now'),?)",
        (url, tipo, sha, esito),
    )

def scarica_pdf(url: str) -> bytes:
    """Scarica un PDF e ne tiene una copia locale in dati/pdf/."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    nome = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])[-150:]
    if not nome.lower().endswith(".pdf"):
        nome += ".pdf"
    path = PDF_DIR / nome
    if path.exists():
        return path.read_bytes()
    dati = get(url, timeout=120).content
    if not dati.startswith(b"%PDF"):
        raise ValueError(f"non e' un PDF: {url}")
    path.write_bytes(dati)
    return dati

def testo_pdf(dati: bytes) -> str:
    with pdfplumber.open(io.BytesIO(dati)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)

MESI = {m: i for i, m in enumerate(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split(), 1)}

def data_italiana(s: str) -> str | None:
    """'10 luglio 2026' / '22/09/2026' / '23092026' -> '2026-07-10'."""
    m = re.search(r"(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})", s)
    if m and m.group(2).lower() in MESI:
        return date(int(m.group(3)), MESI[m.group(2).lower()], int(m.group(1))).isoformat()
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    return None

def num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def adesso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def meta_get(con, chiave: str) -> str | None:
    r = con.execute("SELECT valore FROM meta WHERE chiave=?", (chiave,)).fetchone()
    return r[0] if r else None


def meta_set(con, chiave: str, valore: str | None):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (chiave, valore))


def esegui(job: str, fn) -> tuple[str, str]:
    """Esegue una raccolta registrandone avvio/fine/esito nella tabella `raccolte`.
    `fn` ritorna un int (righe/documenti nuovi) oppure la stringa 'invariato'."""
    con = db()
    with con:
        con.execute("INSERT INTO raccolte (job, ultimo_avvio) VALUES (?, ?) "
                    "ON CONFLICT(job) DO UPDATE SET ultimo_avvio=excluded.ultimo_avvio", (job, adesso()))
    try:
        r = fn()
        esito, msg = ("invariato", "fonte non aggiornata dall'ultimo controllo") if r == "invariato"             else ("ok", f"{r} nuovi elementi")
    except Exception as e:
        esito, msg = "errore", f"{type(e).__name__}: {e}"
    fine = adesso()
    with con:
        con.execute("UPDATE raccolte SET ultima_fine=?, esito=?, messaggio=?, "
                    "ultimo_ok=CASE WHEN ?<>'errore' THEN ? ELSE ultimo_ok END WHERE job=?",
                    (fine, esito, msg, esito, fine, job))
    con.close()
    if esito == "errore":
        log(f"{job}: ERRORE {msg}")
    return esito, msg


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

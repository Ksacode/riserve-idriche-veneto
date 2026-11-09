"""Bollettino mensile ARPAV 'Risorsa idrica' (PDF, esce a fine/inizio mese).

Contiene: precipitazioni, SPI, riserve nivali (SWE per bacino, indice SSPI), Lago di Garda,
serbatoi Piave e Corlo, falda, portate. I numeri sono estratti dal testo con espressioni
regolari (best effort): il testo completo di ogni sezione viene comunque salvato.
"""
from __future__ import annotations

import re
from datetime import date

from .comune import db, get, log, num, scarica_pdf, segna_documento, testo_pdf

INDICE = "https://www.arpa.veneto.it/temi-ambientali/idrologia/bollettini-risorsa-idrica"
BASE = "https://www.arpa.veneto.it"
SEZIONI = ["Sintesi della situazione", "Precipitazioni", "Indice SPI", "Riserve nivali", "Lago di Garda",
           "Serbatoi", "Falda", "Portate"]
LINK = re.compile(r'href="([^"]*bollettino-n-(\d+)-del-(\d+)-([a-z]+)-(\d{4})\.pdf/@@download/file)"')

_RUMORE = re.compile(
    r"^(Bollettino risorsa idrica.*|\d{1,2} \w+ \d{4} N\. \d+|www\.arpa\.veneto\.it|Dipartimento R ?egionale.*|"
    r"la Sicurezza del Territorio.*|Via F\. Tomea.*|\d{1,2})$")


def pulisci(testo: str) -> str:
    return "\n".join(l for l in testo.splitlines() if not _RUMORE.match(l.strip()))


def sezioni(testo: str) -> dict[str, str]:
    pos = []
    for s in SEZIONI:
        m = re.search(rf"^{re.escape(s)}\b", testo, re.M)
        if m:
            pos.append((m.start(), s))
    pos.sort()
    out = {}
    for k, (inizio, nome) in enumerate(pos):
        fine = pos[k + 1][0] if k + 1 < len(pos) else len(testo)
        out[nome] = re.sub(r"\s+", " ", testo[inizio + len(nome):fine]).strip()
    return out


def _media_range(a: str, b: str | None) -> float | None:
    x, y = num(a), num(b) if b else None
    return x if y is None else (x + y) / 2


def estrai(sez: dict[str, str]) -> dict:
    r: dict = {}
    p = sez.get("Precipitazioni", "")
    if m := re.search(r"caduti mediamente in Veneto (?:circa )?(\d+(?:[.,]\d+)?) mm", p):
        r["precip_mm"] = num(m[1])
    if m := re.search(r"media del periodo \d{4}-\d{4} . di (\d+(?:[.,]\d+)?) mm", p):
        r["precip_media_mm"] = num(m[1])
    if m := re.search(r"\(([-+]?\d+)%\)", p):
        r["precip_scarto_pct"] = num(m[1])

    # SWE: la frase cambia ogni mese. Due forme:
    #   '125 Mm3 nel bacino del Piave'  oppure  'nel Bacino del Piave ... valutata 182-186 Mm3'
    n = sez.get("Riserve nivali", "")
    chiavi = {"piave": "swe_piave_mmc", "cordevole": "swe_cordevole_mmc", "brenta": "swe_brenta_mmc"}
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+(?:[.,]\d+)?))?\s*Mm3", n):
        dopo = re.match(r"\s*(?:nel|sul)\s+bacino\s+del\s+(Piave|Cordevole|Brenta)", n[m.end():], re.I)
        prima = re.findall(r"(Piave|Cordevole|Brenta)", n[max(0, m.start() - 70):m.start()], re.I)
        bacino = (dopo[1] if dopo else (prima[-1] if prima else "")).lower()
        if bacino in chiavi and chiavi[bacino] not in r:
            r[chiavi[bacino]] = _media_range(m[1], m[2])
    if re.search(r"(SWE|risorsa idrica nivale)[^.]*assente", n, re.I) and "swe_piave_mmc" not in r:
        r["swe_piave_mmc"] = r["swe_cordevole_mmc"] = r["swe_brenta_mmc"] = 0.0
    if m := re.search(r"SSPI.*?\(([-+]?\d+[.,]\d+)\)", n):
        r["sspi"] = num(m[1])

    s = sez.get("Serbatoi", "")
    if m := re.search(r"volume totale[^.]*? di (\d+(?:[.,]\d+)?)\s*Mm3.*?(\d+)% di riempimento", s):
        r["serbatoi_piave_mmc"], r["serbatoi_piave_pct"] = num(m[1]), num(m[2])
    if m := re.search(r"Corlo.*?pari a (\d+(?:[.,]\d+)?)\s*Mm3.*?(\d+)% di riempimento", s):
        r["corlo_mmc"], r["corlo_pct"] = num(m[1]), num(m[2])
    return r


COLONNE = ["precip_mm", "precip_media_mm", "precip_scarto_pct", "swe_piave_mmc", "swe_cordevole_mmc",
           "swe_brenta_mmc", "sspi", "serbatoi_piave_mmc", "serbatoi_piave_pct", "corlo_mmc", "corlo_pct"]
MESI = {m: i for i, m in enumerate(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split(), 1)}


def link(da_anno: int) -> list[tuple[int, str, str]]:
    """Ritorna [(numero, data ISO, url)] cercando nell'indice e nelle pagine per anno."""
    pagine = [INDICE] + [f"{BASE}/temi-ambientali/idrologia/file-e-allegati/bollettini-risorsa-idrica/{a}"
                         for a in range(da_anno, date.today().year + 1)]
    trovati = {}
    for pg in pagine:
        try:
            html = get(pg).text
        except Exception:
            continue
        for href, numero, g, mese, anno in LINK.findall(html):
            if int(anno) >= da_anno and mese in MESI:
                url = href if href.startswith("http") else BASE + href
                trovati[int(numero)] = (f"{anno}-{MESI[mese]:02d}-{int(g):02d}", url)
    return [(n, d, u) for n, (d, u) in sorted(trovati.items())]


def raccogli(da_anno: int = 2025, forza: bool = False) -> int:
    con = db()
    n = 0
    for numero, data, url in link(da_anno):
        if not forza and con.execute("SELECT 1 FROM bollettini_arpav WHERE numero=?", (numero,)).fetchone():
            continue
        try:
            dati = scarica_pdf(url)
            sez = sezioni(pulisci(testo_pdf(dati)))
            val = estrai(sez)
            with con:
                con.execute(
                    f"INSERT OR REPLACE INTO bollettini_arpav (numero, data, url, {', '.join(COLONNE)}) "
                    f"VALUES ({', '.join('?' * (3 + len(COLONNE)))})",
                    (numero, data, url, *[val.get(c) for c in COLONNE]),
                )
                con.executemany("INSERT OR REPLACE INTO bollettini_sezioni VALUES (?,?,?)",
                                [(numero, k, v) for k, v in sez.items()])
                segna_documento(con, url, "bollettino_arpav", dati, "ok")
            log(f"bollettini: n.{numero} del {data}: {len(sez)} sezioni, {len(val)} valori")
            n += 1
        except Exception as e:
            log(f"bollettini: errore n.{numero}: {e}")
    log(f"bollettini: {n} nuovi")
    return n

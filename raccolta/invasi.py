"""Invasi strategici e livello di severita' idrica - Osservatorio permanente Alpi Orientali.

Fonti (PDF):
  - bollettini dell'Osservatorio: https://distrettoalpiorientali.it/osservatorio-permanente/
  - report settimanali (in periodo di crisi) linkati da ISPRA:
    https://www.isprambiente.gov.it/pre_meteo/idro/SeverIdrica.html
I volumi derivano dalla piattaforma SISMON (MIT - Direzione dighe). La frequenza dipende
dalle sedute dell'Osservatorio: settimanale nei periodi critici, mensile o meno altrimenti.
"""
from __future__ import annotations

import re

from .comune import data_italiana, db, get, log, num, scarica_pdf, segna_documento, testo_pdf

PAGINE_INDICE = [
    "https://distrettoalpiorientali.it/osservatorio-permanente/",
    "https://www.isprambiente.gov.it/pre_meteo/idro/SeverIdrica.html",
]

# Capacita' utile di regolazione (milioni m3), usata per riconoscere le righe delle tabelle.
INVASI = {
    "Adige": {"Santa Giustina": 171.7, "San Valentino - Resia": 112.0, "Vernago": 43.1,
              "Gioveretto": 19.6, "Zoccolo": 33.1, "Stramentizzo": 8.5},
    "Brenta-Bacchiglione": {"Corlo": 38.2, "Senaiga": 7.3},
    "Piave": {"Bastia - S. Croce": 86.6, "Pieve di Cadore": 45.9, "Mis": 35.2},
    "Livenza": {"Ponte Racli": 20.7, "Barcis": 11.2, "Ca' Selva": 34.8, "Ca' Zul": 8.1},
    "Tagliamento": {"Lumiei": 65.2},
}
TOTALI = {b: round(sum(v.values()), 1) for b, v in INVASI.items()}
TOTALE_DISTRETTO = 741.2
ALIAS_BACINO = {"adige": "Adige", "brenta": "Brenta-Bacchiglione", "piave": "Piave",
                "livenza": "Livenza", "tagliamento": "Tagliamento"}

RIGA = re.compile(
    r"^(?P<lab>.*?)\s*\*?\s+(?P<u>\d{1,3}[.,]\d)\s+(?P<v>\d{1,3}(?:[.,]\d{1,2})?)\s+(?P<p>\d{1,3})\s*%\s*$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _vicino(a: float, b: float) -> bool:
    return abs(a - b) <= 0.15


def estrai_invasi(testo: str) -> tuple[str | None, list[tuple]]:
    piatto = re.sub(r"\s+", " ", testo)
    m = re.search(r"invasi strategici alla data del (\d{1,2} \w+ \d{4})", piatto)
    data = data_italiana(m.group(1)) if m else None
    righe = {}
    for linea in testo.splitlines():
        m = RIGA.match(linea.strip())
        if not m:
            continue
        lab, u, v, p = _norm(m["lab"]), num(m["u"]), num(m["v"]), num(m["p"])
        chiavi = []
        if "totaledistretto" in lab and _vicino(u, TOTALE_DISTRETTO):
            chiavi.append(("Distretto", "TOTALE"))
        else:
            for bacino, invasi in INVASI.items():
                alias = next(k for k, b in ALIAS_BACINO.items() if b == bacino)
                if _vicino(u, TOTALI[bacino]) and alias in lab:
                    chiavi.append((bacino, "TOTALE"))
                chiavi += [(bacino, nome) for nome, cap in invasi.items()
                           if _vicino(u, cap) and _norm(nome) in lab]
        if not chiavi:
            continue
        # Le tabelle sorgente contengono a volte refusi (es. bollettino n.8/2026: 'TOTALE DISTRETTO
        # 741.2 435 11%'): se la % non e' coerente con i volumi, la ricalcoliamo dai volumi.
        if abs(p - v / u * 100) > 3:
            log(f"invasi: % incoerente in '{linea.strip()}' -> ricalcolata {v / u * 100:.0f}%")
            p = round(v / u * 100)
        for k in chiavi:
            righe[k] = (u, v, p)
    return data, [(b, i, *vals) for (b, i), vals in righe.items()]


_AREA = re.compile(
    r"((?:Regione (?:del )?Veneto|Pianura Veneta|Montagna Veneta)[^;.]*?)\s+in classe\s+"
    r"(nulla|bassa|media|alta)([^;.]*)", re.I)
_DEFINITA = re.compile(
    r"definita\s+(?:di classe\s+)?(nulla|bassa|media|alta)\s+per i territori[^.]*?Regione (?:del )?Veneto", re.I)


def _comparto(s: str) -> str:
    s = s.lower()
    sup, sot = "superficial" in s, "sotterrane" in s
    return "entrambi" if sup == sot else ("superficiali" if sup else "sotterranee")


def estrai_severita(testo: str) -> tuple[str | None, list[tuple]]:
    piatto = re.sub(r"\s+", " ", testo)
    m = re.search(r"seduta\s+(?:dell.Osservatorio\s+Permanente\s+)?n\.\s*\d+\s+del\s+(\d{1,2} \w+ \d{4})",
                  piatto, re.I)
    data = data_italiana(m.group(1)) if m else None
    i = piatto.lower().rfind("livello di severit")
    sezione = piatto[i:] if i >= 0 else piatto
    out = {}
    for m in _AREA.finditer(sezione):
        area = re.sub(r"\s+", " ", m.group(1)).strip()
        area = "Pianura Veneta" if "pianura" in area.lower() else (
            "Montagna Veneta" if "montagna" in area.lower() else "Regione Veneto")
        out[(area, _comparto(m.group(3)))] = m.group(2).lower()
    if not out:
        m = _DEFINITA.search(sezione)
        if m:
            out[("Regione Veneto", "entrambi")] = m.group(1).lower()
    return data, [(a, c, cl) for (a, c), cl in out.items()]


def link_pdf(da_anno: int) -> list[str]:
    urls = []
    for pagina in PAGINE_INDICE:
        try:
            html = get(pagina).text
        except Exception as e:
            log(f"invasi: indice non raggiungibile {pagina}: {e}")
            continue
        for u in re.findall(r'href="([^"]+\.pdf)"', html, re.I):
            nome = u.rsplit("/", 1)[-1].lower()
            if not re.search(r"bollettino|report|notiziario", nome):
                continue  # delibere e altri atti: niente tabelle invasi
            if "distrettoalpiorientali.it" in u or "/Osservatori/AlpiOrientali/" in u:
                anni = [int(a) for a in re.findall(r"20\d\d", u)]
                if anni and max(anni) >= da_anno and u not in urls:
                    urls.append(u)
    return urls


def elabora(con, url: str) -> str:
    dati = scarica_pdf(url)
    testo = testo_pdf(dati)
    data_inv, invasi = estrai_invasi(testo)
    data_sev, sev = estrai_severita(testo)
    with con:
        if data_inv:
            con.executemany("INSERT OR REPLACE INTO invasi VALUES (?,?,?,?,?,?,?)",
                            [(data_inv, *r, url) for r in invasi])
        data_s = data_sev or data_inv
        if data_s:
            con.executemany("INSERT OR REPLACE INTO severita VALUES (?,?,?,?,?)",
                            [(data_s, *r, url) for r in sev])
        esito = "ok" if (invasi or sev) else "vuoto"
        segna_documento(con, url, "osservatorio", dati, esito)
    log(f"invasi: {url.rsplit('/', 1)[-1]} -> {len(invasi)} righe invasi ({data_inv}), "
        f"{len(sev)} severita ({data_s if data_sev or data_inv else '-'})")
    return esito


def raccogli(da_anno: int = 2025, forza: bool = False) -> int:
    con = db()
    n = 0
    for url in link_pdf(da_anno):
        if not forza and con.execute("SELECT 1 FROM documenti WHERE url=?", (url,)).fetchone():
            continue
        try:
            elabora(con, url)
            n += 1
        except Exception as e:
            log(f"invasi: errore {url}: {e}")
            with con:
                segna_documento(con, url, "osservatorio", None, f"errore: {e}")
    log(f"invasi: {n} documenti nuovi elaborati")
    return n

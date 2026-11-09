"""Idrometri ARPAV in tempo reale (livelli, portate) dal feed XML open data.

Feed: ultimi ~3 giorni, misure ogni 5-60 min (di solito 10), dati NON validati,
licenza CC BY 4.0. Orari in ora solare (UTC+1) tutto l'anno.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime

from .comune import db, get, log, meta_get, meta_set, num

URL = "https://www.arpa.veneto.it/api/risorse/data-meteo/xml/Ultime48ore.xml"


def _iso(istante: str) -> str:
    # 202611050010 -> 2026-11-05T00:10:00+01:00 (ora solare)
    i = istante
    return f"{i[0:4]}-{i[4:6]}-{i[6:8]}T{i[8:10]}:{i[10:12]}:00+01:00"


def raccogli(forza: bool = False):
    con = db()
    etag = None if forza else meta_get(con, "fiumi_etag")
    # Richiesta condizionale: se il file non e' cambiato ARPAV risponde 304 senza corpo.
    r = get(URL, timeout=120, headers={"If-None-Match": etag} if etag else None)
    if r.status_code == 304:
        log("fiumi: feed invariato (304)")
        return "invariato"
    root = ET.fromstring(r.content)  # encoding dichiarato nel prologo XML
    run = root.findtext("ISTANTERUN")
    with con:
        meta_set(con, "fiumi_etag", r.headers.get("ETag"))
        if lm := r.headers.get("Last-Modified"):
            pubbl = parsedate_to_datetime(lm).astimezone().isoformat(timespec="seconds")
            meta_set(con, "fiumi_pubblicato", pubbl)
            # storico delle pubblicazioni del feed -> intervallo di aggiornamento osservato
            storia = json.loads(meta_get(con, "fiumi_pubblicazioni") or "[]")
            if pubbl not in storia:
                storia = (storia + [pubbl])[-30:]
            meta_set(con, "fiumi_pubblicazioni", json.dumps(storia))
        if run:
            meta_set(con, "fiumi_istanterun", _iso(run))
    stazioni = misure = 0
    with con:
        for st in root.iter("STAZIONE"):
            sid = f"idro:{st.findtext('IDSTAZ')}"
            con.execute(
                "INSERT OR REPLACE INTO stazioni VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, "idro", st.findtext("NOME"), float(st.findtext("Y")), float(st.findtext("X")),
                 float(st.findtext("QUOTA") or "nan"), st.findtext("PROVINCIA"), st.findtext("COMUNE"),
                 _iso(run) if run else None),
            )
            stazioni += 1
            sensori = st.findall("SENSORE")
            tipi = Counter(s.findtext("TYPE") for s in sensori)
            for se in sensori:
                tipo = se.findtext("TYPE")
                nome = tipo if tipi[tipo] == 1 else f"{tipo}:{se.findtext('ID')}"
                unita = se.findtext("UNITNM")
                # VM puo' valere '>>' (dato mancante/fuori scala): lo scartiamo
                righe = [
                    (sid, nome, _iso(d.get("ISTANTE")), v, unita)
                    for d in se.findall("DATI") if (v := num(d.findtext("VM"))) is not None
                ]
                con.executemany("INSERT OR REPLACE INTO misure VALUES (?,?,?,?,?)", righe)
                misure += len(righe)
    log(f"fiumi: {stazioni} stazioni, {misure} misure (run {run})")
    return misure

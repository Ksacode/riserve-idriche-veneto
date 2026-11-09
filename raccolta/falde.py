"""Freatimetri ARPAV (livello di falda, m s.l.m.) dall'API REST usata dal sito.

Endpoint non documentato ufficialmente (ricavato dalla pagina 'Freatimetria in diretta'):
potrebbe cambiare senza preavviso. Restituisce dati orari degli ultimi ~10 giorni,
non validati; il sito li ricarica circa una volta al giorno.
"""
from __future__ import annotations

import time

from .comune import db, get, log

API = "https://api.arpa.veneto.it/REST/v1"
URL_STAZIONI = f"{API}/meteo_meteogrammi?rete=MGRAMMIFREAT&orario=0&coordcd=20058"
URL_VALORI = f"{API}/meteo_meteogrammi_tabella?codseqst={{}}"


def raccogli(pausa: float = 0.5) -> int:
    stazioni = get(URL_STAZIONI).json()["data"]
    con = db()
    tot = 0
    for s in stazioni:
        sid = f"falda:{s['codseqst']}"
        with con:
            con.execute(
                "INSERT OR REPLACE INTO stazioni VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, "falda", s["nome_stazione"], s["latitudine"], s["longitudine"], s.get("quota"),
                 s.get("provincia"), None, s.get("aggiornamento")),
            )
            try:
                valori = get(URL_VALORI.format(s["codseqst"])).json().get("data") or []
            except Exception as e:  # una stazione in errore non blocca le altre
                log(f"falde: errore {s['nome_stazione']}: {e}")
                continue
            righe = [
                (sid, v.get("tipo") or "LFREAT", v["dataora"], float(v["valore"]), v.get("unitnm"))
                for v in valori if v.get("valore") not in (None, "")
            ]
            con.executemany("INSERT OR REPLACE INTO misure VALUES (?,?,?,?,?)", righe)
            tot += len(righe)
        time.sleep(pausa)
    log(f"falde: {len(stazioni)} stazioni, {tot} misure")
    return tot

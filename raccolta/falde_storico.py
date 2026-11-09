"""Storico dei freatimetri ARPAV: medie giornaliere del livello di falda dal 2010.

Endpoint non documentato, ricavato dalla pagina ARPAV "Freatimetria ultimi anni"
(dati-storici/meteo-idro-nivo/freat_storici):
  - meteo_storici?coordcd=20058&anno=AAAA   -> stazioni attive in quell'anno (con `codseq` annuale)
  - meteo_storici_tabella?codseq=N&anno=AAAA -> medie giornaliere di quell'anno
ARPAV aggiorna l'archivio intorno al 25 di ogni mese con il mese precedente.

Le medie giornaliere sono salvate in `misure` con sensore 'LFREAT_GG' e istante 'AAAA-MM-GG',
sotto lo stesso id stazione dei dati in tempo reale (falda:<codseqst>), abbinato per codice stazione.
Servono come riferimento per minimo e massimo storici di ogni pozzo.
"""
from __future__ import annotations

import json
import time
from datetime import date

from .comune import db, get, log, meta_get, meta_set
from .falde import URL_STAZIONI

API = "https://api.arpa.veneto.it/REST/v1"
URL_ANNO = f"{API}/meteo_storici?coordcd=20058&anno={{}}"
URL_TABELLA = f"{API}/meteo_storici_tabella?codseq={{}}&anno={{}}"
PRIMO_ANNO = 2010


def raccogli(forza: bool = False, pausa: float = 0.2) -> int:
    con = db()
    # codice stazione -> id usato dai dati in tempo reale
    attuali = {s["codice_stazione"]: s for s in get(URL_STAZIONI).json()["data"]}
    completi = set(json.loads(meta_get(con, "falde_storico_anni_completi") or "[]"))
    oggi = date.today()
    tot = 0
    for anno in range(PRIMO_ANNO, oggi.year + 1):
        # gli anni chiusi da piu' di un anno non cambiano piu': si scaricano una volta sola
        if anno in completi and not forza:
            continue
        try:
            stazioni = get(URL_ANNO.format(anno)).json().get("data") or []
        except Exception as e:
            log(f"falde storico: errore elenco {anno}: {e}")
            continue
        errori = 0
        for s in stazioni:
            rt = attuali.get(s["codice_stazione"])
            if not rt:  # pozzo dismesso: senza dato attuale non serve come riferimento
                continue
            sid = f"falda:{rt['codseqst']}"
            try:
                valori = get(URL_TABELLA.format(s["codseq"], anno)).json().get("data") or []
            except Exception as e:
                log(f"falde storico: errore {s['nome_stazione']} {anno}: {e}")
                errori += 1
                continue
            righe = [(sid, "LFREAT_GG", v["dataora"][:10], float(v["valore"]), v.get("unitnm"))
                     for v in valori if v.get("valore") not in (None, "")]
            with con:
                con.executemany("INSERT OR REPLACE INTO misure VALUES (?,?,?,?,?)", righe)
            tot += len(righe)
            time.sleep(pausa)
        if anno <= oggi.year - 2 and not errori:
            completi.add(anno)
            with con:
                meta_set(con, "falde_storico_anni_completi", json.dumps(sorted(completi)))
        log(f"falde storico: {anno} fatto ({len(stazioni)} pozzi)")
    ultimo = con.execute("SELECT MAX(istante) FROM misure WHERE sensore='LFREAT_GG'").fetchone()[0]
    log(f"falde storico: {tot} medie giornaliere, ultimo giorno {ultimo}")
    return tot

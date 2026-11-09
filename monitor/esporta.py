"""Esporta il monitor come sito statico (per GitHub Pages o qualsiasi hosting di file).

    python -m monitor.esporta --raccogli --out sito

--raccogli  esegue prima le raccolte "scadute" secondo gli intervalli di JOBS
            (pensato per un'attivita' programmata ogni 15-30 min, es. GitHub Actions)
--out       cartella del sito: index.html + api/tutto.json + api/serie/*.json

Il sito statico non ha server: la pagina legge i JSON generati qui. Il pulsante
"Controlla ora" sparisce e il "prossimo controllo" e' una stima.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from raccolta.comune import db, esegui, log

from . import server

GIORNI_SERIE = 31          # idrometri e falde orarie: ultimi 31 giorni (bottoni 3/7/30 giorni)
GIORNI_RISOLUZIONE_PIENA = 7  # oltre, gli idrometri si tengono solo al passo orario
MARGINE = timedelta(minutes=10)  # i lanci programmati non sono puntuali


def nome_file_serie(stazione: str, sensore: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{stazione}__{sensore}") + ".json"


def raccolte_scadute() -> list[str]:
    con = db()
    ultimi = {r[0]: r[1] for r in con.execute("SELECT job, ultima_fine FROM raccolte")}
    con.close()
    ora = datetime.now().astimezone()
    return [job for job, cfg in server.JOBS.items()
            if not ultimi.get(job)
            or ora - datetime.fromisoformat(ultimi[job]) >= timedelta(seconds=cfg["ogni"]) - MARGINE]


def compatta():
    """Idrometri: oltre 7 giorni tiene solo le misure allo scoccare dell'ora (il database resta piccolo)."""
    con = db()
    limite = (datetime.now().astimezone() - timedelta(days=GIORNI_RISOLUZIONE_PIENA)).isoformat()
    with con:
        n = con.execute("DELETE FROM misure WHERE stazione_id LIKE 'idro:%' AND istante < ? "
                        "AND substr(istante, 15, 2) <> '00'", (limite,)).rowcount
    if n:
        log(f"esporta: compattate {n} misure idrometriche vecchie")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()


def esporta(out: Path):
    api = out / "api" / "serie"
    if out.exists():
        shutil.rmtree(out)
    api.mkdir(parents=True)

    corpo = json.loads(server.tutto())
    corpo["statico"] = True
    corpo["raccolta_attiva"] = True
    for f in corpo["fonti"]:
        # stima del prossimo controllo: ultimo controllo + intervallo del job
        if f.get("ultima_raccolta"):
            t = datetime.fromisoformat(f["ultima_raccolta"]) + timedelta(minutes=f["controllo_ogni_min"])
            f["prossima_raccolta"] = t.isoformat(timespec="seconds")
        f["in_corso"] = False
    (out / "api" / "tutto.json").write_text(json.dumps(corpo, ensure_ascii=False), encoding="utf-8")

    con = server._ro()
    ultimo = con.execute("SELECT MAX(istante) FROM misure WHERE sensore<>'LFREAT_GG'").fetchone()[0] or ""
    da = (datetime.fromisoformat(ultimo) - timedelta(days=GIORNI_SERIE)).isoformat() if ultimo else ""
    coppie = con.execute("SELECT DISTINCT stazione_id, sensore FROM misure "
                         "WHERE sensore IN ('LIVIDRO','LFREAT','LFREAT_GG') OR sensore LIKE 'PORTATA%'").fetchall()
    for sid, sens in coppie:
        if sens == "LFREAT_GG":  # storico delle falde: tutto (medie giornaliere dal 2010)
            rows = con.execute("SELECT istante, valore, unita FROM misure WHERE stazione_id=? AND sensore=? "
                               "ORDER BY istante", (sid, sens)).fetchall()
        else:
            rows = con.execute("SELECT istante, valore, unita FROM misure WHERE stazione_id=? AND sensore=? "
                               "AND istante>=? ORDER BY istante", (sid, sens, da)).fetchall()
        dati = {"punti": [[r[0], r[1]] for r in rows], "unita": rows[-1][2] if rows else None}
        (api / nome_file_serie(sid, sens)).write_text(json.dumps(dati, separators=(",", ":")), encoding="utf-8")
    con.close()

    # la pagina e' la stessa del monitor locale, marcata come statica
    html = (server.STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('<html lang="it">', '<html lang="it" data-statico>', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    log(f"esporta: sito in {out} ({len(coppie)} serie)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="monitor.esporta")
    ap.add_argument("--raccogli", action="store_true", help="esegue prima le raccolte scadute")
    ap.add_argument("--out", default="sito")
    a = ap.parse_args(argv)
    if a.raccogli:
        for job in raccolte_scadute():
            log(f"esporta: raccolta {job}")
            esegui(job, server.JOBS[job]["fn"])
        compatta()
    esporta(Path(a.out))


if __name__ == "__main__":
    main()

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


URL_SITO = "https://ksacode.github.io/riserve-idriche-veneto/"
# codice di verifica di Google Search Console (meta tag "google-site-verification"), se presente
GOOGLE_VERIFICA = "GZk0hfLm1nVGLDXXiy4cz5pxRfeHRT9E2PPKdQydmtw"


def testa_seo(generato: str) -> str:
    """Tag per motori di ricerca e anteprime social: solo nel sito pubblicato (URL assoluti)."""
    titolo = "Riserve idriche del Veneto: fiumi, falde, invasi e neve in tempo reale"
    descr = ("Livello di fiumi e falde del Veneto in tempo reale, riempimento degli invasi, severità idrica, "
             "neve e Lago di Garda. Dati ARPAV e Osservatorio Alpi Orientali aggiornati ogni 15 minuti.")
    img = URL_SITO + "anteprima.png"
    dati = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": "Riserve idriche del Veneto in tempo reale",
        "description": descr, "url": URL_SITO, "inLanguage": "it", "isAccessibleForFree": True,
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "keywords": ["Veneto", "riserve idriche", "siccità", "livello fiumi", "idrometri", "falde",
                     "freatimetri", "invasi", "neve", "Lago di Garda", "ARPAV"],
        "spatialCoverage": {"@type": "Place", "name": "Veneto, Italia"},
        "temporalCoverage": f"2010-01-01/{generato[:10]}",
        "dateModified": generato,
        "creator": {"@type": "Person", "name": "Ksacode", "url": "https://github.com/Ksacode"},
        "isBasedOn": ["https://www.arpa.veneto.it/", "https://distrettoalpiorientali.it/osservatorio-permanente/"],
        "distribution": [{"@type": "DataDownload", "encodingFormat": "application/json",
                          "contentUrl": URL_SITO + "api/tutto.json"}],
    }
    righe = [
        f'<link rel="canonical" href="{URL_SITO}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="it_IT">',
        f'<meta property="og:title" content="{titolo}">',
        f'<meta property="og:description" content="{descr}">',
        f'<meta property="og:url" content="{URL_SITO}">',
        f'<meta property="og:image" content="{img}">',
        '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<script type="application/ld+json">{json.dumps(dati, ensure_ascii=False)}</script>',
    ]
    if GOOGLE_VERIFICA:
        righe.append(f'<meta name="google-site-verification" content="{GOOGLE_VERIFICA}">')
    return "\n".join(righe) + "\n"


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
    # statistiche delle visite senza cookie (GoatCounter), solo sul sito pubblicato
    html = html.replace("</head>", '<script data-goatcounter="https://ksajural.goatcounter.com/count" '
                        'async src="https://gc.zgo.at/count.js"></script></head>', 1)
    html = html.replace("</head>", testa_seo(corpo["generato"]) + "</head>", 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    for f in server.STATIC.iterdir():  # immagini e altri file statici (anteprima per i social)
        if f.is_file() and f.name != "index.html":
            shutil.copy2(f, out / f.name)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{URL_SITO}</loc><lastmod>{corpo["generato"][:10]}</lastmod>'
        '<changefreq>hourly</changefreq></url>\n</urlset>\n', encoding="utf-8")
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

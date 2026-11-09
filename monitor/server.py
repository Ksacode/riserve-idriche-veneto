"""Monitor in tempo reale: server web locale + raccolta automatica in background.

Avvio:   .venv\\Scripts\\python -m monitor            -> http://127.0.0.1:8790
Opzioni: --porta 8790  --senza-raccolta (solo visualizzazione del database)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from raccolta import bollettini, falde, falde_storico, fiumi, invasi
from raccolta.comune import DB_PATH, adesso, db, esegui, log

STATIC = Path(__file__).parent / "static"

# Ogni quanto il monitor ricontrolla ciascuna fonte (secondi) e cosa sappiamo della fonte.
JOBS = {
    "fiumi": dict(ogni=5 * 60, fn=lambda: fiumi.raccogli(),
                  nome="Idrometri (livelli e portate)", ente="ARPAV",
                  url="https://www.arpa.veneto.it/dati-ambientali/open-data/dati-arpav-in-formato-xml",
                  frequenza="Misure ogni 10 min (alcune stazioni 5–60 min). File ripubblicato di continuo, "
                            "dati disponibili ~30 min dopo la misura. Non validati.",
                  soglie_h=(1.5, 6)),
    "falde": dict(ogni=2 * 3600, fn=lambda: falde.raccogli(),
                  nome="Falde (freatimetri)", ente="ARPAV",
                  url="https://www.arpa.veneto.it/dati-ambientali/dati-in-diretta/meteo-idro-nivo/freat_diretta",
                  frequenza="Misure orarie, caricate da ARPAV circa 1 volta al giorno (mattina). Non validati.",
                  soglie_h=(36, 96)),
    "falde_storico": dict(ogni=24 * 3600, fn=lambda: falde_storico.raccogli(),
                          nome="Falde: archivio storico (medie giornaliere dal 2010)", ente="ARPAV",
                          url="https://www.arpa.veneto.it/dati-ambientali/dati-storici/meteo-idro-nivo/freat_storici",
                          frequenza="Mensile: ARPAV aggiunge il mese precedente intorno al 25 del mese. "
                                    "Serve per minimo e massimo storici di ogni pozzo.",
                          soglie_h=(60 * 24, 95 * 24)),
    "invasi": dict(ogni=6 * 3600, fn=lambda: invasi.raccogli(),
                   nome="Invasi strategici e severità idrica", ente="Osservatorio Alpi Orientali / ISPRA",
                   url="https://distrettoalpiorientali.it/osservatorio-permanente/",
                   frequenza="A ogni seduta dell'Osservatorio: settimanale nei periodi di crisi, "
                             "ogni 1–6 settimane altrimenti (bollettino PDF).",
                   soglie_h=(45 * 24, 90 * 24)),
    "bollettini": dict(ogni=6 * 3600, fn=lambda: bollettini.raccogli(),
                       nome="Bollettino risorsa idrica (neve, serbatoi, Garda)", ente="ARPAV",
                       url="https://www.arpa.veneto.it/temi-ambientali/idrologia/bollettini-risorsa-idrica",
                       frequenza="Mensile: dati a fine mese, PDF pubblicato nei primi giorni del mese successivo.",
                       soglie_h=(40 * 24, 70 * 24)),
}

_prossimo: dict[str, float] = {}
_in_corso: set[str] = set()
_lock = threading.Lock()
_avviato = adesso()
_raccolta_attiva = True


def lancia(job: str) -> bool:
    with _lock:
        if job in _in_corso:
            return False
        _in_corso.add(job)

    def run():
        try:
            log(f"monitor: raccolta {job}")
            esegui(job, JOBS[job]["fn"])
        finally:
            with _lock:
                _in_corso.discard(job)
                _prossimo[job] = time.time() + JOBS[job]["ogni"]
    threading.Thread(target=run, daemon=True, name=f"job-{job}").start()
    return True


def pianificatore():
    con = db()
    ultimi = {r[0]: r[1] for r in con.execute("SELECT job, ultima_fine FROM raccolte")}
    con.close()
    for job, cfg in JOBS.items():
        # al primo avvio riprende da dove era rimasto: se l'ultima raccolta e' recente, aspetta
        t = ultimi.get(job)
        trascorso = (datetime.now().astimezone() - datetime.fromisoformat(t)).total_seconds() if t else 1e9
        _prossimo[job] = time.time() + max(0, cfg["ogni"] - trascorso)
    while True:
        for job in JOBS:
            if time.time() >= _prossimo.get(job, 0) and job not in _in_corso:
                _prossimo[job] = time.time() + JOBS[job]["ogni"]
                lancia(job)
        time.sleep(5)


# ----------------------------------------------------------------------------- dati per la pagina

def _ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.astimezone()  # orari senza fuso = ora locale


def _stato(eta_h: float | None, soglie) -> str:
    if eta_h is None:
        return "critical"
    return "good" if eta_h <= soglie[0] else ("warning" if eta_h <= soglie[1] else "critical")


def fonti(con) -> list[dict]:
    racc = {r["job"]: dict(r) for r in con.execute("SELECT * FROM raccolte")}
    meta = {r["chiave"]: r["valore"] for r in con.execute("SELECT * FROM meta")}
    ora = datetime.now().astimezone()

    ultimo = {
        "fiumi": con.execute("SELECT MAX(istante) FROM misure WHERE stazione_id LIKE 'idro:%'").fetchone()[0],
        "falde": con.execute("SELECT MAX(istante) FROM misure WHERE stazione_id LIKE 'falda:%' "
                             "AND sensore='LFREAT'").fetchone()[0],
        "falde_storico": con.execute("SELECT MAX(istante) FROM misure WHERE sensore='LFREAT_GG'").fetchone()[0],
        "invasi": con.execute("SELECT MAX(data) FROM invasi").fetchone()[0],
        "bollettini": con.execute("SELECT MAX(data) FROM bollettini_arpav").fetchone()[0],
    }
    pubblicato = {
        "fiumi": meta.get("fiumi_pubblicato"),
        "falde": con.execute("SELECT MAX(aggiornata) FROM stazioni WHERE rete='falda'").fetchone()[0],
        "invasi": con.execute("SELECT MAX(data) FROM severita").fetchone()[0],
        "falde_storico": None,
        "bollettini": None,
    }
    # intervallo osservato tra due pubblicazioni consecutive del feed idrometri
    oss = None
    storia = [datetime.fromisoformat(x) for x in json.loads(meta.get("fiumi_pubblicazioni") or "[]")]
    if len(storia) >= 2:
        oss = statistics.median((b - a).total_seconds() / 60 for a, b in zip(storia, storia[1:]))

    out = []
    for job, cfg in JOBS.items():
        r = racc.get(job, {})
        d = _dt(ultimo[job]) if job in ("fiumi", "falde") else (
            _dt(ultimo[job] + "T23:59:59") if ultimo[job] else None)
        eta_h = (ora - d).total_seconds() / 3600 if d else None
        prossimo = _prossimo.get(job)
        out.append(dict(
            id=job, nome=cfg["nome"], ente=cfg["ente"], url=cfg["url"], frequenza=cfg["frequenza"],
            controllo_ogni_min=cfg["ogni"] // 60,
            ultimo_dato=ultimo[job], pubblicato=pubblicato[job],
            ultima_raccolta=r.get("ultima_fine"), ultimo_ok=r.get("ultimo_ok"), esito=r.get("esito"),
            messaggio=r.get("messaggio"), in_corso=job in _in_corso,
            prossima_raccolta=(datetime.fromtimestamp(prossimo).astimezone().isoformat(timespec="seconds")
                               if prossimo and _raccolta_attiva else None),
            stato=_stato(eta_h, cfg["soglie_h"]),
            intervallo_osservato_min=round(oss, 1) if oss and job == "fiumi" else None,
            pubblicazioni_osservate=len(storia) if job == "fiumi" else None,
        ))
    return out


def tipo_corpo_idrico(nome: str) -> str:
    n = (nome or "").lower()
    if n.startswith("lago"):
        return "lago"
    if n.startswith("cassa di espansione"):
        return "cassa"
    # Bisatto e Gorzone sono canali artificiali anche se il nome non lo dice
    if n.startswith(("canal", "scolo", "collettore", "idrovia", "naviglio", "fossa", "bisatto", "gorzone")):
        return "canale"
    if n.startswith("po a porto") or "foce" in n:
        return "foce"  # delta: il livello risente della marea
    return "fiume"


def dati_fiumi(con) -> dict:
    fine = con.execute("SELECT MAX(istante) FROM misure WHERE stazione_id LIKE 'idro:%'").fetchone()[0]
    if not fine:
        return {"stazioni": [], "ore": []}
    t_fine = datetime.fromisoformat(fine).replace(minute=0, second=0)
    ore = [(t_fine - timedelta(hours=h)).isoformat() for h in range(47, -1, -1)]
    inizio = ore[0]
    serie: dict = {}
    for r in con.execute(
            "SELECT stazione_id, sensore, istante, valore FROM misure WHERE stazione_id LIKE 'idro:%' "
            "AND (sensore='LIVIDRO' OR sensore LIKE 'PORTATA%') AND istante>=? ORDER BY istante", (inizio,)):
        serie.setdefault(r[0], {}).setdefault(r[1], {})[r[2]] = r[3]
    # escursione del livello in tutto lo storico registrato: riferimento per l'indicatore di riempimento
    escursione = {r[0]: dict(min=r[1], max=r[2], dal=r[3]) for r in con.execute(
        "SELECT stazione_id, MIN(valore), MAX(valore), MIN(istante) FROM misure "
        "WHERE stazione_id LIKE 'idro:%' AND sensore='LIVIDRO' GROUP BY stazione_id")}
    staz = []
    for s in con.execute("SELECT * FROM stazioni WHERE rete='idro' ORDER BY nome"):
        ss = serie.get(s["id"], {})
        rec = dict(id=s["id"], nome=s["nome"], prov=s["provincia"], comune=s["comune"],
                   lat=s["lat"], lon=s["lon"], quota=s["quota"], tipo=tipo_corpo_idrico(s["nome"]),
                   esc=escursione.get(s["id"]))
        por_key = "PORTATA" if "PORTATA" in ss else next((k for k in sorted(ss) if k.startswith("PORTATA")), None)
        for chiave, sensore in (("liv", "LIVIDRO"), ("por", por_key)):
            vals = ss.get(sensore) if sensore else None
            if not vals:
                continue
            t_last = max(vals)
            t_24 = (datetime.fromisoformat(t_last) - timedelta(hours=24)).isoformat()
            prec = [t for t in vals if t <= t_24]
            rec[chiave] = dict(sensore=sensore, v=vals[t_last], t=t_last,
                               d24=round(vals[t_last] - vals[max(prec)], 3) if prec else None,
                               spark=[vals.get(o) for o in ore])
        staz.append(rec)
    return {"stazioni": staz, "ore": ore}


def _storico_falda(con, sid: str) -> dict | None:
    """Minimo e massimo storici delle medie giornaliere (archivio ARPAV dal 2010), con le date."""
    r = con.execute("SELECT MIN(istante), MAX(istante), COUNT(*) FROM misure "
                    "WHERE stazione_id=? AND sensore='LFREAT_GG'", (sid,)).fetchone()
    if not r[2]:
        return None
    lo = con.execute("SELECT valore, istante FROM misure WHERE stazione_id=? AND sensore='LFREAT_GG' "
                     "ORDER BY valore, istante DESC LIMIT 1", (sid,)).fetchone()
    hi = con.execute("SELECT valore, istante FROM misure WHERE stazione_id=? AND sensore='LFREAT_GG' "
                     "ORDER BY valore DESC, istante DESC LIMIT 1", (sid,)).fetchone()
    ult = con.execute("SELECT valore, istante FROM misure WHERE stazione_id=? AND sensore='LFREAT_GG' "
                      "ORDER BY istante DESC LIMIT 1", (sid,)).fetchone()
    return dict(min=lo[0], min_data=lo[1], max=hi[0], max_data=hi[1], dal=r[0], al=r[1], giorni=r[2],
                ultimo_gg=ult[0])


def dati_falde(con) -> list[dict]:
    out = []
    for s in con.execute("SELECT * FROM stazioni WHERE rete='falda' ORDER BY nome"):
        rows = con.execute("SELECT istante, valore FROM misure WHERE stazione_id=? AND sensore='LFREAT' "
                           "ORDER BY istante", (s["id"],)).fetchall()
        rec = dict(id=s["id"], nome=s["nome"], prov=s["provincia"], lat=s["lat"], lon=s["lon"], quota=s["quota"],
                   esc=_storico_falda(con, s["id"]))
        if rows:
            t_last, v_last = rows[-1]
            t7 = (datetime.fromisoformat(t_last) - timedelta(days=7)).isoformat()
            prec = [r for r in rows if r[0] <= t7]
            # una misura ogni 6 ore per la sparkline degli ultimi giorni
            rec.update(v=v_last, t=t_last, d7=round(v_last - prec[-1][1], 3) if prec else None,
                       spark=[[r[0], r[1]] for r in rows[-24 * 14:] if r[0][11:13] in ("00", "06", "12", "18")])
        out.append(rec)
    return out


def dati_invasi(con) -> dict:
    d = con.execute("SELECT MAX(data) FROM invasi").fetchone()[0]
    ultimi = [dict(r) for r in con.execute(
        "SELECT bacino, invaso, volume_utile, volume_invasato, perc, fonte_url FROM invasi WHERE data=? "
        "ORDER BY bacino='Distretto', bacino, invaso='TOTALE' DESC, invaso", (d,))]
    storia: dict = {}
    for r in con.execute("SELECT data, bacino, perc, volume_invasato FROM invasi WHERE invaso='TOTALE' ORDER BY data"):
        storia.setdefault(r[1], []).append([r[0], r[2], r[3]])
    sev = [dict(r) for r in con.execute("SELECT * FROM severita ORDER BY data DESC, area")]
    return {"data": d, "ultimi": ultimi, "storia": storia, "severita": sev}


def dati_bollettini(con) -> dict:
    storia = [dict(r) for r in con.execute("SELECT * FROM bollettini_arpav ORDER BY numero")]
    sez = {}
    if storia:
        sez = {r[0]: r[1] for r in con.execute(
            "SELECT sezione, testo FROM bollettini_sezioni WHERE numero=?", (storia[-1]["numero"],))}
    return {"storia": storia, "sezioni": sez}


_cache: dict = {"chiave": object(), "corpo": {}}


def tutto() -> bytes:
    con = _ro()
    try:
        chiave = con.execute("SELECT GROUP_CONCAT(ultima_fine) FROM raccolte").fetchone()[0]
        if chiave != _cache["chiave"]:
            corpo = dict(fiumi=dati_fiumi(con), falde=dati_falde(con),
                         invasi=dati_invasi(con), bollettini=dati_bollettini(con))
            _cache.update(chiave=chiave, corpo=corpo)
        out = dict(_cache["corpo"], generato=adesso(), server_avviato=_avviato,
                   raccolta_attiva=_raccolta_attiva, fonti=fonti(con))
        return json.dumps(out, ensure_ascii=False).encode()
    finally:
        con.close()


def serie(stazione: str, sensore: str, giorni: int) -> bytes:
    con = _ro()
    try:
        fine = con.execute("SELECT MAX(istante) FROM misure WHERE stazione_id=? AND sensore=?",
                           (stazione, sensore)).fetchone()[0]
        if not fine:
            return b'{"punti": []}'
        inizio = (datetime.fromisoformat(fine) - timedelta(days=giorni)).isoformat()
        rows = con.execute("SELECT istante, valore, unita FROM misure WHERE stazione_id=? AND sensore=? "
                           "AND istante>=? ORDER BY istante", (stazione, sensore, inizio)).fetchall()
        passo = max(1, len(rows) // 1500)  # al massimo ~1500 punti
        return json.dumps({"punti": [[r[0], r[1]] for r in rows[::passo]],
                           "unita": rows[-1][2] if rows else None}).encode()
    finally:
        con.close()


class Server(ThreadingHTTPServer):
    # Su Windows SO_REUSEADDR permette a due processi di ascoltare sulla stessa porta:
    # lo disattiviamo, cosi' se la porta e' gia' occupata l'avvio fallisce in modo chiaro.
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, corpo: bytes, tipo: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/tutto":
                return self._send(200, tutto())
            if u.path == "/api/serie":
                return self._send(200, serie(q["id"][0], q["sensore"][0], min(int(q.get("giorni", ["3"])[0]), 366 * 20)))
            nome = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / nome).resolve()
            if STATIC.resolve() in f.parents and f.is_file():
                tipo = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(f.suffix[1:], "text/plain")
                return self._send(200, f.read_bytes(), tipo + "; charset=utf-8")
            self._send(404, b'{"errore": "non trovato"}')
        except Exception as e:
            self._send(500, json.dumps({"errore": str(e)}).encode())

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/raccogli/"):
            job = u.path.rsplit("/", 1)[-1]
            if job not in JOBS:
                return self._send(404, b'{"errore": "job sconosciuto"}')
            if not _raccolta_attiva:
                return self._send(409, b'{"errore": "raccolta disattivata"}')
            avviato = lancia(job)
            return self._send(202, json.dumps({"avviato": avviato}).encode())
        self._send(404, b"{}")

    def log_message(self, *a):  # silenzia il log di ogni richiesta
        pass


def main(argv=None):
    global _raccolta_attiva
    ap = argparse.ArgumentParser(prog="monitor")
    ap.add_argument("--porta", type=int, default=8790)
    ap.add_argument("--senza-raccolta", action="store_true")
    ap.add_argument("--non-aprire", action="store_true", help="non aprire il browser")
    a = ap.parse_args(argv)
    db().close()  # crea lo schema se manca
    _raccolta_attiva = not a.senza_raccolta
    try:
        srv = Server(("127.0.0.1", a.porta), Handler)
    except OSError as e:
        raise SystemExit(f"Porta {a.porta} occupata ({e}). Riprova con: python -m monitor --porta <altra>")
    if _raccolta_attiva:
        threading.Thread(target=pianificatore, daemon=True, name="pianificatore").start()
    url = f"http://127.0.0.1:{a.porta}"
    log(f"monitor attivo su {url}  (raccolta automatica: {'sì' if _raccolta_attiva else 'no'}) - Ctrl+C per uscire")
    if not a.non_aprire:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

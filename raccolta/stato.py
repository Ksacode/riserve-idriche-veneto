"""Riepilogo a terminale dell'ultimo stato disponibile nel database."""
from __future__ import annotations

from .comune import db


def stampa():
    c = db()
    print("== RISERVE IDRICHE VENETO - ultimo stato nel database ==\n")

    r = c.execute("SELECT MAX(istante), COUNT(DISTINCT stazione_id) FROM misure WHERE stazione_id LIKE 'idro:%'").fetchone()
    print(f"Idrometri: {r[1]} stazioni, ultima misura {r[0]}")
    for nome, v, t in c.execute("""
        SELECT s.nome, m.valore, m.istante FROM misure m JOIN stazioni s ON s.id=m.stazione_id
        WHERE m.sensore='PORTATA' AND s.nome IN ('Adige a Boara Pisani','Brenta a Barzizza','Piave a Segusino',
              'Bacchiglione a Montegalda','Livenza a Meduna di Livenza','Piave a Belluno Ponte Vittoria')
          AND m.istante=(SELECT MAX(istante) FROM misure WHERE stazione_id=m.stazione_id AND sensore='PORTATA')"""):
        print(f"   {nome:35s} {v:8.1f} m3/s  ({t})")

    r = c.execute("SELECT MAX(istante), COUNT(DISTINCT stazione_id) FROM misure WHERE stazione_id LIKE 'falda:%'").fetchone()
    print(f"\nFalde: {r[1]} pozzi, ultima misura {r[0]}")

    d = c.execute("SELECT MAX(data) FROM invasi").fetchone()[0]
    print(f"\nInvasi strategici (al {d}):")
    for b, vi, vu, p in c.execute(
            "SELECT bacino, volume_invasato, volume_utile, perc FROM invasi WHERE data=? AND invaso='TOTALE' "
            "ORDER BY bacino='Distretto', bacino", (d,)):
        print(f"   {b:22s} {vi:6.1f} / {vu:6.1f} Mm3  {p:3.0f}%  {'#' * int(p // 5)}")

    d = c.execute("SELECT MAX(data) FROM severita").fetchone()[0]
    print(f"\nSeverita' idrica (seduta del {d}):")
    for a, comp, cl in c.execute("SELECT area, comparto, classe FROM severita WHERE data=?", (d,)):
        print(f"   {a} - acque {comp}: {cl.upper()}")

    b = c.execute("SELECT * FROM bollettini_arpav ORDER BY numero DESC LIMIT 1").fetchone()
    if b:
        cols = [x[0] for x in c.execute("SELECT * FROM bollettini_arpav LIMIT 0").description]
        b = dict(zip(cols, b))
        print(f"\nBollettino ARPAV n.{b['numero']} ({b['data']}):")
        print(f"   pioggia mese {b['precip_mm']} mm (scarto {b['precip_scarto_pct']}%)")
        print(f"   neve SWE Mm3: Piave {b['swe_piave_mmc']}, Cordevole {b['swe_cordevole_mmc']}, "
              f"Brenta {b['swe_brenta_mmc']}  SSPI {b['sspi']}")
        print(f"   serbatoi Piave {b['serbatoi_piave_mmc']} Mm3 ({b['serbatoi_piave_pct']}%), "
              f"Corlo {b['corlo_mmc']} Mm3 ({b['corlo_pct']}%)")
        g = c.execute("SELECT testo FROM bollettini_sezioni WHERE numero=? AND sezione='Lago di Garda'",
                      (b["numero"],)).fetchone()
        if g:
            print(f"   Garda: {g[0][:200]}...")

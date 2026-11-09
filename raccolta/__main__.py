"""Uso:
    python -m raccolta fiumi          # idrometri tempo reale (consigliato ogni 1-2 ore)
    python -m raccolta falde          # freatimetri (1 volta al giorno)
    python -m raccolta falde_storico  # medie giornaliere falde dal 2010 (la prima volta ~5 min)
    python -m raccolta invasi         # invasi + severita' idrica (1 volta al giorno, scarica solo PDF nuovi)
    python -m raccolta bollettini     # bollettino mensile ARPAV (1 volta al giorno, scarica solo i nuovi)
    python -m raccolta tutto
    python -m raccolta stato          # riepilogo ultimo stato
Opzioni: --da-anno 2025  --forza (rielabora PDF gia' visti)
"""
from __future__ import annotations

import argparse
import sys

from . import bollettini, falde, falde_storico, fiumi, invasi, stato
from .comune import esegui


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="raccolta")
    ap.add_argument("cosa", choices=["fiumi", "falde", "falde_storico", "invasi", "bollettini", "tutto", "stato"])
    ap.add_argument("--da-anno", type=int, default=2025)
    ap.add_argument("--forza", action="store_true")
    a = ap.parse_args(argv)
    if a.cosa == "stato":
        stato.stampa()
        return 0
    lavori = {
        "fiumi": lambda: fiumi.raccogli(a.forza),
        "falde": lambda: falde.raccogli(),
        "falde_storico": lambda: falde_storico.raccogli(a.forza),
        "invasi": lambda: invasi.raccogli(a.da_anno, a.forza),
        "bollettini": lambda: bollettini.raccogli(a.da_anno, a.forza),
    }
    errori = 0
    for nome in (lavori if a.cosa == "tutto" else [a.cosa]):
        esito, _ = esegui(nome, lavori[nome])
        errori += esito == "errore"
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())

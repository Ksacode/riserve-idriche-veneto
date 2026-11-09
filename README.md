# Riserve idriche del Veneto – raccolta dati

Raccoglie in un database SQLite (`dati/riserve.sqlite`) lo stato delle riserve idriche del Veneto
da 4 fonti pubbliche.

| Modulo | Cosa | Fonte | Formato | Aggiornamento fonte | Esecuzione consigliata |
|---|---|---|---|---|---|
| `fiumi` | Idrometri: livelli e portate, 146 stazioni (fiumi e torrenti, Canal Bianco, Lago di Garda a Peschiera, delta del Po, una cassa di espansione) | ARPAV, feed XML open data (`Ultime48ore.xml`) | XML | continuo, misure ogni ~10 min, ritardo ~30 min | ogni ora |
| `falde` | Livello di falda (m s.l.m.), 38 pozzi | ARPAV, API REST `api.arpa.veneto.it/REST/v1` | JSON | orario, ricaricato ~1 volta/giorno | 1/giorno |
| `falde_storico` | Medie giornaliere del livello di falda dal 2010 (riferimento per minimo/massimo storici) | ARPAV, API REST `meteo_storici` (pagina "Freatimetria ultimi anni") | JSON | mensile (intorno al 25, con il mese precedente) | 1/giorno; la prima volta ~4 min |
| `invasi` | Invasi strategici (volumi, % riempimento) e livello di **severità idrica** | Osservatorio permanente Alpi Orientali + report ISPRA | PDF | a ogni seduta (settimanale in crisi, ~mensile altrimenti) | 1/giorno (scarica solo PDF nuovi) |
| `bollettini` | Bollettino mensile ARPAV: pioggia, **neve (SWE, SSPI)**, serbatoi Piave/Corlo, Garda, falda, portate | ARPAV “Bollettino risorsa idrica” | PDF | mensile (fine mese) | 1/giorno (scarica solo i nuovi) |

## Uso

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

.\.venv\Scripts\python -m raccolta tutto     # prima raccolta (scarica anche lo storico PDF dal 2025)
.\.venv\Scripts\python -m raccolta stato     # riepilogo
.\.venv\Scripts\python -m raccolta fiumi     # solo un modulo
.\.venv\Scripts\python -m raccolta invasi --da-anno 2024 --forza
```

## Monitor in tempo reale

```powershell
.\.venv\Scripts\python -m monitor          # apre http://127.0.0.1:8790
.\.venv\Scripts\python -m monitor --porta 8800 --senza-raccolta --non-aprire
```

Il monitor è un server web locale che **fa anche la raccolta in background**, finché resta acceso:

| Fonte | Controllo del monitor | Note |
|---|---|---|
| Idrometri | ogni 5 min | richiesta condizionale (ETag): se il feed non è cambiato non scarica nulla |
| Falde | ogni 2 h | |
| Falde, archivio storico | ogni 24 h | gli anni chiusi si scaricano una volta sola |
| Invasi / severità | ogni 6 h | scarica solo PDF nuovi |
| Bollettino ARPAV | ogni 6 h | scarica solo PDF nuovi |

In cima c'è la mappa di idrometri e pozzi di falda: ogni stazione è un indicatore più o meno pieno in base al livello attuale. Per i pozzi pieno = massimo storico e vuoto = minimo storico (medie giornaliere dal 2010); i pozzi fuori dai valori storici sono cerchiati in rosso. La pagina si ricarica ogni 30 s. Per ogni fonte mostra: data dell'ultimo dato, quando la fonte l'ha
pubblicato, frequenza dichiarata della fonte (e, per gli idrometri, l'intervallo **osservato** fra
pubblicazioni del feed), ultimo e prossimo controllo, stato (aggiornato / in ritardo / non aggiornato)
e un pulsante “Controlla ora”.

In alternativa al monitor, raccolta senza interfaccia con le attività pianificate di Windows:
`powershell -ExecutionPolicy Bypass -File pianifica.ps1` (non usare entrambe insieme).

## Tabelle

- `stazioni` (`id` = `idro:<IDSTAZ>` o `falda:<codseqst>`), `misure` (serie temporali; `sensore` = LIVIDRO, PORTATA, PRECCUM, LFREAT…)
- `invasi` (una riga per invaso + `invaso='TOTALE'` per bacino e per `bacino='Distretto'`)
- `severita` (area, comparto acque superficiali/sotterranee, classe nulla/bassa/media/alta)
- `bollettini_arpav` (valori estratti) + `bollettini_sezioni` (testo integrale di ogni sezione)
- `documenti` (PDF già elaborati; copie locali in `dati/pdf/`)

## Avvertenze

- I dati in tempo reale (fiumi, falde) **non sono validati** e possono cambiare dopo la validazione.
- Orari: idrometri in ora solare (UTC+1) tutto l'anno. Falde: orario come restituito dall'API.
- L'endpoint falde non è documentato ufficialmente: può cambiare senza preavviso.
- I numeri dei PDF sono estratti con regole sul testo. Quando le percentuali non tornano con i volumi
  vengono ricalcolate (es. refuso “TOTALE DISTRETTO 741.2 435 11%” nel bollettino OP n.8/2026),
  ma eventuali errori nei volumi stessi della fonte restano.
- Il livello del Lago di Garda è misurato in tempo reale dall'idrometro di Peschiera (feed idrometri); il bollettino ARPAV ne riporta anche il commento mensile.
- Le stazioni sul delta del Po (Porto Levante, Porto Barricata) risentono della marea.
- Il "riempimento" degli idrometri nel monitor è la posizione del livello attuale fra minimo e massimo **registrati dal monitor** per quella stazione (escursione minima 20 cm): non è una soglia ufficiale e diventa più significativo man mano che lo storico cresce.
- Licenza dati ARPAV: CC BY 4.0, citare “ARPAV” come fonte.

## Pubblicare gratis su GitHub Pages

Il sito pubblico è una versione statica del monitor: GitHub Actions (`.github/workflows/aggiorna.yml`)
ogni 15 minuti esegue `python -m monitor.esporta --raccogli --out sito` (raccoglie solo le fonti
"scadute" secondo gli intervalli del monitor) e pubblica la cartella `sito/` su GitHub Pages.
Il database resta fra un lancio e l'altro come allegato `riserve.sqlite.gz` della release `dati`.
Gli idrometri più vecchi di 7 giorni vengono ridotti a una misura all'ora per contenere le dimensioni.

1. Crea un repository **pubblico** su github.com (es. `riserve-idriche-veneto`), senza README.
2. Dalla cartella del progetto:
   ```powershell
   git init -b main
   git add .
   git commit -m "Monitor riserve idriche del Veneto"
   git remote add origin https://github.com/<utente>/riserve-idriche-veneto.git
   git push -u origin main
   ```
3. Su GitHub: *Settings → Pages → Build and deployment → Source: GitHub Actions*.
4. *Actions → Aggiorna dati e sito → Run workflow* per il primo lancio (circa 10 minuti: scarica anche
   lo storico delle falde e i PDF). Poi riparte da solo ogni 15 minuti.
5. Il sito è su `https://<utente>.github.io/riserve-idriche-veneto/`.

Note: con repository pubblico i minuti di Actions sono gratuiti; GitHub può ritardare i lanci
programmati e li sospende se il repository resta senza attività per 60 giorni (si riattivano dalla
scheda Actions). Prova in locale: `python -m monitor.esporta --out sito` e poi
`python -m http.server -d sito`.

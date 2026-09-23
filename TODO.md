# TODO

Fonte di verità per scope e sequenza (in italiano). `CLAUDE.md` e `README.md` restano
sintetici e puntano qui — non duplicare i dettagli lì.

## Fase 1 — Narrativa a singolo agente (WhatsGoingOn)

- [x] Data layer: FRED + Yahoo Finance → SQLite, idempotente (`sources/`, `db.py`)
- [x] Agent loop hand-rolled: delta 30gg → tool calling → narrativa (`agent/loop.py`, `agent/state.py`, `agent/tools.py`)
- [x] RAG minimale per continuità: Chroma locale (`agent/narrative_store.py`)
- [x] FastAPI wrapper (`GET /state`, `POST /refresh`) + demo page minimale
- [x] Dockerfile (uso locale, nessun deploy hosted per scelta)
- [x] Logging strutturato delle decisioni dell'agente (`logging_config.py`)
- [x] CI (`.github/workflows/ci.yml`)
- [x] Export Obsidian (bonus non pianificato: `agent/obsidian_export.py`)

## Fase 2 — Orchestrazione multi-agente

Estende questo stesso repo (non un repo separato — riuso diretto di `db.py`, `sources/`,
`narrative_store.py`, `config.py`, `retry.py` invece di duplicarli altrove). Nuovo
package `whatsgoingon/orchestrator/`, `agent/` (v1) resta invariato così i due possono
girare fianco a fianco e essere confrontati.

Niente framework (LangGraph/CrewAI ecc.) anche qui — l'obiettivo è vedere i meccanismi
di coordinamento tra agenti, non nasconderli dietro un'astrazione.

Il sistema: newsroom a 3 agenti sui dati macro — **Analyst** (bozza con claim numerici
espliciti, riusa `agent/state.py` per i delta) → **Skeptic** (critica la bozza: claim
non supportati, causalità affrettata, confidence gonfiata; può rimandarla indietro,
max N giri) → **Editor** (decide se pubblicare o rifare un giro, produce la finale +
changelog di cosa è cambiato e perché).

### Settimana 1 — Message passing e orchestratore sequenziale
- [x] Schema di stato condiviso tra agenti (draft con claim taggati, lista critiche con
      severità, storico dei giri)
- [x] Orchestratore hand-rolled: Analyst → Skeptic → (loop Analyst se serve) → Editor,
      con contatore di giri massimo (evita loop infiniti)
- [x] Log strutturato per-agente (chi ha detto cosa, che tool call, quanti token) —
      estende `logging_config.py`
- [x] Un ciclo completo gira da CLI (`whatsgoingon-debate`): bozza + critica + finale, tutto tracciato

### Settimana 2 — Concorrenza, budget, fallimenti
- [ ] `asyncio` dove ha senso far girare agenti in parallelo (es. Skeptic + un quarto
      agente "Context" che cerca news mentre l'Analyst pensa)
- [ ] Budget per-agente e per-ciclo (token/costo), con fallback esplicito invece di
      fallire silenziosamente
- [ ] Timeout e retry per agente (estende `retry.py`) con degradazione controllata
      (es. Editor pubblica senza critica se lo Skeptic non risponde, e lo segnala)
- [ ] Test che mockano ogni agente separatamente e verificano l'orchestrazione, non
      solo l'output finale

### Settimana 3 — Produzione
- [ ] Endpoint API che espone anche la trascrizione del dibattito (bozza, critiche,
      finale, changelog), non solo il risultato finale
- [ ] Costo e latenza per-agente loggati e aggregati (numero concreto: costo di un
      ciclo a 3 agenti vs 1 del mese scorso)
- [ ] Deploy vero su Fly.io o Render (a differenza della Fase 1, rimasta locale per
      scelta) + env var per i budget

### Settimana 4 — Rifinitura e demo
- [ ] CI estesa (riusa `.github/workflows/ci.yml` come base)
- [ ] UI minimale che mostra il dibattito come conversazione (bozza → critica →
      revisione → finale), non solo il testo finale
- [ ] README/CLAUDE.md aggiornati: quando il pattern debate/critique ha davvero
      cambiato l'output (esempi concreti dai run) vs quando è stato solo overhead
- [ ] Bonus se c'è tempo: agente "Contrarian" che argomenta la tesi opposta (debate a
      2 vs critique a 1)

## Working conventions (promemoria)

- Infrastruttura più semplice possibile per lo scope del mese (SQLite, Chroma locale,
  niente framework agentico) — vale anche per la Fase 2
- Agent loop hand-rolled: i meccanismi di tool calling e coordinamento restano visibili
- Git deliberato anche da soli: commit strutturati, branch, PR

# TODO

Fonte di verità per scope e sequenza (in italiano). `CLAUDE.md` e `README.md` restano
sintetici e puntano qui — non duplicare i dettagli lì.

## Stato attuale (aggiornato 2026-09-23)

- **Branch:** `feat/orchestrator-week1` — codice Settimana 1 committato e pushato
  (`ac7d858`); PR verso `main` non ancora aperta. Non committati: questo `TODO.md`,
  `.gitignore` (smette di ignorare `CLAUDE.md`, ignora `past_conversations/`), `CLAUDE.md`.
- **Test/lint:** 118 passed / ruff ok.
- **Fatto nell'ultima sessione:** run live `whatsgoingon-debate --month 2026-09 --max-rounds 2`
  ok: 2 giri, lo Skeptic ha colto un CPI di luglio spacciato per settembre; ~53k token in /
  4.6k out su Haiku 4.5 (~$0.08, stima). Eliminato `test_orchestrator_stubs.py` (testava solo
  i vecchi stub `NotImplementedError`).
- **Problemi aperti (dalla run live):**
  1. Analyst al giro 1 esaurisce i 6 turni di `call_structured` e viene forzato a consegnare
     (troppe tool call di ricerca).
  2. Claim id rinominati tra un giro e l'altro (`c11` → `c11_revised`) nonostante il prompt.
  3. I log dello Skeptic non hanno `round` (`critique_draft` riceve solo il draft).
  4. L'Analyst non riceve le narrative dei mesi passati (`NarrativeStore`), a differenza
     della Fase 1.
  5. Editor forzato a pubblicare segnala la critica high irrisolta nel changelog ma non
     ammorbidisce il testo finale — da verificare se è un problema di prompt.
- **Decisioni recenti:** l'ultimo giro pubblica sempre (niente "revise" nello schema
  dell'Editor) → il ciclo termina per costruzione; il modello nomina solo le serie, i
  `SeriesDelta` li attacca il codice → nessun numero inventato nei claim; critiche high
  saltano l'Editor finché restano giri.
- **Prossimo passo:** sistemare i punti 1–4 (partire dal 2: validare in `produce_draft` che
  gli id dei claim riportati coincidano con quelli del giro prima), poi aprire la PR, poi
  Settimana 2.

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
- [x] `asyncio` dove ha senso far girare agenti in parallelo (es. Skeptic + un quarto
      agente "Context" che cerca news mentre l'Analyst pensa)
- [x] Budget per-agente e per-ciclo (token/costo), con fallback esplicito invece di
      fallire silenziosamente
- [x] Timeout e retry per agente (estende `retry.py`) con degradazione controllata
      (es. Editor pubblica senza critica se lo Skeptic non risponde, e lo segnala)
- [x] Test che mockano ogni agente separatamente e verificano l'orchestrazione, non
      solo l'output finale

### Settimana 3 — Produzione
- [x] Endpoint API che espone anche la trascrizione del dibattito (bozza, critiche,
      finale, changelog), non solo il risultato finale — `POST /debate`, `GET /debate`,
      `GET /debate/{month}`; trascrizioni salvate in `data/debates/<mese>.json`
- [x] Costo e latenza per-agente loggati e aggregati (numero concreto: costo di un
      ciclo a 3 agenti vs 1 del mese scorso) — run live 2026-09-25, 2 run per setup su
      Haiku 4.5: Fase 1 ~$0.016 / ~20s, dibattito $0.19–0.25 / 97–131s (~12–16x il costo).
      L'Analyst pesa il 56–61% perché rifà ricerca a ogni revisione. Tabella e dettagli nel
      README ("Cost: one agent vs. a three-agent debate")

### Settimana 4 — Rifinitura e demo
- [ ] CI estesa (riusa `.github/workflows/ci.yml` come base)
- [ ] UI minimale che mostra il dibattito come conversazione (bozza → critica →
      revisione → finale), non solo il testo finale
      — parziale: la pagina demo ha lo switch singolo agente / dibattito e una barra di
      avanzamento; in modalità dibattito mostra finale, giri/critiche/costo/tempo e il
      changelog, ma non ancora le bozze e le critiche giro per giro
- [ ] README/CLAUDE.md aggiornati: quando il pattern debate/critique ha davvero
      cambiato l'output (esempi concreti dai run) vs quando è stato solo overhead
- [ ] Publish on GitHub pages

## Working conventions (promemoria)

- Infrastruttura più semplice possibile per lo scope del mese (SQLite, Chroma locale,
  niente framework agentico) — vale anche per la Fase 2
- Agent loop hand-rolled: i meccanismi di tool calling e coordinamento restano visibili
- Git deliberato anche da soli: commit strutturati, branch, PR

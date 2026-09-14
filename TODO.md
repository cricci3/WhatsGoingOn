# WhatsGoingOn

Pipeline dati reale, un agente con tool use e stato persistente, RAG per la memoria/coerenza nel tempo, API, containerizzazione, deploy, test, CI.

**Struttura del mese:**

**Settimana 1 — Setup SWE + data layer**
- Repo strutturato come si deve: package Python vero (non notebook), `pyproject.toml`, pytest. 
- Script che tira giù da FRED 5-6 serie chiave (CPI, GDP, Fed Funds Rate, 10Y yield, unemployment, magari oil/energy da EIA), le salva in SQLite.
- Test sul data layer (mock delle chiamate API, validazione schema).
- Fine settimana: hai dati puliti e uno script che li aggiorna, testato.

**Settimana 2 — L'agente, versione minima ma vera**
- Loop agentico "from scratch" (no framework, per capire i meccanismi): 
  1. L'agente guarda lo stato attuale vs 30gg fa (delta sui numeri).
  2. Decide, tramite function calling, se serve approfondire qualcosa (es: "CPI su, energy su → chiama tool che guarda solo la componente energy del CPI").
  3. Chiama i tool necessari (query SQL sui tuoi dati, magari 1 chiamata a NewsAPI free tier per contesto).
  4. Scrive la narrativa finale.
- Qui tocchi: function/tool calling nativo, gestione dello stato tra step, error handling sulle chiamate.
- RAG minimale: salvi ogni narrativa mensile generata in un vector store (anche solo Chroma locale) e la usi come contesto per la narrativa successiva, così il "world model" ha continuità e non si contraddice mese su mese.

**Settimana 3 — Produzione**
- Wrappa tutto in FastAPI (endpoint tipo `/state` che ritorna l'ultima interpretazione, `/refresh` che triggera un nuovo ciclo).
- Dockerfile, docker-compose se separi API e eventuale DB. Progetto hobby: gira in locale (`docker run` / `uv run`), nessun deploy su hosting esterno.
- Logging strutturato delle decisioni dell'agente (che tool ha chiamato, perché, con che input/output) — è il pezzo che ti insegna osservabilità, sottovalutata ma richiestissima.

**Settimana 4 — Rifinitura, CI/CD, storytelling**
- GitHub Actions: test automatici a ogni push, deploy automatico su merge.
- Piccola UI (anche solo una pagina HTML/Streamlit che chiama l'API) per rendere il progetto demo-abile in un colloquio in 30 secondi.
- README serio con architettura, limiti dichiarati onestamente (specialmente sulla confidence "finta" — dichiararlo mostra maturità, non debolezza).
- Bonus se hai tempo: uno o due test end-to-end che simulano un intero ciclo dell'agente.

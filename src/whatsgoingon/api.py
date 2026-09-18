from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.run import run_cycle
from whatsgoingon.logging_config import configure_logging

logger = logging.getLogger(__name__)

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhatsGoingOn</title>
<script>
  (function () {
    try {
      var stored = localStorage.getItem("wgo-theme");
      if (stored === "light" || stored === "dark") {
        document.documentElement.setAttribute("data-theme", stored);
      }
    } catch (e) {}
  })();
</script>
<style>
  :root {
    color-scheme: light dark;
    --bg-start: #eef1ff;
    --bg-end: #f8f9fc;
    --card-bg: #ffffff;
    --text: #1a1a2e;
    --text-muted: #6b7280;
    --accent: #4f46e5;
    --accent-hover: #4338ca;
    --accent-soft: #eef0fe;
    --border: #e5e7eb;
    --error: #b91c1c;
    --error-bg: #fef2f2;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg-start: #0f1023;
      --bg-end: #16172c;
      --card-bg: #1c1d33;
      --text: #e8e9f3;
      --text-muted: #9497b3;
      --accent: #818cf8;
      --accent-hover: #a5b0fb;
      --accent-soft: #292a4a;
      --border: #33345a;
      --error: #f87171;
      --error-bg: #3a1a1a;
    }
  }
  :root[data-theme="dark"] {
    --bg-start: #0f1023;
    --bg-end: #16172c;
    --card-bg: #1c1d33;
    --text: #e8e9f3;
    --text-muted: #9497b3;
    --accent: #818cf8;
    --accent-hover: #a5b0fb;
    --accent-soft: #292a4a;
    --border: #33345a;
    --error: #f87171;
    --error-bg: #3a1a1a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--text);
    background: linear-gradient(160deg, var(--bg-start), var(--bg-end));
    display: flex;
    justify-content: center;
    padding: 3rem 1.25rem;
    transition: background 0.2s ease, color 0.2s ease;
  }
  .page { width: 100%; max-width: 640px; position: relative; }
  header { text-align: center; margin-bottom: 2rem; }
  header h1 { font-size: 1.75rem; margin: 0 0 0.3rem; letter-spacing: -0.02em; }
  header p { margin: 0; color: var(--text-muted); font-size: 0.95rem; }
  .theme-toggle {
    position: absolute;
    top: 0;
    right: 0;
    width: 2.4rem;
    height: 2.4rem;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--card-bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: 1.1rem;
    border-radius: 999px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }
  .theme-toggle:hover { background: var(--accent-soft); }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 1rem;
    padding: 1.75rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 20px 40px -20px rgba(30,30,60,0.25);
    transition: background 0.2s ease, border-color 0.2s ease;
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1.1rem;
    flex-wrap: wrap;
    min-height: 1.6rem;
  }
  .month-badge {
    font-weight: 600;
    font-size: 0.9rem;
    background: var(--accent-soft);
    color: var(--accent);
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
  }
  .timestamp { color: var(--text-muted); font-size: 0.8rem; }
  .narrative { line-height: 1.65; font-size: 1.02rem; }
  .narrative p { margin: 0 0 1rem; }
  .narrative p:last-child { margin-bottom: 0; }
  .narrative h1, .narrative h2, .narrative h3 {
    line-height: 1.3;
    margin: 1.4rem 0 0.6rem;
  }
  .narrative h1:first-child, .narrative h2:first-child, .narrative h3:first-child { margin-top: 0; }
  .narrative h1 { font-size: 1.3rem; }
  .narrative h2 { font-size: 1.15rem; }
  .narrative h3 { font-size: 1.05rem; }
  .narrative ul, .narrative ol { margin: 0 0 1rem; padding-left: 1.4rem; }
  .narrative li { margin-bottom: 0.4rem; }
  .narrative code {
    background: var(--accent-soft);
    color: var(--accent);
    padding: 0.1rem 0.35rem;
    border-radius: 0.3rem;
    font-size: 0.9em;
  }
  .empty-state { text-align: center; color: var(--text-muted); padding: 0.5rem 0 0.75rem; }
  .controls {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 0.6rem;
    margin-top: 1.75rem;
    flex-wrap: wrap;
  }
  input[type="month"] {
    border: 1px solid var(--border);
    background: var(--card-bg);
    color: var(--text);
    border-radius: 0.6rem;
    padding: 0.55rem 0.7rem;
    font-size: 0.85rem;
    accent-color: var(--accent);
  }
  button {
    cursor: pointer;
    border: none;
    background: var(--accent);
    color: white;
    font-size: 0.95rem;
    font-weight: 600;
    padding: 0.65rem 1.3rem;
    border-radius: 0.6rem;
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    transition: background 0.15s ease, transform 0.1s ease;
  }
  button:hover:not(:disabled) { background: var(--accent-hover); }
  button:active:not(:disabled) { transform: scale(0.98); }
  button:disabled { opacity: 0.75; cursor: default; }
  .spinner {
    width: 0.85rem;
    height: 0.85rem;
    border: 2px solid rgba(255,255,255,0.4);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: none;
  }
  button.loading .spinner { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .message {
    margin-top: 1rem;
    text-align: center;
    font-size: 0.9rem;
    color: var(--error);
    background: var(--error-bg);
    border-radius: 0.6rem;
    padding: 0.6rem 0.9rem;
    display: none;
  }
  .message.visible { display: block; }
  .fade-in { animation: fadeIn 0.35s ease; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
</style>
</head>
<body>
<div class="page">
  <button class="theme-toggle" id="themeToggle" type="button"
          aria-label="Toggle color theme" title="Toggle color theme">
    <span id="themeIcon">🌙</span>
  </button>
  <header>
    <h1>WhatsGoingOn</h1>
    <p>An agent's monthly read on macro data and markets</p>
  </header>
  <div class="card" id="card">
    <div class="card-header">
      <span class="month-badge" id="monthBadge"></span>
      <span class="timestamp" id="timestamp"></span>
    </div>
    <div class="narrative" id="narrative">Loading…</div>
  </div>
  <div class="controls">
    <input type="month" id="month" aria-label="Month to generate">
    <button id="refresh">
      <span class="spinner"></span>
      <span id="refreshLabel">Generate new narrative</span>
    </button>
  </div>
  <div class="message" id="message"></div>
</div>
<script>
  const monthInput = document.getElementById("month");
  const refreshBtn = document.getElementById("refresh");
  const refreshLabel = document.getElementById("refreshLabel");
  const monthBadge = document.getElementById("monthBadge");
  const timestampEl = document.getElementById("timestamp");
  const narrativeEl = document.getElementById("narrative");
  const messageEl = document.getElementById("message");
  const cardEl = document.getElementById("card");
  const themeToggle = document.getElementById("themeToggle");
  const themeIcon = document.getElementById("themeIcon");

  function currentTheme() {
    const explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function updateThemeIcon() {
    themeIcon.textContent = currentTheme() === "dark" ? "☀️" : "🌙";
  }

  themeToggle.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    updateThemeIcon();
    try {
      localStorage.setItem("wgo-theme", next);
    } catch {}
  });

  updateThemeIcon();

  monthInput.value = new Date().toISOString().slice(0, 7);

  function formatTimestamp(iso) {
    if (!iso) return "";
    try {
      return "Generated " + new Date(iso).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
    } catch {
      return "";
    }
  }

  function playFadeIn() {
    cardEl.classList.remove("fade-in");
    void cardEl.offsetWidth;
    cardEl.classList.add("fade-in");
  }

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderInline(text) {
    let out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\\*([^*\\n]+)\\*(?!\\*)/g, "$1<em>$2</em>");
    out = out.replace(/(^|[^_])_([^_\\n]+)_(?!_)/g, "$1<em>$2</em>");
    return out;
  }

  // Small hand-rolled renderer for the subset of Markdown the model actually
  // produces (headers, bold/italic, code, lists, paragraphs) - not full CommonMark.
  function markdownToHtml(text) {
    const lines = text.replace(/\\r\\n/g, "\\n").split("\\n");
    const htmlParts = [];
    let paragraphLines = [];
    let listTag = null;
    let listItems = null;

    function flushParagraph() {
      if (paragraphLines.length) {
        htmlParts.push("<p>" + paragraphLines.map(renderInline).join("<br>") + "</p>");
        paragraphLines = [];
      }
    }
    function flushList() {
      if (listItems) {
        const items = listItems.map((item) => "<li>" + renderInline(item) + "</li>").join("");
        htmlParts.push("<" + listTag + ">" + items + "</" + listTag + ">");
        listItems = null;
        listTag = null;
      }
    }

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (line === "") {
        flushParagraph();
        flushList();
        continue;
      }
      const headingMatch = line.match(/^(#{1,6})\\s+(.*)$/);
      if (headingMatch) {
        flushParagraph();
        flushList();
        const level = headingMatch[1].length;
        htmlParts.push("<h" + level + ">" + renderInline(headingMatch[2]) + "</h" + level + ">");
        continue;
      }
      const ulMatch = line.match(/^[-*]\\s+(.*)$/);
      const olMatch = line.match(/^\\d+\\.\\s+(.*)$/);
      if (ulMatch || olMatch) {
        flushParagraph();
        const tag = ulMatch ? "ul" : "ol";
        if (listTag !== tag) {
          flushList();
          listTag = tag;
          listItems = [];
        }
        listItems.push((ulMatch || olMatch)[1]);
        continue;
      }
      flushList();
      paragraphLines.push(line);
    }
    flushParagraph();
    flushList();
    return htmlParts.join("");
  }

  function showNarrative(data) {
    monthBadge.textContent = data.month;
    timestampEl.textContent = formatTimestamp(data.generated_at);
    narrativeEl.innerHTML = markdownToHtml(data.narrative);
    playFadeIn();
    clearMessage();
  }

  function showEmptyState() {
    monthBadge.textContent = "";
    timestampEl.textContent = "";
    narrativeEl.innerHTML = '<div class="empty-state">No narrative yet — generate the first one below.</div>';
    playFadeIn();
  }

  function showMessage(text) {
    messageEl.textContent = text;
    messageEl.classList.add("visible");
  }

  function clearMessage() {
    messageEl.textContent = "";
    messageEl.classList.remove("visible");
  }

  async function loadState() {
    try {
      const res = await fetch("/state");
      if (res.status === 404) {
        showEmptyState();
        return;
      }
      if (!res.ok) throw new Error("failed to load state");
      showNarrative(await res.json());
    } catch (err) {
      showEmptyState();
      showMessage("Couldn't load the latest narrative: " + err);
    }
  }

  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    refreshBtn.classList.add("loading");
    refreshLabel.textContent = "Generating…";
    clearMessage();
    try {
      const res = await fetch("/refresh?month=" + encodeURIComponent(monthInput.value), { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        showMessage(data.detail || "Refresh failed.");
      } else {
        showNarrative(data);
      }
    } catch (err) {
      showMessage("Refresh failed: " + err);
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.classList.remove("loading");
      refreshLabel.textContent = "Generate new narrative";
    }
  });

  loadState();
</script>
</body>
</html>
"""


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    yield


app = FastAPI(title="WhatsGoingOn", description="Monthly macro/markets narrative agent", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Minimal demo page: shows the latest narrative and a Refresh button that
    triggers a new cycle for a chosen month."""
    return _INDEX_HTML


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/state")
def state() -> dict[str, str | None]:
    """Return the most recent narrative the agent has produced."""
    latest = NarrativeStore().get_latest_narrative()
    if latest is None:
        raise HTTPException(status_code=404, detail="No narrative has been generated yet")
    return latest


@app.post("/refresh")
def refresh(month: str | None = None) -> dict[str, str | None]:
    """Trigger a new agent cycle (fetches deltas, calls tools as needed, writes a new
    narrative) and return it. Synchronous: this can take from several seconds up to
    roughly the iteration budget's worth of Claude API calls."""
    month = month or date.today().strftime("%Y-%m")
    try:
        return run_cycle(month=month)
    except RuntimeError as exc:
        logger.warning("refresh failed", extra={"month": month, "error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def main() -> int:
    uvicorn.run("whatsgoingon.api:app", host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

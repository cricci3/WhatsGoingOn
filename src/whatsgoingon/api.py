from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse

from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.run import run_cycle
from whatsgoingon.logging_config import configure_logging
from whatsgoingon.orchestrator.orchestrator import AGENT_FAILURES, DEFAULT_MAX_ROUNDS
from whatsgoingon.orchestrator.run import run_debate
from whatsgoingon.orchestrator.transcript import (
    load_latest_transcript,
    load_transcript,
    save_transcript,
    transcript_to_dict,
)

logger = logging.getLogger(__name__)

MONTH_PATTERN = r"^\d{4}-\d{2}$"

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
    --warning: #92400e;
    --warning-bg: #fffbeb;
    --analyst: #4f46e5;
    --context: #0f766e;
    --skeptic: #b45309;
    --editor: #15803d;
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
      --warning: #fbbf24;
      --warning-bg: #3a2f0f;
      --analyst: #818cf8;
      --context: #2dd4bf;
      --skeptic: #fbbf24;
      --editor: #4ade80;
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
    --warning: #fbbf24;
    --warning-bg: #3a2f0f;
    --analyst: #818cf8;
    --context: #2dd4bf;
    --skeptic: #fbbf24;
    --editor: #4ade80;
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
  .message.warning { color: var(--warning); background: var(--warning-bg); }
  .fade-in { animation: fadeIn 0.35s ease; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .mode-switch {
    display: flex;
    margin: 0 auto 1.25rem;
    width: fit-content;
    background: var(--accent-soft);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.25rem;
    gap: 0.2rem;
  }
  .mode-switch button {
    background: transparent;
    color: var(--text-muted);
    font-size: 0.85rem;
    padding: 0.45rem 1rem;
    border-radius: 999px;
  }
  .mode-switch button:hover:not(:disabled) { background: transparent; color: var(--text); }
  .mode-switch button[aria-pressed="true"],
  .mode-switch button[aria-pressed="true"]:hover:not(:disabled) {
    background: var(--card-bg);
    color: var(--accent);
    box-shadow: 0 1px 3px rgba(0,0,0,0.12);
  }
  .mode-hint { text-align: center; color: var(--text-muted); font-size: 0.8rem; margin: -0.6rem 0 1.25rem; }
  .debate-meta {
    margin-top: 1.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  .debate-meta[hidden] { display: none; }
  .debate-stats { display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; }
  .debate-meta details { margin-top: 0.75rem; }
  .debate-meta summary { cursor: pointer; color: var(--accent); font-weight: 600; }
  .debate-meta .changelog { margin-top: 0.5rem; color: var(--text); line-height: 1.55; }
  .convo {
    list-style: none;
    margin: 0.75rem 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .convo-round {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-top: 0.4rem;
  }
  .msg {
    border: 1px solid var(--border);
    border-left: 3px solid var(--agent, var(--accent));
    border-radius: 0.6rem;
    padding: 0.6rem 0.8rem;
    color: var(--text);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .msg.analyst { --agent: var(--analyst); }
  .msg.context { --agent: var(--context); }
  .msg.skeptic { --agent: var(--skeptic); }
  .msg.editor { --agent: var(--editor); }
  .msg.fallback { --agent: var(--warning); background: var(--warning-bg); }
  .msg-who { font-weight: 700; font-size: 0.8rem; color: var(--agent); margin-bottom: 0.25rem; }
  .msg-who span { font-weight: 400; color: var(--text-muted); }
  .msg p { margin: 0 0 0.5rem; }
  .msg p:last-child { margin-bottom: 0; }
  .msg ul { margin: 0.25rem 0 0; padding-left: 1.2rem; }
  .msg li { margin-bottom: 0.35rem; }
  .msg details { margin-top: 0.4rem; }
  .msg .draft-text { margin-top: 0.5rem; font-size: 0.92rem; }
  .msg .quote { display: block; color: var(--text-muted); font-size: 0.85em; font-style: italic; }
  .sev {
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    padding: 0.05rem 0.4rem;
    border-radius: 999px;
    margin-right: 0.3rem;
    background: var(--accent-soft);
    color: var(--text-muted);
  }
  .sev.medium { background: var(--warning-bg); color: var(--warning); }
  .sev.high { background: var(--error-bg); color: var(--error); }
  .progress { margin-top: 1.25rem; }
  .progress[hidden] { display: none; }
  .progress-track {
    height: 0.5rem;
    background: var(--accent-soft);
    border-radius: 999px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    width: 0;
    background: linear-gradient(90deg, var(--accent), var(--accent-hover));
    border-radius: 999px;
    transition: width 0.3s ease;
  }
  .progress-label { margin-top: 0.5rem; text-align: center; font-size: 0.82rem; color: var(--text-muted); }
  @media (prefers-reduced-motion: reduce) {
    .progress-fill, .fade-in, button { transition: none; animation: none; }
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
  <div class="mode-switch" role="group" aria-label="Narrative version">
    <button type="button" id="modeSingle" data-mode="single" aria-pressed="true">Single agent</button>
    <button type="button" id="modeDebate" data-mode="debate" aria-pressed="false">Multi-agent debate</button>
  </div>
  <p class="mode-hint" id="modeHint"></p>
  <div class="card" id="card">
    <div class="card-header">
      <span class="month-badge" id="monthBadge"></span>
      <span class="timestamp" id="timestamp"></span>
    </div>
    <div class="narrative" id="narrative">Loading…</div>
    <div class="debate-meta" id="debateMeta" hidden></div>
  </div>
  <div class="controls">
    <input type="month" id="month" aria-label="Month to generate">
    <button id="refresh">
      <span class="spinner"></span>
      <span id="refreshLabel">Generate new narrative</span>
    </button>
  </div>
  <div class="progress" id="progress" hidden>
    <div class="progress-track" role="progressbar" aria-label="Agents at work"
         aria-valuemin="0" aria-valuemax="100" id="progressTrack">
      <div class="progress-fill" id="progressFill"></div>
    </div>
    <div class="progress-label" id="progressLabel" aria-live="polite"></div>
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
  const modeButtons = document.querySelectorAll(".mode-switch button");
  const modeHint = document.getElementById("modeHint");
  const debateMetaEl = document.getElementById("debateMeta");
  const progressEl = document.getElementById("progress");
  const progressTrack = document.getElementById("progressTrack");
  const progressFill = document.getElementById("progressFill");
  const progressLabel = document.getElementById("progressLabel");

  // The two versions of the pipeline: Phase 1's single agent and Phase 2's debate
  // (Analyst + Context in parallel, then Skeptic and Editor, up to 3 rounds).
  // expectedS is a typical run's duration from live runs; it only drives the progress
  // bar, since the endpoints don't report progress while they work.
  const MODES = {
    single: {
      stateUrl: "/state",
      runUrl: "/refresh",
      buttonLabel: "Generate new narrative",
      busyLabel: "Generating…",
      working: "The agent is reading the data and writing",
      hint: "One agent researches and writes the narrative (~20s).",
      empty: "No narrative yet — generate the first one below.",
      expectedS: 25,
    },
    debate: {
      stateUrl: "/debate",
      runUrl: "/debate",
      buttonLabel: "Run the agent debate",
      busyLabel: "Debating…",
      working: "Analyst, Context, Skeptic and Editor are debating",
      hint: "Analyst drafts, Skeptic critiques, Editor publishes or sends it back (~2 min, ~12x the cost).",
      empty: "No debate yet — run the first one below.",
      expectedS: 120,
    },
  };
  let mode = "single";
  let busy = false;
  let progressTimer = null;

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
    debateMetaEl.hidden = true;
    monthBadge.textContent = data.month;
    timestampEl.textContent = formatTimestamp(data.generated_at);
    narrativeEl.innerHTML = markdownToHtml(data.narrative);
    playFadeIn();
    if (data.warning) {
      showMessage(data.warning, { warning: true });
    } else {
      clearMessage();
    }
  }

  function agentMessage(agent, who, bodyHtml) {
    return '<li class="msg ' + agent + '"><div class="msg-who">' + who + "</div>" + bodyHtml + "</li>";
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  // The transcript as a conversation: Context's brief, then per round the Analyst's draft,
  // the Skeptic's critiques and what the Editor (or the orchestrator) did with them, then
  // the Editor's publishing decision. Fallbacks appear in the round they happened.
  function debateConversationHtml(data) {
    const parts = [];
    if (data.context) {
      const events = data.context.events.map((e) => {
        const related = e.related_series.length ? '<span class="quote">' +
          escapeHtml(e.related_series.join(", ")) + "</span>" : "";
        return "<li>" + renderInline(e.summary) + related + "</li>";
      }).join("");
      let body = events ? "<ul>" + events + "</ul>" : "<p>No relevant news found.</p>";
      if (data.context.notes) body += "<p>" + renderInline(data.context.notes) + "</p>";
      parts.push(agentMessage("context", "Context <span>· news brief, in parallel with draft 1</span>",
        body));
    }
    const fallbacksByRound = {};
    (data.fallbacks || []).forEach((f) => {
      // A failed Analyst revision adds no round of its own, so it can point past the last
      // recorded round - show it there rather than drop it.
      const round = Math.min(f.round, data.rounds.length);
      (fallbacksByRound[round] = fallbacksByRound[round] || []).push(f);
    });
    data.rounds.forEach((r) => {
      const last = r.round === data.rounds.length;
      parts.push('<li class="convo-round">Round ' + r.round + "</li>");

      const claims = {};
      r.draft.claims.forEach((c) => { claims[c.id] = c; });
      parts.push(agentMessage("analyst",
        "Analyst <span>· " + (r.round === 1 ? "first draft" : "revision") + ", " +
          plural(r.draft.claims.length, "claim") + "</span>",
        "<details><summary>Read draft " + r.round + "</summary>" +
          '<div class="draft-text">' + markdownToHtml(r.draft.narrative) + "</div></details>"));

      if (r.review_skipped) {
        parts.push(agentMessage("skeptic", "Skeptic",
          "<p>Didn't review this draft: " + renderInline(r.review_skipped) + "</p>"));
      } else if (r.critiques.length === 0) {
        parts.push(agentMessage("skeptic", "Skeptic", "<p>No objections.</p>"));
      } else {
        const items = r.critiques.map((c) => {
          const claim = claims[c.claim_id];
          const quote = claim ? '<span class="quote">' + escapeHtml(c.claim_id) + ": “" +
            renderInline(claim.text) + "”</span>" : "";
          return '<li><span class="sev ' + escapeHtml(c.severity) + '">' + escapeHtml(c.severity) +
            "</span>" + renderInline(c.comment) + quote + "</li>";
        }).join("");
        parts.push(agentMessage("skeptic", "Skeptic <span>· " +
          plural(r.critiques.length, "critique") + "</span>", "<ul>" + items + "</ul>"));
      }

      (fallbacksByRound[r.round] || []).forEach((f) => {
        parts.push(agentMessage("fallback", escapeHtml(f.agent) + " <span>· fallback</span>",
          "<p>" + renderInline(f.reason) + " → " + renderInline(f.action) + "</p>"));
      });

      if (r.editor_note) {
        parts.push(agentMessage("editor", "Editor <span>· sent back for revision</span>",
          markdownToHtml(r.editor_note)));
      } else if (!last && r.critiques.some((c) => c.severity === "high")) {
        parts.push(agentMessage("editor", "Editor <span>· skipped</span>",
          "<p>High-severity critique: the draft went straight back to the Analyst.</p>"));
      }
    });
    if (data.final) {
      // The changelog is already shown above the conversation, so only the reason goes here.
      const body = data.final.reason ? markdownToHtml(data.final.reason) : "";
      parts.push(agentMessage("editor", "Editor <span>· published the narrative above</span>", body));
    }
    return '<ol class="convo">' + parts.join("") + "</ol>";
  }

  function showDebate(data) {
    monthBadge.textContent = data.month;
    timestampEl.textContent = formatTimestamp(data.generated_at);
    narrativeEl.innerHTML = markdownToHtml((data.final && data.final.narrative) || "");
    const critiques = data.rounds.reduce((n, r) => n + r.critiques.length, 0);
    const spend = data.spend || {};
    const stats = [
      data.rounds_used + (data.rounds_used === 1 ? " round" : " rounds"),
      critiques + (critiques === 1 ? " critique" : " critiques"),
    ];
    if (typeof spend.cost_usd === "number") stats.push("~$" + spend.cost_usd.toFixed(3));
    if (typeof spend.elapsed_s === "number") stats.push(Math.round(spend.elapsed_s) + "s");
    let html = '<div class="debate-stats">' +
      stats.map((s) => "<span>" + escapeHtml(s) + "</span>").join("") + "</div>";
    if (data.final && data.final.changelog) {
      html += "<details><summary>What the debate changed</summary>" +
        '<div class="changelog">' + markdownToHtml(data.final.changelog) + "</div></details>";
    }
    html += "<details open><summary>The debate, round by round</summary>" +
      debateConversationHtml(data) + "</details>";
    debateMetaEl.innerHTML = html;
    debateMetaEl.hidden = false;
    playFadeIn();
    if (data.degraded) {
      const agents = data.fallbacks.map((f) => f.agent).join(", ");
      showMessage("This cycle ran degraded (" + agents + " didn't finish) — see the changelog.",
        { warning: true });
    } else {
      clearMessage();
    }
  }

  function showResult(data) {
    if (mode === "debate") showDebate(data);
    else showNarrative(data);
  }

  function showEmptyState() {
    monthBadge.textContent = "";
    timestampEl.textContent = "";
    debateMetaEl.hidden = true;
    narrativeEl.innerHTML = '<div class="empty-state">' + MODES[mode].empty + "</div>";
    playFadeIn();
  }

  function setProgress(pct) {
    progressFill.style.width = pct + "%";
    progressTrack.setAttribute("aria-valuenow", String(Math.round(pct)));
  }

  // There are no real progress events, so the bar is time-based: it eases towards 95%
  // around the mode's typical duration and only reaches 100% when the answer arrives.
  function startProgress() {
    const config = MODES[mode];
    const started = Date.now();
    progressEl.hidden = false;
    const tick = () => {
      const elapsed = (Date.now() - started) / 1000;
      setProgress(Math.max(2, 95 * (1 - Math.exp(-2 * elapsed / config.expectedS))));
      const overtime = elapsed > config.expectedS ? " — taking longer than usual" : "";
      progressLabel.textContent = config.working + "… " + Math.round(elapsed) + "s (usually ~" +
        config.expectedS + "s)" + overtime;
    };
    tick();
    progressTimer = setInterval(tick, 500);
  }

  function stopProgress(succeeded) {
    clearInterval(progressTimer);
    progressTimer = null;
    if (!succeeded) {
      progressEl.hidden = true;
      return;
    }
    setProgress(100);
    progressLabel.textContent = "Done";
    setTimeout(() => {
      if (!busy) progressEl.hidden = true;
    }, 600);
  }

  function setMode(next) {
    mode = next;
    modeButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    modeHint.textContent = MODES[mode].hint;
    refreshLabel.textContent = MODES[mode].buttonLabel;
    try {
      localStorage.setItem("wgo-mode", mode);
    } catch {}
    clearMessage();
    debateMetaEl.hidden = true;
    narrativeEl.textContent = "Loading…";
    loadState();
  }

  modeButtons.forEach((b) => b.addEventListener("click", () => {
    if (!busy && b.dataset.mode !== mode) setMode(b.dataset.mode);
  }));

  function showMessage(text, options) {
    options = options || {};
    messageEl.textContent = text;
    messageEl.classList.toggle("warning", !!options.warning);
    messageEl.classList.add("visible");
  }

  function clearMessage() {
    messageEl.textContent = "";
    messageEl.classList.remove("visible", "warning");
  }

  async function loadState() {
    const requested = mode;
    try {
      const res = await fetch(MODES[requested].stateUrl);
      const data = res.status === 404 ? null : await res.json();
      if (requested !== mode) return;  // switched mode while this was loading
      if (data === null) {
        showEmptyState();
        return;
      }
      if (!res.ok) throw new Error("failed to load state");
      showResult(data);
    } catch (err) {
      if (requested !== mode) return;
      showEmptyState();
      showMessage("Couldn't load the latest narrative: " + err);
    }
  }

  function setBusy(value) {
    busy = value;
    refreshBtn.disabled = value;
    refreshBtn.classList.toggle("loading", value);
    modeButtons.forEach((b) => { b.disabled = value; });
    monthInput.disabled = value;
    refreshLabel.textContent = value ? MODES[mode].busyLabel : MODES[mode].buttonLabel;
  }

  refreshBtn.addEventListener("click", async () => {
    setBusy(true);
    clearMessage();
    startProgress();
    let succeeded = false;
    try {
      const url = MODES[mode].runUrl + "?month=" + encodeURIComponent(monthInput.value);
      const res = await fetch(url, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        // 422s carry a list of validation errors, not a sentence.
        const detail = typeof data.detail === "string" ? data.detail : "check the month (YYYY-MM)";
        showMessage("Run failed: " + detail);
      } else {
        succeeded = true;
        showResult(data);
        if (data.warning) {
          window.alert(data.warning);
        }
      }
    } catch (err) {
      showMessage("Run failed: " + err);
    } finally {
      setBusy(false);
      stopProgress(succeeded);
    }
  });

  let savedMode = "single";
  try {
    if (localStorage.getItem("wgo-mode") === "debate") savedMode = "debate";
  } catch {}
  setMode(savedMode);
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


@app.post("/debate")
def debate(
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    max_rounds: Annotated[int, Query(ge=1, le=5)] = DEFAULT_MAX_ROUNDS,
    context: bool = True,
) -> dict[str, Any]:
    """Run one Phase 2 debate cycle (Analyst -> Skeptic -> Editor, Context in parallel with
    the first draft) and return its full transcript: every draft, critique and editor note,
    the published narrative + changelog, fallbacks, and per-agent tokens/cost/latency. The
    transcript is also saved, so GET /debate/{month} can serve it later. Synchronous, like
    /refresh - a cycle takes tens of seconds to a few minutes. Doesn't touch the Phase 1
    narrative that /state serves."""
    month = month or date.today().strftime("%Y-%m")
    try:
        state = run_debate(month=month, max_rounds=max_rounds, with_context=context)
    except (RuntimeError, *AGENT_FAILURES) as exc:
        # Setup problems (no API key, no data) or the Analyst failing before a first draft -
        # every later failure degrades inside the cycle instead of reaching here.
        logger.warning("debate failed", extra={"month": month, "error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    transcript = transcript_to_dict(state)
    save_transcript(transcript)
    return transcript


@app.get("/debate")
def latest_debate() -> dict[str, Any]:
    """Transcript of the most recent month's debate."""
    transcript = load_latest_transcript()
    if transcript is None:
        raise HTTPException(status_code=404, detail="No debate has been run yet")
    return transcript


@app.get("/debate/{month}")
def debate_for_month(month: Annotated[str, PathParam(pattern=MONTH_PATTERN)]) -> dict[str, Any]:
    """Transcript of the last debate run for `month` (YYYY-MM)."""
    transcript = load_transcript(month)
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"No debate has been run for {month}")
    return transcript


def main() -> int:
    uvicorn.run("whatsgoingon.api:app", host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

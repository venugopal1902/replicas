#!/usr/bin/env python3
"""
Static site generator for the Replicas catalog.

Reads replicas.json and emits a complete static site into docs/.
No dependencies outside the standard library.

    python site/build.py
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
MANIFEST = ROOT / "replicas.json"


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def code_block(code: str, lang: str = "bash", title: str = "") -> str:
    label = f'<div class="code-title">{esc(title)}</div>' if title else ""
    return (
        f'<div class="codeblock">{label}'
        f'<button class="copy" data-copy aria-label="Copy code">Copy</button>'
        f'<pre><code class="lang-{esc(lang)}">{esc(code)}</code></pre>'
        f"</div>"
    )


STYLE = r"""
:root {
  --bg: #08090c;
  --bg-soft: #0e1015;
  --panel: #12151c;
  --panel-2: #171b24;
  --border: #232935;
  --border-soft: #1b2029;
  --text: #e8ecf4;
  --muted: #8b93a7;
  --dim: #656d80;
  --accent: #6ee7b7;
  --accent-2: #7c9cff;
  --warn: #fbbf24;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --radius: 14px;
  --max: 1120px;
}

* { box-sizing: border-box; }

html { scroll-behavior: smooth; scroll-padding-top: 90px; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 16px;
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(900px 520px at 12% -8%, rgba(110, 231, 183, 0.10), transparent 62%),
    radial-gradient(760px 460px at 92% 2%, rgba(124, 156, 255, 0.10), transparent 60%);
  z-index: 0;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.wrap { max-width: var(--max); margin: 0 auto; padding: 0 24px; position: relative; z-index: 1; }

/* ---------- nav ---------- */
.nav {
  position: sticky; top: 0; z-index: 50;
  background: rgba(8, 9, 12, 0.82);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border-soft);
}
.nav-inner {
  max-width: var(--max); margin: 0 auto; padding: 0 24px;
  height: 62px; display: flex; align-items: center; gap: 18px;
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 650; letter-spacing: -0.01em; color: var(--text); }
.brand:hover { text-decoration: none; }
.logo {
  width: 28px; height: 28px; border-radius: 8px;
  background: linear-gradient(140deg, var(--accent), var(--accent-2));
  display: grid; place-items: center; color: #06110d; font-weight: 800; font-size: 14px;
}
.nav-links { margin-left: auto; display: flex; align-items: center; gap: 20px; font-size: 14px; }
.nav-links a { color: var(--muted); }
.nav-links a:hover { color: var(--text); text-decoration: none; }
.gh-btn {
  border: 1px solid var(--border); border-radius: 9px; padding: 7px 13px;
  color: var(--text) !important; font-size: 13.5px; background: var(--panel);
}
.gh-btn:hover { border-color: #35405280; background: var(--panel-2); }

/* ---------- hero ---------- */
.hero { padding: 84px 0 44px; }
.eyebrow {
  display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid var(--border); background: var(--panel);
  border-radius: 999px; padding: 5px 13px; font-size: 12.5px;
  color: var(--muted); letter-spacing: 0.02em; margin-bottom: 26px;
}
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); }
h1 {
  font-size: clamp(38px, 6vw, 60px); line-height: 1.05; letter-spacing: -0.035em;
  margin: 0 0 20px; font-weight: 680;
}
h1 .grad {
  background: linear-gradient(100deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.lede { font-size: 18.5px; color: var(--muted); max-width: 680px; margin: 0 0 32px; }
.cta { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 56px; }
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 11px 20px; border-radius: 10px; font-size: 14.5px; font-weight: 560;
  border: 1px solid transparent; cursor: pointer;
}
.btn-primary { background: linear-gradient(135deg, var(--accent), #4fd1a5); color: #05120c; }
.btn-primary:hover { text-decoration: none; filter: brightness(1.06); }
.btn-ghost { background: var(--panel); border-color: var(--border); color: var(--text); }
.btn-ghost:hover { text-decoration: none; background: var(--panel-2); }

/* ---------- stats ---------- */
.stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: var(--border-soft); border: 1px solid var(--border-soft);
  border-radius: var(--radius); overflow: hidden; margin-bottom: 72px;
}
.stat { background: var(--bg-soft); padding: 22px 20px; }
.stat b { display: block; font-size: 26px; letter-spacing: -0.02em; margin-bottom: 3px; }
.stat span { font-size: 12.5px; color: var(--dim); text-transform: uppercase; letter-spacing: 0.07em; }

/* ---------- sections ---------- */
section { margin-bottom: 76px; }
.sec-head { margin-bottom: 26px; }
.sec-head h2 { font-size: 26px; letter-spacing: -0.02em; margin: 0 0 6px; font-weight: 650; }
.sec-head p { margin: 0; color: var(--muted); font-size: 15.5px; }

/* ---------- service cards ---------- */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; }
.card {
  display: block; background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px; color: var(--text);
  transition: border-color 0.18s, transform 0.18s, background 0.18s;
}
.card:hover { text-decoration: none; border-color: #37414f; transform: translateY(-2px); background: var(--panel-2); }
.card-top { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.card h3 { margin: 0; font-size: 19px; letter-spacing: -0.015em; }
.badge {
  display: inline-block; font-size: 10.5px; font-weight: 650; text-transform: uppercase;
  letter-spacing: 0.08em; padding: 3px 8px; border-radius: 5px;
  background: rgba(110, 231, 183, 0.12); color: var(--accent); border: 1px solid rgba(110, 231, 183, 0.25);
}
.badge.warn { background: rgba(251, 191, 36, 0.1); color: var(--warn); border-color: rgba(251, 191, 36, 0.25); }
.card .tagline { color: var(--muted); font-size: 14.5px; margin: 0 0 16px; }
.card .meta { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  font-size: 11.5px; font-family: var(--mono); color: var(--dim);
  background: var(--bg-soft); border: 1px solid var(--border-soft);
  padding: 2px 8px; border-radius: 5px;
}

/* ---------- feature rows ---------- */
.feature-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.feature { background: var(--bg-soft); border: 1px solid var(--border-soft); border-radius: 12px; padding: 20px; }
.feature h4 { margin: 0 0 8px; font-size: 15px; }
.feature p { margin: 0; font-size: 14px; color: var(--muted); }

/* ---------- docs layout ---------- */
.doc { display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 52px; padding: 44px 0 88px; }
.side { position: sticky; top: 86px; align-self: start; font-size: 13.5px; }
.side h5 {
  margin: 0 0 10px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.09em; color: var(--dim);
}
.side ul { list-style: none; margin: 0 0 26px; padding: 0; }
.side li { margin-bottom: 3px; }
.side a { display: block; padding: 5px 10px; border-radius: 7px; color: var(--muted); border-left: 2px solid transparent; }
.side a:hover { color: var(--text); background: var(--panel); text-decoration: none; border-left-color: var(--accent); }

.content { min-width: 0; }
.content h1 { font-size: 38px; margin-bottom: 14px; }
.content h2 {
  font-size: 24px; letter-spacing: -0.02em; margin: 52px 0 14px; padding-top: 14px;
  border-top: 1px solid var(--border-soft); font-weight: 650;
}
.content h2:first-of-type { margin-top: 34px; }
.content h3 { font-size: 17px; margin: 28px 0 10px; }
.content p { color: #b9c0cf; }
.content ul, .content ol { color: #b9c0cf; padding-left: 22px; }
.content li { margin-bottom: 6px; }

.callout {
  border: 1px solid var(--border); border-left: 3px solid var(--accent);
  background: var(--panel); border-radius: 0 10px 10px 0;
  padding: 14px 18px; margin: 20px 0; font-size: 14.5px; color: #b9c0cf;
}
.callout.warn { border-left-color: var(--warn); }
.callout b { color: var(--text); }

/* ---------- code ---------- */
.codeblock { position: relative; margin: 14px 0 22px; border: 1px solid var(--border); border-radius: 11px; overflow: hidden; background: #0b0d12; }
.code-title {
  padding: 9px 16px; font-size: 12px; color: var(--dim); font-family: var(--mono);
  background: var(--panel); border-bottom: 1px solid var(--border-soft);
}
.codeblock pre { margin: 0; padding: 16px 18px; overflow-x: auto; }
.codeblock code { font-family: var(--mono); font-size: 13px; line-height: 1.72; color: #d3dae8; white-space: pre; }
.copy {
  position: absolute; top: 8px; right: 8px; z-index: 2;
  font-size: 11.5px; padding: 4px 9px; border-radius: 6px; cursor: pointer;
  background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
  font-family: var(--sans); opacity: 0; transition: opacity 0.15s;
}
.codeblock:hover .copy, .copy:focus { opacity: 1; }
.copy:hover { color: var(--text); border-color: #3a4452; }
.copy.done { color: var(--accent); border-color: rgba(110, 231, 183, 0.4); opacity: 1; }

/* ---------- tables ---------- */
.table-wrap { border: 1px solid var(--border); border-radius: 11px; overflow: hidden; margin: 18px 0 26px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th {
  text-align: left; padding: 11px 16px; background: var(--panel); color: var(--dim);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
  border-bottom: 1px solid var(--border);
}
td { padding: 11px 16px; border-bottom: 1px solid var(--border-soft); color: #b9c0cf; vertical-align: top; }
tr:last-child td { border-bottom: none; }
td code, p code, li code {
  font-family: var(--mono); font-size: 12.5px; background: var(--panel-2);
  border: 1px solid var(--border-soft); padding: 1px 6px; border-radius: 5px; color: var(--accent);
}
.method { font-family: var(--mono); font-size: 11.5px; font-weight: 700; letter-spacing: 0.04em; }
.method.get { color: var(--accent-2); }
.method.post { color: var(--accent); }
.method.del { color: #f87171; }

/* ---------- pricing ---------- */
.price-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; margin: 20px 0 8px; }
.price { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
.price h4 { margin: 0 0 4px; font-size: 14px; color: var(--muted); font-weight: 550; }
.price b { font-size: 27px; letter-spacing: -0.02em; display: block; margin-bottom: 10px; }
.price p { margin: 0; font-size: 13.5px; color: var(--dim); }

/* ---------- faq ---------- */
details {
  border: 1px solid var(--border); border-radius: 10px; padding: 0; margin-bottom: 10px;
  background: var(--bg-soft); overflow: hidden;
}
summary {
  padding: 14px 18px; cursor: pointer; font-size: 15px; font-weight: 550;
  list-style: none; position: relative; padding-right: 42px;
}
summary::-webkit-details-marker { display: none; }
summary::after {
  content: "+"; position: absolute; right: 18px; top: 12px;
  color: var(--dim); font-size: 18px; font-weight: 400;
}
details[open] summary::after { content: "\2212"; }
details p { margin: 0; padding: 0 18px 16px; color: var(--muted); font-size: 14.5px; }

/* ---------- footer ---------- */
footer {
  border-top: 1px solid var(--border-soft); padding: 34px 0 54px;
  color: var(--dim); font-size: 13.5px;
}
.foot-inner { max-width: var(--max); margin: 0 auto; padding: 0 24px; display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }
.foot-inner a { color: var(--muted); }
.foot-inner .sp { margin-left: auto; }

/* ---------- misc ---------- */
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin: 22px 0 8px; }
.shot { margin: 0; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--bg-soft); }
.shot img { display: block; width: 100%; height: auto; }
.shot figcaption { padding: 9px 14px; font-size: 12.5px; color: var(--dim); border-top: 1px solid var(--border-soft); }
.pill-row { display: flex; flex-wrap: wrap; gap: 7px; margin: 16px 0 4px; }
.anchor-link { color: var(--dim); font-size: 16px; margin-left: 8px; opacity: 0; }
h2:hover .anchor-link { opacity: 1; }
.doc-meta { display: flex; flex-wrap: wrap; gap: 20px; font-size: 13.5px; color: var(--dim); margin-bottom: 26px; padding-bottom: 22px; border-bottom: 1px solid var(--border-soft); }
.doc-meta b { color: var(--text); font-weight: 550; }
.back { font-size: 13.5px; color: var(--dim); display: inline-block; margin-bottom: 18px; }

@media (max-width: 900px) {
  .doc { grid-template-columns: 1fr; gap: 0; }
  .side { position: static; display: none; }
  .stats { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 620px) {
  .nav-links a:not(.gh-btn) { display: none; }
  .hero { padding: 52px 0 30px; }
  .stat b { font-size: 22px; }
}
"""

SCRIPT = r"""
(function () {
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var block = btn.closest('.codeblock');
      var code = block ? block.querySelector('code') : null;
      if (!code) return;
      var text = code.innerText;
      var done = function () {
        btn.textContent = 'Copied';
        btn.classList.add('done');
        setTimeout(function () { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  });

  function fallback(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  // Scroll-spy for the docs sidebar
  var links = Array.prototype.slice.call(document.querySelectorAll('.side a[href^="#"]'));
  if (!links.length) return;
  var targets = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); });
  function onScroll() {
    var pos = window.scrollY + 110, active = 0;
    for (var i = 0; i < targets.length; i++) {
      if (targets[i] && targets[i].offsetTop <= pos) active = i;
    }
    links.forEach(function (a, i) {
      a.style.color = i === active ? 'var(--text)' : '';
      a.style.borderLeftColor = i === active ? 'var(--accent)' : 'transparent';
      a.style.background = i === active ? 'var(--panel)' : '';
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
"""


def page_shell(site: dict, title: str, body: str, depth: int = 0) -> str:
    prefix = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(site['description'])}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(site['description'])}">
<meta property="og:type" content="website">
<link rel="stylesheet" href="{prefix}assets/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%236ee7b7'/><text x='16' y='23' font-size='19' font-family='monospace' font-weight='bold' text-anchor='middle' fill='%23061010'>R</text></svg>">
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a class="brand" href="{prefix}index.html">
      <span class="logo">R</span>
      <span>{esc(site['title'])}</span>
    </a>
    <div class="nav-links">
      <a href="{prefix}index.html#services">Services</a>
      <a href="{prefix}index.html#how">How it works</a>
      <a href="{prefix}index.html#stack">Tech</a>
      <a class="gh-btn" href="{esc(site['repo_url'])}">GitHub</a>
    </div>
  </div>
</nav>
{body}
<footer>
  <div class="foot-inner">
    <span>&copy; {esc(site['author'])}</span>
    <span>Replicas &middot; self-hosted replacements</span>
    <span class="sp"><a href="{esc(site['repo_url'])}">Source on GitHub</a></span>
  </div>
</footer>
<script src="{prefix}assets/app.js" defer></script>
</body>
</html>"""


def render_index(site: dict, services: list[dict]) -> str:
    cards = []
    for s in services:
        badges = "".join(f'<span class="chip">{esc(x)}</span>' for x in s["stack"][:4])
        cards.append(
            f"""<a class="card" href="services/{esc(s['slug'])}.html">
  <div class="card-top">
    <h3>{esc(s['name'])}</h3>
    <span class="badge">{esc(s['status'])}</span>
  </div>
  <p class="tagline">{esc(s['tagline'])}</p>
  <div class="meta">
    <span class="chip">replaces {esc(s['replaces'])}</span>
    <span class="chip">{esc(s['language'])}</span>
    {badges}
  </div>
</a>"""
        )

    platform_count = max((len(s["platforms"]) for s in services), default=0)
    endpoint_count = sum(len(s["endpoints"]) for s in services)
    tool_count = sum(len(s["mcp_tools"]) for s in services)

    body = f"""
<main class="wrap">
  <section class="hero">
    <span class="eyebrow"><span class="dot"></span> Clean-room replicas &middot; MIT licensed</span>
    <h1>Own the APIs you<br><span class="grad">already pay for.</span></h1>
    <p class="lede">{esc(site['description'])}</p>
    <div class="cta">
      <a class="btn btn-primary" href="services/{esc(services[0]['slug'])}.html">Read the docs &rarr;</a>
      <a class="btn btn-ghost" href="{esc(site['repo_url'])}">View source</a>
    </div>

    <div class="stats">
      <div class="stat"><b>{len(services)}</b><span>Services</span></div>
      <div class="stat"><b>{endpoint_count}</b><span>Endpoints</span></div>
      <div class="stat"><b>{tool_count}</b><span>MCP tools</span></div>
      <div class="stat"><b>{platform_count}</b><span>Platforms</span></div>
    </div>
  </section>

  <section id="services">
    <div class="sec-head">
      <h2>Services</h2>
      <p>Every replica ships with full source, an install path that works from a clean machine, and a documented API surface.</p>
    </div>
    <div class="grid">{''.join(cards)}</div>
  </section>

  <section id="how">
    <div class="sec-head">
      <h2>How a replica gets built</h2>
      <p>Behaviour is reverse engineered, code is written fresh. No proprietary source is copied.</p>
    </div>
    <div class="feature-grid">
      <div class="feature"><h4>1 &middot; Map the surface</h4><p>Read the public API, OpenAPI schema and agent docs. Capture real request and response pairs for every operation that matters.</p></div>
      <div class="feature"><h4>2 &middot; Trace the behaviour</h4><p>Use REA and browser instrumentation to observe the live app: endpoints, state transitions, async job shapes, error semantics.</p></div>
      <div class="feature"><h4>3 &middot; Write it clean</h4><p>Implement the same contracts in our own code. Compatibility is behavioural, not copy-pasted.</p></div>
      <div class="feature"><h4>4 &middot; Prove it works</h4><p>End-to-end tests hit the real ASGI app: publish, poll status, verify credits, list MCP tools. Green suite or it does not ship.</p></div>
    </div>
  </section>

  <section id="stack">
    <div class="sec-head">
      <h2>Stack</h2>
      <p>Boring, portable choices. Anything that runs in a container runs these replicas.</p>
    </div>
    <div class="pill-row">
      <span class="chip">Python 3.12</span>
      <span class="chip">FastAPI</span>
      <span class="chip">SQLAlchemy</span>
      <span class="chip">APScheduler</span>
      <span class="chip">SQLite / Postgres</span>
      <span class="chip">JSON-RPC MCP</span>
      <span class="chip">uv</span>
      <span class="chip">Docker-ready</span>
    </div>
  </section>
</main>
"""
    return page_shell(site, f"{site['title']} · {site['tagline']}", body, depth=0)


def render_service(site: dict, s: dict) -> str:
    sections = [
        ("overview", "Overview"),
        ("ui", "Interface"),
        ("install", "Install"),
        ("usage", "Usage"),
        ("api", "API reference"),
        ("mcp", "MCP tools"),
        ("monetize", "Monetize"),
        ("notes", "Operational notes"),
        ("faq", "FAQ"),
    ]
    side = "".join(f'<li><a href="#{sid}">{esc(label)}</a></li>' for sid, label in sections)
    side += f'<h5>Source</h5><ul><li><a href="{esc(site["repo_url"])}/tree/main/{esc(s["repo_path"])}">Repository</a></li></ul>'

    parts: list[str] = []

    # Overview
    parts.append(f'<h2 id="overview">Overview<a class="anchor-link" href="#overview">#</a></h2>')
    parts.append(f"<p>{esc(s['summary'])}</p>")
    parts.append('<div class="pill-row">')
    parts.append(f'<span class="chip">replaces {esc(s["replaces"])}</span>')
    parts.append(f'<span class="chip">v{esc(s["version"])}</span>')
    parts.append(f'<span class="chip">{esc(s["license"])}</span>')
    parts.append(f'<span class="chip">{esc(s["language"])}</span>')
    parts.append("</div>")
    if s.get("highlights"):
        parts.append("<h3>What you get</h3><ul>")
        parts.extend(f"<li>{esc(h)}</li>" for h in s["highlights"])
        parts.append("</ul>")
    if s.get("features"):
        parts.append('<div class="feature-grid">')
        for f in s["features"]:
            parts.append(
                f'<div class="feature"><h4>{esc(f["title"])}</h4><p>{esc(f["body"])}</p></div>'
            )
        parts.append("</div>")

    # Interface + Install
    if s.get("ui"):
        parts.append('<h2 id="ui">Interface<a class="anchor-link" href="#ui">#</a></h2>')
        parts.append(f"<p>{esc(s['ui'].get('intro', 'Runs in the browser at the same origin as the API.'))}</p>")
        parts.append('<div class="shots">')
        for img in s["ui"].get("images", []):
            parts.append(
                f'<figure class="shot"><img src="../{esc(img["src"])}" alt="{esc(img.get("caption", ""))}" loading="lazy">'
                f'<figcaption>{esc(img.get("caption", ""))}</figcaption></figure>'
            )
        parts.append("</div>")

    parts.append('<h2 id="install">Install<a class="anchor-link" href="#install">#</a></h2>')
    parts.append("<p>From a clean machine to a running server in four commands.</p>")
    parts.append("<h3>Requirements</h3><ul>")
    parts.extend(f"<li>{esc(p)}</li>" for p in s["prereqs"])
    parts.append("</ul>")
    for i, step in enumerate(s["install"], start=1):
        parts.append(f'<h3>Step {i} &middot; {esc(step["title"])}</h3>')
        parts.append(f'<p>{esc(step["desc"])}</p>')
        parts.append(code_block(step["code"], step.get("lang", "bash")))
    if s.get("verify"):
        parts.append(f'<h3>{esc(s["verify"]["title"])}</h3>')
        parts.append(code_block(s["verify"]["code"], "bash"))
    if s.get("urls"):
        parts.append('<div class="table-wrap"><table><thead><tr><th>Surface</th><th>URL</th></tr></thead><tbody>')
        for u in s["urls"]:
            parts.append(f'<tr><td>{esc(u["label"])}</td><td><code>{esc(u["value"])}</code></td></tr>')
        parts.append("</tbody></table></div>")

    # Usage
    parts.append('<h2 id="usage">Usage<a class="anchor-link" href="#usage">#</a></h2>')
    for u in s["usage"]:
        parts.append(f'<h3>{esc(u["title"])}</h3>')
        parts.append(f'<p>{esc(u["desc"])}</p>')
        parts.append(code_block(u["code"], u.get("lang", "bash")))

    # API
    parts.append('<h2 id="api">API reference<a class="anchor-link" href="#api">#</a></h2>')
    parts.append('<div class="table-wrap"><table><thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead><tbody>')
    for e in s["endpoints"]:
        cls = "get" if e["method"] == "GET" else ("del" if e["method"] == "DELETE" else "post")
        parts.append(
            f'<tr><td><span class="method {cls}">{esc(e["method"])}</span></td>'
            f'<td><code>{esc(e["path"])}</code></td><td>{esc(e["desc"])}</td></tr>'
        )
    parts.append("</tbody></table></div>")

    # MCP
    if s.get("mcp_tools"):
        parts.append('<h2 id="mcp">MCP tools<a class="anchor-link" href="#mcp">#</a></h2>')
        parts.append("<p>Any MCP-capable agent that connects to the endpoint gets these tools with no extra wiring.</p>")
        parts.append('<div class="table-wrap"><table><thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>')
        for t in s["mcp_tools"]:
            parts.append(f'<tr><td><code>{esc(t["name"])}</code></td><td>{esc(t["desc"])}</td></tr>')
        parts.append("</tbody></table></div>")

    # Monetize
    if s.get("monetization"):
        parts.append('<h2 id="monetize">Monetize<a class="anchor-link" href="#monetize">#</a></h2>')
        parts.append("<p>The billing layer is built in. Keys carry a plan, a credit balance and an hourly ceiling, so you can resell access without writing auth code.</p>")
        parts.append('<div class="price-grid">')
        for p in s["monetization"]:
            parts.append(
                f'<div class="price"><h4>{esc(p["tier"])}</h4><b>{esc(p["price"])}</b><p>{esc(p["detail"])}</p></div>'
            )
        parts.append("</div>")
        parts.append(
            '<div class="callout"><b>Ship it publicly:</b> put the service behind Caddy or Nginx with TLS, '
            "point a subdomain at it, then hand clients "
            '<code>https://api.yourdomain.com/v2</code> and their own key.</div>'
        )

    # Notes
    if s.get("notes"):
        parts.append('<h2 id="notes">Operational notes<a class="anchor-link" href="#notes">#</a></h2>')
        parts.append('<div class="callout warn"><b>Read before going live.</b> These are the things that break in production.</div>')
        parts.append("<ul>")
        parts.extend(f"<li>{esc(n)}</li>" for n in s["notes"])
        parts.append("</ul>")

    # FAQ
    if s.get("faq"):
        parts.append('<h2 id="faq">FAQ<a class="anchor-link" href="#faq">#</a></h2>')
        for q in s["faq"]:
            parts.append(f"<details><summary>{esc(q['q'])}</summary><p>{esc(q['a'])}</p></details>")

    body = f"""
<main class="wrap">
  <div class="doc">
    <aside class="side">
      <h5>On this page</h5>
      <ul>{side}</ul>
    </aside>
    <article class="content">
      <a class="back" href="../index.html">&larr; All services</a>
      <h1>{esc(s['name'])}</h1>
      <p class="lede" style="font-size:17px">{esc(s['tagline'])}</p>
      <div class="doc-meta">
        <span>Replaces <b>{esc(s['replaces'])}</b></span>
        <span>Version <b>{esc(s['version'])}</b></span>
        <span>License <b>{esc(s['license'])}</b></span>
        <span>Runtime <b>{esc(s['language'])}</b></span>
      </div>
      {''.join(parts)}
    </article>
  </div>
</main>
"""
    return page_shell(
        site,
        f"{s['name']} · {site['title']}",
        body,
        depth=1,
    )


def main() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    site = data["site"]
    services = data["services"]

    if DOCS.exists():
        shutil.rmtree(DOCS)
    ASSETS.mkdir(parents=True, exist_ok=True)
    (DOCS / "services").mkdir(parents=True, exist_ok=True)

    (ASSETS / "style.css").write_text(STYLE, encoding="utf-8")
    (ASSETS / "app.js").write_text(SCRIPT, encoding="utf-8")

    # Copy static assets that live outside the generated HTML (screenshots, images)
    src_assets = ROOT / "site" / "assets"
    if src_assets.exists():
        shutil.copytree(src_assets, ASSETS, dirs_exist_ok=True)

    (DOCS / "index.html").write_text(render_index(site, services), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    for s in services:
        out = DOCS / "services" / f"{s['slug']}.html"
        out.write_text(render_service(site, s), encoding="utf-8")
        print(f"  rendered services/{s['slug']}.html")

    print(f"Built {len(services)} service page(s) into {DOCS}")


if __name__ == "__main__":
    main()

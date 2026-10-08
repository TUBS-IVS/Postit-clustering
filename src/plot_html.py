"""Turn a cluster table into an interactive 3-D page (clusters_<name>.html): the plot, a clickable
cluster list with theme words, a note reader with source details and a word search.
plotly.js loads from its CDN, so the page is small (needs internet to open).

Called by src/embed_cluster.py. You can also run it alone to redraw pages from the saved CSVs
without embedding again:
    python src/plot_html.py                      # every data/processed/clusters_*.csv
    python src/plot_html.py data/processed/clusters_all-mpnet-base-v2.csv
"""

import json
import os
import sys
import textwrap
from html import escape
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "postits_geprueft (2).json"
SCAN_DIR = ROOT / "WorkshopPostItScan"
PALETTE = px.colors.qualitative.Bold + px.colors.qualitative.Vivid + px.colors.qualitative.Prism
NOISE_COLOR = "#8a93a6"
BG = "#0f1222"


def wrap(text, width=52):
    return "<br>".join("<br>".join(textwrap.wrap(escape(line), width)) for line in str(text).splitlines() if line.strip())


def source(r):
    """Where an idea comes from: its line in the cluster table plus the checked scan record (data/raw)."""
    row, n_ideas = int(r["row"]), int(r["n_ideas"])
    raw = SOURCE_RAW[row]
    flags = (["checked"] if raw.get("geprueft") else ["not checked"]) + (["edited by hand"] if raw.get("manuell_geaendert") else [])
    scan = SCAN_DIR / raw["bild"]
    return {"row": row, "id": str(r["id"]), "text": r["text_clean"], "note": r["note"],
            "idea": f"idea {r['idea']} of {n_ideas}" if n_ideas > 1 else "",
            "original": raw["text"], "english": raw["text_englisch"],
            "where": raw["adresse"], "seite": raw["seite"], "zeile": raw["zeile"], "spalte": raw["spalte"],
            "teil": raw["teilposition"], "scan": Path(raw["bild"]).name, "flags": ", ".join(flags),
            "hint": raw.get("hinweis") or "",
            "scan_url": quote(os.path.relpath(scan, OUT_DIR).replace(os.sep, "/")) if scan.exists() else ""}


def hover(n):
    dim = "<span style='color:#9aa3b8'>{}</span>"
    lines = [f"<b>{wrap(n['text'])}</b>"]
    if n["idea"]:
        lines.append(dim.format(f"{n['idea']} on this post-it:") + f" {wrap(n['note'])}")
    if n["original"].strip() != n["english"].strip():
        lines.append(dim.format("original:") + f" <i>{wrap(n['original'])}</i>")
    if " ".join(n["english"].split()) != n["note"]:
        lines.append(dim.format("before cleaning:") + f" {wrap(n['english'])}")
    lines.append(dim.format(f"post-it {n['where']} · side {n['seite']} · row {n['zeile']} · column {n['spalte']}"
                            f" · part {n['teil']}"))
    lines.append(dim.format(f"scan {n['scan']} · note #{n['id']} · {n['flags']}"))
    return "<br>".join(lines)


SOURCE_RAW = json.load(open(RAW, encoding="utf-8-sig"))


def build(df, title):
    """The figure (one trace per cluster, noise last, then the label trace) and the data the page script needs."""
    ids = sorted(c for c in df["cluster"].unique() if c != -1) + ([-1] if (df["cluster"] == -1).any() else [])
    fig, clusters = go.Figure(), []
    for i, c in enumerate(ids):
        part = df[df["cluster"] == c]
        noise = bool(c == -1)
        color = NOISE_COLOR if noise else PALETTE[i % len(PALETTE)]
        notes = [source(r) for _, r in part.iterrows()]
        fig.add_trace(go.Scatter3d(
            x=part["x"], y=part["y"], z=part["z"], mode="markers",
            marker=dict(size=3 if noise else 5, color=color, opacity=0.35 if noise else 0.9,
                        line=dict(width=0.5, color="rgba(255,255,255,0.5)")),
            customdata=[[hover(n)] for n in notes],
            hovertemplate=f"%{{customdata[0]}}<extra>{'noise' if noise else f'cluster {c}'}</extra>"))
        clusters.append({"id": int(c), "noise": noise, "color": color, "n": len(part),
                         "words": [] if noise else part["theme"].iloc[0].split(", "),
                         "size": 3 if noise else 5, "opacity": 0.35 if noise else 0.9,
                         "notes": notes})
    named = [c for c in clusters if not c["noise"]]
    centers = df[df["cluster"] != -1].groupby("cluster")[["x", "y", "z"]].mean().loc[[c["id"] for c in named]]
    fig.add_trace(go.Scatter3d(
        x=centers["x"], y=centers["y"], z=centers["z"], mode="text", hoverinfo="skip",
        text=[f"<b>{' · '.join(c['words'][:2])}</b>" for c in named],
        textfont=dict(size=13, color=[c["color"] for c in named], family="Inter, Segoe UI, sans-serif")))
    axis = dict(showbackground=False, showgrid=False, zeroline=False, showticklabels=False, title="", showspikes=False)
    fig.update_layout(
        paper_bgcolor=BG, showlegend=False, margin=dict(l=0, r=0, t=0, b=0),
        scene=dict(xaxis=axis, yaxis=axis, zaxis=axis, bgcolor=BG, camera=dict(eye=dict(x=1.4, y=1.4, z=0.9))),
        hoverlabel=dict(bgcolor="#1b2036", bordercolor="#3a4266", font=dict(color="#e8ebf5", size=13,
                                                                            family="Inter, Segoe UI, sans-serif")))
    return fig, clusters


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0f1222; --panel:#161a2e; --card:#1d2240; --line:#2c3358; --text:#e8ebf5; --dim:#9aa3b8; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
               font:14px/1.45 Inter, "Segoe UI", system-ui, sans-serif; }
  .app { display:flex; height:100vh; }
  aside { width:380px; min-width:300px; display:flex; flex-direction:column; background:var(--panel);
          border-right:1px solid var(--line); }
  header { padding:18px 18px 10px; }
  h1 { margin:0 0 4px; font-size:19px; letter-spacing:.2px; }
  .stats { color:var(--dim); font-size:12.5px; }
  .tools { display:flex; gap:8px; padding:0 18px 12px; flex-wrap:wrap; }
  input[type=search] { flex:1 1 100%; padding:9px 12px; border-radius:10px; border:1px solid var(--line);
                       background:var(--card); color:var(--text); font:inherit; outline:none; }
  input[type=search]:focus { border-color:#6c7cff; }
  .toggle { font-size:12.5px; color:var(--dim); display:flex; align-items:center; gap:6px; cursor:pointer;
            padding:4px 10px; border:1px solid var(--line); border-radius:999px; user-select:none; }
  .toggle input { accent-color:#6c7cff; margin:0; }
  .scroll { overflow-y:auto; padding:0 12px 18px; flex:1; }
  .section { color:var(--dim); font-size:11.5px; text-transform:uppercase; letter-spacing:1px; margin:14px 6px 8px; }
  .cluster { display:flex; gap:10px; align-items:flex-start; padding:9px 10px; border-radius:12px; cursor:pointer;
             border:1px solid transparent; transition:background .15s, border-color .15s; }
  .cluster:hover { background:var(--card); }
  .cluster.on { background:var(--card); border-color:var(--c); }
  .cluster.off { opacity:.4; }
  .dot { width:12px; height:12px; border-radius:50%; background:var(--c); margin-top:4px; flex:none;
         box-shadow:0 0 10px var(--c); }
  .chips { display:flex; flex-wrap:wrap; gap:5px; }
  .chip { font-size:12.5px; padding:1px 9px; border-radius:999px; background:color-mix(in srgb, var(--c) 22%, transparent);
          color:var(--text); border:1px solid color-mix(in srgb, var(--c) 45%, transparent); }
  .chip:hover { background:color-mix(in srgb, var(--c) 45%, transparent); }
  .count { margin-left:auto; color:var(--dim); font-size:12px; padding-top:2px; }
  .note { margin:8px 4px; padding:9px 12px; background:var(--card); border-radius:10px; border-left:4px solid var(--c); }
  .note .meta { color:var(--dim); font-size:11.5px; margin-top:4px; }
  .note .orig { color:#c8cde0; font-style:italic; font-size:12.5px; margin-top:5px; white-space:pre-line; }
  .note .hint2 { color:#e9b872; font-size:11.5px; margin-top:4px; }
  .note a { color:#8fa2ff; text-decoration:none; } .note a:hover { text-decoration:underline; }
  .note.picked { outline:2px solid var(--c); }
  mark { background:#ffd54a; color:#111; border-radius:3px; padding:0 2px; }
  main { flex:1; position:relative; min-width:0; }
  #plot { position:absolute; inset:0; }
  .hint { position:absolute; right:16px; bottom:12px; color:var(--dim); font-size:12px; pointer-events:none; }
  .empty { color:var(--dim); margin:8px 6px; }
  @media (max-width:800px) { .app { flex-direction:column-reverse; } aside { width:100%; height:45vh; }
                             main { height:55vh; flex:none; } }
</style></head>
<body><div class="app">
<aside>
  <header><h1>__TITLE__</h1><div class="stats">__STATS__</div></header>
  <div class="tools">
    <input type="search" id="q" placeholder="Search a word or post-it, e.g. bike, Wohnen, MA32…">
    <label class="toggle"><input type="checkbox" id="noise" checked> noise</label>
    <label class="toggle"><input type="checkbox" id="labels" checked> labels</label>
  </div>
  <div class="scroll">
    <div class="section" id="listTitle">Clusters · click to focus</div>
    <div id="list"></div>
    <div class="section" id="notesTitle"></div>
    <div id="notes"></div>
  </div>
</aside>
<main>__PLOT__<div class="hint">drag to rotate · scroll to zoom · click a dot to read it</div></main>
</div>
<script>
const C = __DATA__;
const plot = document.getElementById('plot');
const labelTrace = C.length;
let active = null, query = '', picked = null;
const esc = s => s.replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
const reEsc = s => s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
const name = c => c.noise ? 'noise · fits no cluster' : c.words.join(' · ');

function highlight(text) {
  const safe = esc(text);
  return query ? safe.replace(new RegExp('(' + reEsc(esc(query)) + ')', 'gi'), '<mark>$1</mark>') : safe;
}
function matches(n) { return !query || (n.text + ' ' + n.original + ' ' + n.where).toLowerCase().includes(query); }

function renderList() {
  document.getElementById('list').innerHTML = C.map((c, i) => {
    const hits = c.notes.filter(matches).length;
    const cls = active === i ? 'on' : (active !== null || (query && !hits)) ? 'off' : '';
    const words = c.noise ? '<span class="chip">noise</span>'
                          : c.words.map(w => `<span class="chip" data-w="${esc(w)}">${esc(w)}</span>`).join('');
    return `<div class="cluster ${cls}" data-i="${i}" style="--c:${c.color}"><span class="dot"></span>
            <div class="chips">${words}</div><span class="count">${query ? hits + '/' : ''}${c.n}</span></div>`;
  }).join('');
}

function renderNotes() {
  const title = document.getElementById('notesTitle'), box = document.getElementById('notes');
  let items = [];
  C.forEach((c, i) => { if (active === null || active === i) c.notes.forEach((n, j) => { if (matches(n)) items.push([c, n, i, j]); }); });
  if (active === null && !query) { title.textContent = ''; box.innerHTML = picked ? noteHtml(...picked) : ''; return; }
  title.textContent = (active !== null ? name(C[active]) : 'All clusters') + ' · ' + items.length + ' notes';
  box.innerHTML = items.length ? items.map(x => noteHtml(...x)).join('') : '<div class="empty">No note contains this word.</div>';
}
function noteHtml(c, n, i, j) {
  const on = picked && picked[2] === i && picked[3] === j ? ' picked' : '';
  const orig = n.original.trim() !== n.english.trim() ? `<div class="orig">${esc(n.original)}</div>` : '';
  const scan = n.scan_url ? `<a href="${n.scan_url}" target="_blank">open scan ↗</a>` : esc(n.scan);
  return `<div class="note${on}" style="--c:${c.color}">${highlight(n.text)}${orig}
          <div class="meta">post-it <b>${esc(n.where)}</b> · side ${esc(n.seite)} · row ${esc(n.zeile)} · column ${n.spalte} · part ${n.teil}<br>
          ${scan} · note #${esc(n.id)}${n.idea ? ' (' + n.idea + ')' : ''} · ${esc(n.flags)} · ${c.noise ? 'noise' : 'cluster ' + c.id}</div>
          ${n.hint ? `<div class="hint2">⚠ ${esc(n.hint)}</div>` : ''}</div>`;
}

function restyle() {
  const idx = C.map((_, i) => i);
  Plotly.restyle(plot, {
    'marker.opacity': C.map((c, i) => active === null ? c.opacity : (i === active ? 1 : 0.05)),
    'marker.size': C.map(c => c.notes.map(n => query ? (matches(n) ? 9 : 2) : c.size)),
  }, idx);
}
function update() { renderList(); renderNotes(); restyle(); }

document.getElementById('list').addEventListener('click', e => {
  const chip = e.target.closest('.chip[data-w]');
  if (chip) { const q = document.getElementById('q'); q.value = chip.dataset.w; q.dispatchEvent(new Event('input')); return; }
  const card = e.target.closest('.cluster'); if (!card) return;
  const i = +card.dataset.i; active = active === i ? null : i; picked = null; update();
});
document.getElementById('q').addEventListener('input', e => { query = e.target.value.trim().toLowerCase(); update(); });
document.getElementById('noise').addEventListener('change', e => {
  const k = C.findIndex(c => c.noise); if (k >= 0) Plotly.restyle(plot, {visible: e.target.checked}, [k]);
});
document.getElementById('labels').addEventListener('change', e => Plotly.restyle(plot, {visible: e.target.checked}, [labelTrace]));
window.addEventListener('load', () => plot.on('plotly_click', ev => {
  const p = ev.points[0]; if (p.curveNumber >= C.length) return;
  picked = [C[p.curveNumber], C[p.curveNumber].notes[p.pointNumber], p.curveNumber, p.pointNumber];
  renderNotes();
  const el = document.querySelector('.note.picked'); if (el) el.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}));
renderList();
</script></body></html>
"""


def write_page(df, title, path):
    fig, clusters = build(df, title)
    n_noise = int((df["cluster"] == -1).sum())
    stats = f"{len(df)} notes · {len(clusters) - (1 if n_noise else 0)} clusters · {n_noise} noise"
    plot = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="plot",
                       config={"displaylogo": False, "responsive": True,
                               "modeBarButtonsToRemove": ["toImage", "resetCameraLastSave3d"]})
    data = json.dumps(clusters, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE.replace("__TITLE__", escape(title)).replace("__STATS__", stats).replace("__DATA__", data)
    Path(path).write_text(page.replace("__PLOT__", plot), encoding="utf-8")


def main():
    paths = [Path(p) for p in sys.argv[1:]] or sorted(OUT_DIR.glob("clusters_*.csv"))
    for csv in paths:
        df = pd.read_csv(csv, encoding="utf-8-sig", dtype={"id": str})
        name = csv.stem.removeprefix("clusters_")
        write_page(df, f"Post-it clusters · {name.replace('_', ' · ')}", csv.with_suffix(".html"))
        print(f"{csv.name} -> {csv.stem}.html")


if __name__ == "__main__":
    main()

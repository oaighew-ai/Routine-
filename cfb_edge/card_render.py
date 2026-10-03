"""Render a ``CFB_EDGE_WEEKLY_CARD_V1`` JSON document as the weekly card page.

The page is a view of the card and nothing else: every number on it is read
from the JSON the builder wrote, so the page cannot disagree with the evidence
it cites. Output is an HTML fragment in the shape the claude.ai Artifact
publisher expects (``<title>`` and ``<style>`` first, no document skeleton);
``standalone=True`` wraps it in a full document for local viewing.

Standard library only; deterministic for a given card.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - depends on the host's tz database
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

STATE_CLASS = {"PASS": "ok", "DEGRADED": "warn", "FAIL": "bad", "NOT_APPLICABLE": "na"}
STATE_LABEL = {"PASS": "Pass", "DEGRADED": "Degraded", "FAIL": "Fail", "NOT_APPLICABLE": "N/A"}
GATE_ORDER = ("marketCapture", "execution", "openProvenance", "informationState",
              "qbContinuity", "epa", "weather", "cohortIdentity")
# Codes every row carries while the authority is blocked or the board is old.
# Shown once above the list instead of on each of fifty rows.
GLOBAL_CODES = ("AUTHORITY_BLOCKED", "QUOTE_STALE")
GATE_SHORT = {"marketCapture": "Market", "execution": "Fresh", "openProvenance": "Open",
              "informationState": "Info", "qbContinuity": "QB", "epa": "EPA",
              "weather": "Wx", "cohortIdentity": "Cohort"}


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _instant(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def et(value: Any, fmt: str = "%a %b %-d, %-I:%M %p") -> str:
    when = _instant(value)
    if when is None:
        return "—"
    if _ET is not None:
        local = when.astimezone(_ET)
        return local.strftime(fmt) + " ET"
    return (when - timedelta(hours=4)).strftime(fmt) + " ET"


def signed(x: Any, nd: int = 1) -> str:
    if x is None:
        return "—"
    v = float(x)
    if abs(v) < 0.5 * 10 ** (-nd):
        v = 0.0
    return f"{v:+.{nd}f}"


def price(x: Any) -> str:
    if x is None:
        return "—"
    v = round(float(x))
    return f"+{v}" if v > 0 else f"{v}"


def pct(x: Any, nd: int = 1) -> str:
    return "—" if x is None else f"{float(x) * 100:.{nd}f}%"


def pct_signed(x: Any, nd: int = 1) -> str:
    if x is None:
        return "—"
    v = float(x) * 100
    if abs(v) < 0.5 * 10 ** (-nd):
        return f"{0:.{nd}f}%"
    return f"{v:+.{nd}f}%"


def line_txt(x: Any) -> str:
    if x is None:
        return "—"
    v = float(x)
    if abs(v) < 1e-9:
        return "PK"
    return f"{v:+g}"


def pill(state: str, text: str | None = None) -> str:
    cls = STATE_CLASS.get(state, "na")
    return f'<span class="pill {cls}">{e(text or STATE_LABEL.get(state, state))}</span>'


def disposition_pill(d: str) -> str:
    cls = {"BET": "bet", "LEAN": "lean", "PASS": "pass"}.get(d, "pass")
    return f'<span class="disp {cls}">{e(d)}</span>'


# ---------------------------------------------------------------------------

CSS = """
/* Layout: one column of ticket-like panels; summary first, detail after;
   wide tables scroll inside their own frames. */
:root {
  --ground: #f3f5f8; --surface: #ffffff; --sunk: #eaeef3;
  --ink: #111a24; --muted: #566273; --faint: #7d8898;
  --rule: #d6dde6; --rule-strong: #b9c3cf;
  --accent: #1d4f8c; --accent-soft: #e3ecf7;
  --ok: #1f7a4d; --ok-soft: #e2f3ea;
  --warn: #9a5b00; --warn-soft: #fbf0dc;
  --bad: #b3261e; --bad-soft: #fbe6e4;
  --na: #6b7684; --na-soft: #eceff3;
  --lean: #8a4b00; --bet: #1f7a4d;
  --display: "Barlow Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "Barlow", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #0e131a; --surface: #151c25; --sunk: #1b2430;
    --ink: #e7edf4; --muted: #a0adbd; --faint: #7d8a9b;
    --rule: #263140; --rule-strong: #34414f;
    --accent: #8ab8f2; --accent-soft: #1b2a3d;
    --ok: #5cc98f; --ok-soft: #14291f;
    --warn: #f0b457; --warn-soft: #2d2413;
    --bad: #ff8a7a; --bad-soft: #321a18;
    --na: #9aa6b4; --na-soft: #1e2630;
    --lean: #f0b457; --bet: #5cc98f;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --ground: #0e131a; --surface: #151c25; --sunk: #1b2430;
  --ink: #e7edf4; --muted: #a0adbd; --faint: #7d8a9b;
  --rule: #263140; --rule-strong: #34414f;
  --accent: #8ab8f2; --accent-soft: #1b2a3d;
  --ok: #5cc98f; --ok-soft: #14291f;
  --warn: #f0b457; --warn-soft: #2d2413;
  --bad: #ff8a7a; --bad-soft: #321a18;
  --na: #9aa6b4; --na-soft: #1e2630;
  --lean: #f0b457; --bet: #5cc98f;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--ground); color: var(--ink); font-family: var(--body);
  font-size: 16px; line-height: 1.5; margin: 0; padding-block: 28px 64px;
  padding-inline: 16px; }
.wrap { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 34px; }
h1, h2, h3 { font-family: var(--display); margin: 0; text-wrap: balance; letter-spacing: .005em; }
h1 { font-size: clamp(2.3rem, 6vw, 3.6rem); line-height: 1; font-weight: 700; }
h2 { font-size: 1.55rem; font-weight: 700; line-height: 1.1; }
h3 { font-size: 1.15rem; font-weight: 600; }
p { margin: 0; max-width: 68ch; }
.eyebrow { font-family: var(--mono); font-size: .72rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--accent); }
.muted { color: var(--muted); }
.mono, .num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
code { font-family: var(--mono); font-size: .88em; background: var(--sunk);
  padding: .08em .35em; border-radius: 3px; }
section { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.sechead { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 16px; }
.sechead p { font-size: .95rem; }

/* masthead */
header.mast { display: flex; flex-direction: column; gap: 12px; padding-bottom: 20px;
  border-bottom: 3px solid var(--ink); }
.mast .week { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; }
.mast .alias { font-family: var(--mono); font-size: .8rem; color: var(--muted); }
.verdict { font-size: 1.12rem; font-weight: 500; max-width: 70ch; }
.meta { display: flex; flex-wrap: wrap; gap: 4px 18px; font-family: var(--mono); font-size: .74rem;
  color: var(--muted); border-top: 1px solid var(--rule); padding-top: 10px; }

/* kpi strip */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 1px;
  background: var(--rule); border: 1px solid var(--rule); }
.kpi { background: var(--surface); padding: 14px 14px 12px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.kpi .v { font-family: var(--display); font-size: 2.3rem; line-height: 1; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi .k { font-family: var(--mono); font-size: .68rem; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.kpi .n { font-size: .8rem; color: var(--muted); overflow-wrap: anywhere; }
.kpi.bad .v { color: var(--bad); } .kpi.warn .v { color: var(--warn); }
.kpi.ok .v { color: var(--ok); } .kpi.lean .v { color: var(--lean); }

/* tables */
.frame { overflow-x: auto; border: 1px solid var(--rule); background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-family: var(--mono); font-size: .66rem; letter-spacing: .09em; text-transform: uppercase;
  color: var(--muted); font-weight: 500; background: var(--sunk); white-space: nowrap; }
td.r, th.r { text-align: right; }
td.num { white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.game b { font-weight: 600; }
.game .when { display: block; font-family: var(--mono); font-size: .72rem; color: var(--muted); margin-top: 2px; }
.neg { color: var(--bad); } .pos { color: var(--ok); }
.empty td { color: var(--muted); padding: 18px 12px; font-size: .95rem; }

/* pills */
.pill { display: inline-block; font-family: var(--mono); font-size: .66rem; letter-spacing: .06em;
  text-transform: uppercase; padding: 2px 7px; border-radius: 3px; white-space: nowrap; }
.pill.ok { background: var(--ok-soft); color: var(--ok); }
.pill.warn { background: var(--warn-soft); color: var(--warn); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.pill.na { background: var(--na-soft); color: var(--na); }
.disp { display: inline-block; font-family: var(--display); font-weight: 700; font-size: .95rem;
  letter-spacing: .06em; padding: 1px 9px; border: 1.5px solid currentColor; border-radius: 3px; }
.disp.bet { color: var(--bet); } .disp.lean { color: var(--lean); } .disp.pass { color: var(--na); }
.dots { display: inline-flex; gap: 3px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); } .dot.warn { background: var(--warn); }
.dot.bad { background: var(--bad); } .dot.na { background: var(--na); }
.codes { font-family: var(--mono); font-size: .7rem; color: var(--muted); }

/* callouts */
.callout { border-left: 3px solid var(--accent); background: var(--surface); padding: 12px 16px;
  display: flex; flex-direction: column; gap: 6px; }
.callout.bad { border-left-color: var(--bad); }
.callout p { font-size: .95rem; }

/* health grid */
.health { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; }
.gate { background: var(--surface); border: 1px solid var(--rule); border-top: 4px solid var(--na);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.gate.ok { border-top-color: var(--ok); } .gate.warn { border-top-color: var(--warn); }
.gate.bad { border-top-color: var(--bad); }
.gate .top { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
.gate .name { font-family: var(--display); font-weight: 600; font-size: 1.08rem; }
.gate p { font-size: .86rem; color: var(--muted); overflow-wrap: anywhere; }

/* shadow + pass filters */
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: inherit; font-size: .82rem; font-weight: 600; color: var(--ink); background: var(--surface);
  border: 1px solid var(--rule-strong); border-radius: 999px; padding: 5px 12px; cursor: pointer; }
.chip[aria-pressed="true"] { background: var(--ink); color: var(--ground); border-color: var(--ink); }
.chip:focus-visible, .rowbtn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.rowbtn { font: inherit; background: none; border: 1px solid var(--rule-strong); color: var(--ink);
  border-radius: 4px; padding: 1px 7px; cursor: pointer; font-size: .8rem; }
tr.detail td { background: var(--sunk); }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 8px 18px; }
.detail-grid div { font-size: .82rem; }
.detail-grid .lab { font-family: var(--mono); font-size: .66rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }

/* chart */
.chart { background: var(--surface); border: 1px solid var(--rule); padding: 14px 14px 8px; overflow-x: auto; }
.chart svg { display: block; width: 100%; min-width: 560px; height: auto; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: .8rem; color: var(--muted); padding-top: 6px; }
.legend i { display: inline-block; width: 12px; height: 3px; vertical-align: middle; margin-right: 6px; }

ul.plain { margin: 0; padding-left: 1.1rem; display: flex; flex-direction: column; gap: 7px; max-width: 72ch; }
ul.plain li { font-size: .95rem; }
footer { border-top: 1px solid var(--rule); padding-top: 14px; font-size: .8rem; color: var(--muted);
  display: flex; flex-direction: column; gap: 6px; overflow-wrap: anywhere; }
.codes { overflow-wrap: anywhere; }
@media (max-width: 560px) {
  body { font-size: 15px; }
  th, td { padding: 8px 9px; }
  .kpi .v { font-size: 1.9rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500;600&'
         'family=IBM+Plex+Mono:wght@400;500&display=swap">')

JS = """
(function () {
  var chips = document.querySelectorAll('[data-filter]');
  var rows = document.querySelectorAll('#passlist tbody tr.main');
  function apply(f) {
    rows.forEach(function (r) {
      var tags = (r.getAttribute('data-tags') || '').split(' ');
      var show = f === 'all' || tags.indexOf(f) !== -1;
      r.hidden = !show;
      var d = document.getElementById(r.getAttribute('data-detail'));
      if (d && !show) d.hidden = true;
    });
    chips.forEach(function (c) { c.setAttribute('aria-pressed', c.getAttribute('data-filter') === f ? 'true' : 'false'); });
    try { localStorage.setItem('cfb-card-filter', f); } catch (err) {}
  }
  chips.forEach(function (c) { c.addEventListener('click', function () { apply(c.getAttribute('data-filter')); }); });
  document.querySelectorAll('.rowbtn').forEach(function (b) {
    b.addEventListener('click', function () {
      var d = document.getElementById(b.getAttribute('aria-controls'));
      if (!d) return;
      d.hidden = !d.hidden;
      b.setAttribute('aria-expanded', d.hidden ? 'false' : 'true');
      b.textContent = d.hidden ? 'Gates' : 'Hide';
    });
  });
  var start = 'all';
  try { start = localStorage.getItem('cfb-card-filter') || 'all'; } catch (err) {}
  if (!document.querySelector('[data-filter="' + start + '"]')) start = 'all';
  apply(start);
})();
"""


# ---------------------------------------------------------------------------
# sections

def _masthead(card: Mapping[str, Any]) -> str:
    lab = card.get("label") or {}
    s = card["summary"]
    kicks = [r.get("kickoff") for r in card.get("passList", []) + card.get("plays", []) if r.get("kickoff")]
    first = min(kicks) if kicks else None
    last = max(kicks) if kicks else None
    span = ""
    if first and last:
        a, b = _instant(first), _instant(last)
        if a and b:
            if _ET is not None:
                a, b = a.astimezone(_ET), b.astimezone(_ET)
            span = f"{a.strftime('%b %-d')}–{b.strftime('%-d')}" if a.month == b.month else \
                   f"{a.strftime('%b %-d')}–{b.strftime('%b %-d')}"
    week = lab.get("productWeek")
    provider = lab.get("providerWeek")
    board = None
    for r in card.get("passList", []) + card.get("plays", []):
        if r.get("market"):
            board = r["market"].get("boardFetchedAt")
            break
    return f"""
<header class="mast">
  <div class="eyebrow">CFB Edge · Weekly card · Phase: shadow · No money at risk</div>
  <div class="week"><h1>CFB Edge — Week {e(week)}</h1>
    <span class="alias">{e(span)}, {e(lab.get('season'))} · product week {e(week)} = CFBD / ESPN week {e(provider)}</span></div>
  <p class="verdict">{e(s['verdict'])}</p>
  <div class="meta">
    <span>Card as of {e(et(card.get('asOf')))}</span>
    <span>Prices from board {e(et(board))}</span>
    <span>Engine {e(card.get('engineVersion'))} · card {e(card.get('cardVersion'))}</span>
    <span>main {e(str(card.get('codeRevision') or '—')[:7])} · evidence {e(str(card.get('evidenceRevision') or '—')[:7])}</span>
    <span>sha256 {e(str(card.get('cardSha256'))[:12])}</span>
  </div>
</header>"""


def _kpis(card: Mapping[str, Any]) -> str:
    s = card["summary"]
    health = card.get("systemHealth") or []
    fails = sum(1 for h in health if h["state"] == "FAIL")
    degraded = sum(1 for h in health if h["state"] == "DEGRADED")
    auth = card.get("authority") or {}
    blocked = not auth.get("allowPaperDelivery")
    tiles = [
        ("", s["games"], "Games evaluated", "every game gets a disposition"),
        ("ok" if s["bet"] else "", s["bet"], "Bet", "clears every gate"),
        ("lean" if s["lean"] else "", s["lean"], "Lean", "price edge, zero stake"),
        ("", s["pass"], "Pass", "evaluated, no edge"),
        ("bad" if fails else ("warn" if degraded else "ok"), fails, "Gates failing",
         f"{degraded} degraded"),
        ("bad" if blocked else "ok", "Blocked" if blocked else "Open", "Delivery authority",
         f"{auth.get('modelId')} {auth.get('status')}"),
    ]
    cells = "".join(
        f'<div class="kpi {cls}"><span class="k">{e(k)}</span><span class="v">{e(v)}</span>'
        f'<span class="n">{e(n)}</span></div>' for cls, v, k, n in tiles)
    return f'<div class="kpis">{cells}</div>'


def _best_side(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    sides = [s for s in row.get("sides") or [] if s.get("evConservative") is not None]
    return max(sides, key=lambda s: s["evConservative"]) if sides else None


def _exec_table(card: Mapping[str, Any]) -> str:
    plays = card.get("plays") or []
    head = ("<tr><th class='r'>Rank</th><th>Game</th><th>Pick</th><th class='r'>Current line</th>"
            "<th class='r'>Model line / fair price</th><th class='r'>Edge</th>"
            "<th class='r'>Confidence</th><th>Decision</th></tr>")
    body = []
    for r in plays:
        d = r["decision"]
        p = d.get("pick") or {}
        m = r.get("market") or {}
        body.append(
            f"<tr><td class='r num'>{e(d.get('rank'))}</td>"
            f"<td class='game'><b>{e(r['game'])}</b><span class='when'>{e(et(r.get('kickoff')))}</span></td>"
            f"<td><b>{e(p.get('team'))} {e(line_txt(p.get('line')))}</b> <span class='muted'>spread</span></td>"
            f"<td class='r num'>{e(line_txt(p.get('line')))} {e(price(p.get('price')))} "
            f"<span class='muted'>{e(p.get('book'))}</span></td>"
            f"<td class='r num'>{e(line_txt(p.get('line')))} {e(price(p.get('fairAmerican')))} "
            f"<span class='muted'>{e(m.get('reference'))} no-vig</span></td>"
            f"<td class='r num {'pos' if (d.get('edge') or 0) > 0 else 'neg'}'>{e(pct_signed(d.get('edge')))}</td>"
            f"<td class='r num'>{e(pct(d.get('confidence')))}</td>"
            f"<td>{disposition_pill(d['disposition'])}</td></tr>")
    if not body:
        body.append("<tr class='empty'><td colspan='8'>No play clears the gates this week. "
                    "Every game was evaluated; the pass list below shows why each one stopped.</td></tr>")
    return f"<div class='frame'><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"


def _near_misses(card: Mapping[str, Any], n: int = 6) -> str:
    rows = []
    for r in card.get("passList") or []:
        b = _best_side(r)
        if b is not None:
            rows.append((b["evConservative"], r, b))
    rows.sort(key=lambda t: (-t[0], t[1]["game"]))
    body = []
    for ev, r, b in rows[:n]:
        codes = ", ".join(c for c in r["decision"]["reasonCodes"] if c not in GLOBAL_CODES)
        body.append(
            f"<tr><td class='game'><b>{e(r['game'])}</b><span class='when'>{e(et(r.get('kickoff')))}</span></td>"
            f"<td><b>{e(b['team'])} {e(line_txt(b['line']))}</b></td>"
            f"<td class='r num'>{e(price(b['price']))} <span class='muted'>{e(b['bestBook'])}</span></td>"
            f"<td class='r num'>{e(price(b.get('fairAmerican')))}</td>"
            f"<td class='r num'>{e(price(b.get('minimumAcceptablePrice')))}</td>"
            f"<td class='r num neg'>{e(pct_signed(ev))}</td>"
            f"<td class='r num'>{e(pct(b.get('pPost')))}</td>"
            f"<td>{disposition_pill('PASS')} <span class='codes'>{e(codes)}</span></td></tr>")
    head = ("<tr><th>Game</th><th>Side</th><th class='r'>Best price</th><th class='r'>Fair</th>"
            "<th class='r'>Needs</th><th class='r'>Edge</th><th class='r'>Confidence</th><th>Decision</th></tr>")
    return f"<div class='frame'><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"


def _shadow(card: Mapping[str, Any]) -> str:
    rows = card.get("shadowSignals") or []
    body = []
    for s in rows:
        moved = s.get("movementSoFar")
        cls = "pos" if (moved or 0) > 0 else ("neg" if (moved or 0) < 0 else "")
        body.append(
            f"<tr><td class='game'><b>{e(s['game'])}</b></td><td><b>{e(s.get('side'))}</b></td>"
            f"<td class='r num'>{e(signed(s.get('projectionGapVsOpen'), 2))}</td>"
            f"<td class='r num {cls}'>{e(signed(moved, 2))}</td>"
            f"<td class='r num neg'>{e(pct_signed(s.get('bestConservativeEv')))}</td>"
            f"<td>{pill('DEGRADED' if s.get('status') == 'BLOCKED' else 'PASS', s.get('status'))}"
            f" <span class='codes'>{e(', '.join(s.get('exclusions') or []))}</span></td></tr>")
    if not body:
        body.append("<tr class='empty'><td colspan='6'>No audit-grade shadow signal this week.</td></tr>")
    head = ("<tr><th>Game</th><th>Signal side</th><th class='r'>Gap vs open</th>"
            "<th class='r'>Moved so far</th><th class='r'>Best EV now</th><th>Status</th></tr>")
    return f"<div class='frame'><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"


# datetime.weekday(): Monday is 0. A football week runs Tuesday to Monday.
DAY_TAG = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
DAY_NAME = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday",
            5: "Saturday", 6: "Sunday"}
DAY_ORDER = [1, 2, 3, 4, 5, 6, 0]


def _dots(gates: Mapping[str, Any]) -> str:
    out = []
    for k in GATE_ORDER:
        st = (gates.get(k) or {}).get("state", "NOT_APPLICABLE")
        out.append(f"<span class='dot {STATE_CLASS.get(st, 'na')}' title='{e(GATE_SHORT[k])}: "
                   f"{e(STATE_LABEL.get(st, st))}'></span>")
    return f"<span class='dots' aria-label='row gates'>{''.join(out)}</span>"


def _pass_list(card: Mapping[str, Any]) -> str:
    rows = card.get("passList") or []
    body = []
    days_seen: set[int] = set()
    for i, r in enumerate(rows):
        m = r.get("market") or {}
        tags = ["all"]
        kick = _instant(r.get("kickoff"))
        if kick is not None:
            local = kick.astimezone(_ET) if _ET is not None else kick - timedelta(hours=4)
            days_seen.add(local.weekday())
            tags.append(DAY_TAG[local.weekday()])
        codes = [c for c in r["decision"]["reasonCodes"] if c not in GLOBAL_CODES]
        if "LINE_MISMATCH" in codes:
            tags.append("mismatch")
        if (r.get("shadow") or {}).get("auditGrade"):
            tags.append("shadow")
        if not r.get("inFrozenCohort"):
            tags.append("outside")
        sides = {s["side"]: s for s in r.get("sides") or []}

        def side_cell(key: str) -> str:
            s = sides.get(key)
            if not s or s.get("price") is None:
                return "<td class='r num muted'>—</td>"
            ev = s.get("evConservative")
            return (f"<td class='r num'>{e(price(s['price']))} <span class='muted'>{e(s.get('bestBook'))}</span>"
                    f"<br><span class='{'neg' if (ev or 0) < 0 else 'pos'}'>{e(pct_signed(ev))}</span></td>")

        det_id = f"g{i}"
        proj = (r.get("projection") or {}).get("homeMargin")
        gap = (r.get("projection") or {}).get("gapVsReference")
        k = r.get("kalshi") or {}
        body.append(
            f"<tr class='main' data-tags='{' '.join(tags)}' data-detail='{det_id}'>"
            f"<td class='game'><b>{e(r['game'])}</b><span class='when'>{e(et(r.get('kickoff')))}</span></td>"
            f"<td class='r num'>{e(line_txt(m.get('referenceHomeLine')))}<br>"
            f"<span class='muted'>{e(m.get('reference') or '—')}</span></td>"
            f"<td class='r num'>{e(pct(m.get('noVigHome')))}</td>"
            f"{side_cell('home')}{side_cell('away')}"
            f"<td class='r num'>{e(signed(proj))}<br><span class='muted'>gap {e(signed(gap))}</span></td>"
            f"<td>{_dots(r.get('gates') or {})}<br><span class='codes'>{e(', '.join(codes))}</span></td>"
            f"<td><button class='rowbtn' type='button' aria-expanded='false' aria-controls='{det_id}'>Gates</button></td></tr>")
        gate_bits = "".join(
            f"<div><span class='lab'>{e(GATE_SHORT[g])}</span> {pill((r['gates'].get(g) or {}).get('state', 'NOT_APPLICABLE'))}"
            f"<br>{e((r['gates'].get(g) or {}).get('detail'))}</div>" for g in GATE_ORDER if g in r.get("gates", {}))
        extra = ""
        if k:
            extra += (f"<div><span class='lab'>Kalshi derived line</span><br>{e(line_txt(k.get('derivedHomeLine')))} "
                      f"home at {e(et(k.get('observedAt')))}; context, not a price.</div>")
        risks = "".join(f"<li>{e(x)}</li>" for x in r.get("risks") or [])
        if risks:
            extra += f"<div><span class='lab'>Risks</span><ul class='plain'>{risks}</ul></div>"
        body.append(f"<tr class='detail' id='{det_id}' hidden><td colspan='8'><div class='detail-grid'>"
                    f"{gate_bits}{extra}</div></td></tr>")
    head = ("<tr><th>Game</th><th class='r'>Ref line (home)</th><th class='r'>No-vig home</th>"
            "<th class='r'>Best home price · EV</th><th class='r'>Best away price · EV</th>"
            "<th class='r'>Projection (0 wt)</th><th>Gates · reasons</th><th></th></tr>")
    everywhere = sorted({c for r in rows for c in r["decision"]["reasonCodes"] if c in GLOBAL_CODES
                         and all(c in x["decision"]["reasonCodes"] for x in rows)})
    note = (f"<p class='codes'>Every row also carries: {e(', '.join(everywhere))}.</p>" if everywhere else "")
    # One chip per day that actually has a game (US Eastern), so a week with
    # Tuesday and Wednesday kickoffs gets those chips and no empty ones.
    chips = ([("all", "All")] + [(DAY_TAG[d], DAY_NAME[d]) for d in sorted(days_seen, key=DAY_ORDER.index)]
             + [("shadow", "Shadow signal"), ("mismatch", "Line mismatch"), ("outside", "Outside cohort")])
    chip_html = "".join(f"<button class='chip' type='button' data-filter='{k}' aria-pressed='false'>{e(v)}</button>"
                        for k, v in chips)
    return (f"{note}<div class='chips' role='group' aria-label='Filter the pass list'>{chip_html}</div>"
            f"<div class='frame'><table id='passlist'><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>")


def _health(card: Mapping[str, Any]) -> str:
    items = []
    for h in card.get("systemHealth") or []:
        cls = STATE_CLASS.get(h["state"], "na")
        items.append(f"<div class='gate {cls}'><div class='top'><span class='name'>{e(h['label'])}</span>"
                     f"{pill(h['state'])}</div><p>{e(h['detail'])}</p></div>")
    return f"<div class='health'>{''.join(items)}</div>"


def _measured_lines(calibration: Mapping[str, Any] | None, card: Mapping[str, Any]) -> list[tuple[str, float | None, str]]:
    h1 = (calibration or {}).get("h1") or {}
    reg = ((h1.get("registeredRule") or {}).get("clv") or {}).get("mean")
    seasons = h1.get("bySeason_registeredRule") or {}
    recent = [((v.get("clv") or {}).get("mean"), (v.get("clv") or {}).get("n") or 0)
              for k, v in sorted(seasons.items()) if k in ("2024", "2025")]
    tot = sum(n for m, n in recent if m is not None)
    recent_mean = (sum(m * n for m, n in recent if m is not None) / tot) if tot else None
    in_season = card.get("inSeasonClv") or {}
    ins = (in_season.get("allGames") or {}).get("mean")
    return [("2023–25 rule", reg, "var(--ok)"),
            ("2024–25 rule", recent_mean, "var(--warn)"),
            (f"{_in_season_span(in_season)}, all games", ins, "var(--bad)")]


def _span(numbers: Sequence[int]) -> str:
    numbers = sorted(set(numbers))
    if not numbers:
        return ""
    return str(numbers[0]) if len(numbers) == 1 else f"{numbers[0]}–{numbers[-1]}"


def _in_season_span(in_season: Mapping[str, Any]) -> str:
    """'2026 wk 1–3' from the weeks the in-season measurement actually holds."""
    weeks = [int(w) for w in (in_season.get("byWeek") or {}) if str(w).isdigit()]
    season = in_season.get("season")
    return f"{season or 'this season'} wk {_span(weeks)}".strip()


def _evidence_chart(calibration: Mapping[str, Any] | None, card: Mapping[str, Any]) -> str:
    """Breakeven line movement by strike against measured CLV, on one scale."""
    econ = ((calibration or {}).get("economics") or {}).get("rows") or []
    bars = [(r["label"], float(r["breakevenClvPoints"])) for r in econ if r.get("breakevenClvPoints")]
    if not bars:
        return ""
    measured = _measured_lines(calibration, card)
    x_max = 2.0
    width, left, right = 720, 176, 40
    top = 44                      # room for the measured-line labels
    row_h = 30
    plot_w = width - left - right
    height = top + row_h * len(bars) + 34
    sx = lambda v: left + max(0.0, min(v, x_max)) / x_max * plot_w
    mono = "IBM Plex Mono, ui-monospace, monospace"
    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' "
             f"aria-label='Line movement needed to pay the exchange fee at each strike, "
             f"against measured closing line value'>"]
    for tick in (0.0, 0.5, 1.0, 1.5, 2.0):
        x = sx(tick)
        parts.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{top - 4}' y2='{height - 26}' "
                     f"stroke='var(--rule)' stroke-width='1'/>")
        parts.append(f"<text x='{x:.1f}' y='{height - 10}' text-anchor='middle' font-family='{mono}' "
                     f"font-size='11' fill='var(--muted)'>{tick:.1f} pts</text>")
    # Measured markers sit behind the bars; labels stagger above the plot.
    for i, (label, v, col) in enumerate(measured):
        if v is None:
            continue
        x = sx(v)
        y_label = 12 + i * 11
        parts.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{y_label + 3}' y2='{height - 26}' "
                     f"stroke='{col}' stroke-width='2' stroke-dasharray='5 3'/>")
        parts.append(f"<text x='{x + 5:.1f}' y='{y_label + 2}' font-family='{mono}' font-size='10.5' "
                     f"fill='{col}'>{e(label)} {v:+.2f}</text>")
    for i, (label, be) in enumerate(bars):
        y = top + i * row_h
        x_end = sx(be)
        parts.append(f"<text x='{left - 10}' y='{y + 15}' text-anchor='end' font-size='12.5' "
                     f"fill='var(--ink)' font-family='Barlow, system-ui, sans-serif'>{e(label)}</text>")
        parts.append(f"<rect x='{left}' y='{y + 5}' width='{max(1.0, x_end - left):.1f}' height='14' "
                     f"fill='var(--accent-soft)' stroke='var(--accent)' stroke-width='1'/>")
        over = be > x_max
        label_txt = f"{be:.2f}" + (" →" if over else "")
        if over or (x_end - left) > 48:
            # Inside the bar's right end, clear of the measured markers.
            parts.append(f"<text x='{x_end - 5:.1f}' y='{y + 16}' text-anchor='end' font-family='{mono}' "
                         f"font-size='11' fill='var(--accent)'>{label_txt}</text>")
        else:
            parts.append(f"<text x='{x_end + 5:.1f}' y='{y + 16}' font-family='{mono}' font-size='11' "
                         f"fill='var(--accent)'>{label_txt}</text>")
    parts.append("</svg>")
    legend = ("<span><i style='background:var(--accent)'></i>bar: line movement a Kalshi spread "
              "contract needs to pay its fee at that strike (pick'em game)</span>"
              "<span>dashed: measured directional CLV at the sportsbook opener</span>")
    return f"<div class='chart'>{''.join(parts)}<div class='legend'>{legend}</div></div>"


def _evidence(calibration: Mapping[str, Any] | None, card: Mapping[str, Any]) -> str:
    cal = calibration or {}
    mb = cal.get("marketBaseline") or {}
    pin = mb.get("pinnacleNoVigCalibration") or {}
    enc = cal.get("encompassing") or {}
    wf = cal.get("calibration") or {}
    h1 = cal.get("h1") or {}
    reg = h1.get("registeredRule") or {}
    clv = reg.get("clv") or {}
    gate = card.get("clvGate") or {}
    ins = card.get("inSeasonClv") or {}
    econ = {r["label"]: r for r in (cal.get("economics") or {}).get("rows") or []}
    key3 = (econ.get("key number 3") or {}).get("breakevenClvPoints")
    atm = (econ.get("pick'em strike, off key") or {}).get("breakevenClvPoints")
    measured = {l: v for l, v, _ in _measured_lines(calibration, card)}
    facts = []
    if enc:
        facts.append(f"The projection adds nothing to the closing line: coefficient "
                     f"{signed(enc.get('projectionCoef'), 3)} (t {signed(enc.get('projectionT'), 2)}) over "
                     f"{enc.get('n')} walk-forward games, so its weight in the posterior is zero.")
    if pin.get("games"):
        facts.append(f"The market prior is calibrated and carries no side information on a spread: Pinnacle "
                     f"no-vig Brier {pin.get('brier'):.4f} against a coin flip's {pin.get('brierCoinFlip'):.4f}, "
                     f"ECE {pin.get('ece'):.3f}, n = {pin.get('games')} ({_span(pin.get('seasons') or [])}; the "
                     f"source has no priced Pinnacle closes after that, so recent calibration is assumed). "
                     f"A confidence near 50% is what calibration allows.")
    if clv:
        facts.append(f"Line movement is predictable but small and unstable: the registered rule (week 3+, "
                     f"gap 4+) earned {signed(clv.get('mean'), 3)} pts (95% CI {signed(clv.get('lo95'), 2)} to "
                     f"{signed(clv.get('hi95'), 2)}, {clv.get('clusters')} week clusters) on 2023–25 games whose "
                     f"openers agree across books; 2024–25 alone {signed(measured.get('2024–25 rule'), 3)}. "
                     f"ROI at an assumed −110: {pct_signed(reg.get('roiAtAssumedMinus110'))}.")
    if key3 and atm:
        facts.append(f"Where the edge is expressed decides whether it can pay. An at-the-money exchange strike "
                     f"needs {atm:.2f} pts of movement to cover its fee (D31 uses 0.675 under a smooth normal); "
                     f"a key-number strike needs about {key3:.2f}. The pooled estimate straddles the key-number "
                     f"bar and the recent seasons fall short of it. Whether sportsbook line movement transfers to "
                     f"an exchange strike is untested, so this is an open question for a preregistered cohort, "
                     f"not a reason to bet.")
    rr = ins.get("registeredRule") or {}
    if ins.get("allGames"):
        a = ins["allGames"]
        by_week = ins.get("byWeek") or {}
        rule_weeks = rr.get("weeks") or []
        if len(rule_weeks) == 1:
            where = (f", all from week {rule_weeks[0]}, which is one week cluster. One good week is the "
                     f"case the four-cluster minimum exists for.")
        elif rule_weeks:
            where = f", across weeks {_span(rule_weeks)} ({len(rule_weeks)} week clusters)."
        else:
            where = "."
        weekly = "; ".join(f"week {w} {signed((v or {}).get('mean'), 2)}" for w, v in sorted(by_week.items()))
        facts.append(f"This season so far ({_in_season_span(ins)}): {signed(a.get('mean'), 3)} pts over "
                     f"{a.get('n')} games (t {signed(a.get('t'), 2)}); the registered subset is "
                     f"{signed(rr.get('mean'), 3)} over {rr.get('n')} games{where}"
                     + (f" By week: {weekly}." if weekly else ""))
    if wf.get("pooledN"):
        facts.append(f"Per-game direction does not forecast out of sample: walk-forward P(line moves our way) "
                     f"scored Brier {wf.get('pooledBrier'):.4f} against a coin flip's "
                     f"{wf.get('pooledBrierCoinFlip'):.4f} (n = {wf.get('pooledN')}).")
    if gate:
        facts.append(f"The CLV gate reads {gate.get('verdict')}: {gate.get('gradeable')} gradeable of "
                     f"{gate.get('observations')} captured rows. It needs {gate.get('minimumObservations')} "
                     f"venue-proven opens across {gate.get('minimumWeekClusters')} weeks.")
    lis = "".join(f"<li>{e(f)}</li>" for f in facts)
    return f"{_evidence_chart(calibration, card)}<ul class='plain'>{lis}</ul>"


def _what_changes() -> str:
    items = [
        "Delivery authority passes its registered gates (200+ non-push forecasts, 8+ week clusters, "
        "log-loss and Brier no worse than market, anytime e-value 20+). Until then BET is unavailable.",
        "The CLV gate reads PASS: 96+ venue-proven true opens over 4+ weeks with the anytime e-value past 20 "
        "against the fee-adjusted null.",
        "A registered system with forward evidence contributes Stage A weight, so a price gap stops being NO_EVIDENCE.",
        "A fresh quote (15 minutes or less) beats the sharp no-vig price at the same number under both de-vig methods: "
        "that row becomes a LEAN immediately and a BET only with the two conditions above.",
        "A starting quarterback change or an injury the market has not priced. There is no injury feed, "
        "so re-price any stale number after news.",
    ]
    return "<ul class='plain'>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"


def _trace(card: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{e(i['name'])}</td><td class='mono'>{e(i['path'])}</td>"
        f"<td class='mono'>{e((i.get('sha256') or 'missing')[:16])}</td></tr>"
        for i in card.get("inputs") or [])
    rule = card.get("decisionRule") or {}
    rank = card.get("rankingRule") or {}
    rules = "".join(f"<li><b>{e(k)}</b>: {e(v)}</li>" for k, v in rule.items())
    return (f"<ul class='plain'>{rules}<li><b>Rank</b>: {e(rank.get('formula'))}; "
            f"half-life {e(rank.get('executionHalfLifeHours'))} h; heuristic, not validated.</li></ul>"
            f"<div class='frame'><table><thead><tr><th>Input</th><th>Path</th><th>sha256</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


def render(card: Mapping[str, Any], *, calibration: Mapping[str, Any] | None = None,
           standalone: bool = False) -> str:
    lab = card.get("label") or {}
    # Stable across weekly republishes to the same page; the week is in the H1.
    title = lab.get("title") or "CFB Edge Weekly Card"
    s = card["summary"]
    plays_note = ("None this week." if not card.get("plays") else "")
    leans = [r for r in card.get("plays") or [] if r["decision"]["disposition"] == "LEAN"]
    bets = [r for r in card.get("plays") or [] if r["decision"]["disposition"] == "BET"]
    body = f"""<title>{e(title)}</title>
{FONTS}
<style>{CSS}</style>
<div class="wrap">
{_masthead(card)}
{_kpis(card)}
<section>
  <div class="sechead"><h2>Executive card</h2>
    <p class="muted">Ranked by decision quality: positive edge × evidence × execution. Heuristic order, not validated.</p></div>
  {_exec_table(card)}
</section>
<section>
  <div class="sechead"><h2>Top plays</h2><p class="muted">{e(len(bets))} bet(s).</p></div>
  <div class="callout{' bad' if not bets else ''}"><p>{e('No bet this week. ' + ('Delivery authority is blocked (S02 has not passed its registered gates), so the engine cannot return a staked BET regardless of price.' if not (card.get('authority') or {}).get('allowPaperDelivery') else 'Nothing cleared the evidence and execution gates.'))}</p></div>
</section>
<section>
  <div class="sechead"><h2>Secondary / leans</h2><p class="muted">{e(len(leans))} lean(s). A lean is a same-number price that beats the sharp no-vig line under both de-vig methods, blocked by evidence, authority or freshness. Zero stake.</p></div>
  {'' if leans else "<div class='callout'><p>No lean. On the latest board no book beat Pinnacle's no-vig price at Pinnacle's own number under both de-vig methods. The closest rows are below; each one still costs money at its best price.</p></div>"}
  <h3>Closest to a bet</h3>
  {_near_misses(card)}
</section>
<section>
  <div class="sechead"><h2>Shadow signals</h2>
    <p class="muted">Frozen before kickoff, graded after it against the live Kalshi close. Research only; they move no card decision.</p></div>
  {_shadow(card)}
</section>
<section>
  <div class="sechead"><h2>Pass list</h2><p class="muted">{e(s['pass'])} games evaluated with no edge at the best available price. Tap Gates for the row's evidence.</p></div>
  {_pass_list(card)}
</section>
<section>
  <div class="sechead"><h2>System health</h2><p class="muted">Pass · Degraded · Fail · N/A. A failed gate blocks a bet; it is never turned into a passing input.</p></div>
  {_health(card)}
</section>
<section>
  <div class="sechead"><h2>Why nothing bets</h2><p class="muted">Line movement needed to pay the fee, by strike, against what the signal has measured.</p></div>
  {_evidence(calibration, card)}
</section>
<section>
  <div class="sechead"><h2>What would change the decision</h2></div>
  {_what_changes()}
</section>
<section>
  <div class="sechead"><h2>Traceability</h2><p class="muted">Every number above is recomputable from these files.</p></div>
  {_trace(card)}
</section>
<footer>
  <p>{e(card.get('contract'))} · {e(card.get('cardVersion'))} · engine {e(card.get('engineVersion'))} · spec {e(str(card.get('specHash'))[:12])} · card sha256 {e(card.get('cardSha256'))}</p>
  <p>Posterior at the executable price, net of fees. CLV is the validation signal. The private Site's /api/picks remains the registered delivery authority; this card cannot grant a stake.</p>
</footer>
</div>
<script>{JS}</script>
"""
    if standalone:
        return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
                f"</head><body>{body}</body></html>")
    return body


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(prog="cfb_edge.card_render")
    p.add_argument("--card", required=True)
    p.add_argument("--calibration")
    p.add_argument("--out", required=True)
    p.add_argument("--standalone", action="store_true")
    a = p.parse_args(argv)
    card = json.loads(Path(a.card).read_text(encoding="utf-8"))
    cal = json.loads(Path(a.calibration).read_text(encoding="utf-8")) if a.calibration else None
    Path(a.out).write_text(render(card, calibration=cal, standalone=a.standalone), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

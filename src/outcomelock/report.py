from __future__ import annotations

import html
from pathlib import Path

from .core import Decision

_COLORS = {"proceed": "#10b981", "suppress": "#f59e0b", "blocked": "#ef4444"}


def render_html(
    decisions: list[Decision], output: str | Path, title: str = "OutcomeLock report"
) -> Path:
    rows = []
    for decision in decisions:
        fact = decision.fact
        evidence = (
            "No terminal evidence"
            if fact is None
            else (
                f"{html.escape(fact.verdict)} · {html.escape(fact.source)} · "
                f"{html.escape(fact.locator)} · {html.escape(fact.observed_at)}"
                f"<br><small>{html.escape(fact.summary)}</small>"
            )
        )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(decision.work_key)}</code></td>"
            f"<td><span class='pill' style='--c:{_COLORS[decision.state]}'>{html.escape(decision.state.upper())}</span></td>"
            f"<td>{html.escape(decision.reason)}</td><td>{evidence}</td>"
            "</tr>"
        )
    counts = {state: sum(item.state == state for item in decisions) for state in _COLORS}
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#07111f;--panel:#0d1b2e;--text:#e8f0fb;--muted:#8da2bd;--line:#203653}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#153252 0,var(--bg) 42%);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui;padding:48px 20px}}
main{{max-width:1100px;margin:auto}}h1{{font-size:42px;margin:0}}.sub{{color:var(--muted);font-size:18px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}.num{{font-size:32px;font-weight:800}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}}th,td{{padding:14px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted)}}code{{color:#9bdcff}}.pill{{display:inline-block;border:1px solid var(--c);color:var(--c);border-radius:999px;padding:3px 9px;font-weight:800;font-size:12px}}small{{color:var(--muted)}}
@media(max-width:760px){{.cards{{grid-template-columns:1fr}}table{{font-size:12px}}th,td{{padding:9px}}}}
</style></head><body><main><h1>OutcomeLock</h1><p class="sub">Deterministic stale-work protection for autonomous agents.</p>
<div class="cards"><div class="card"><div class="num" style="color:#10b981">{counts["proceed"]}</div>Proceed</div><div class="card"><div class="num" style="color:#f59e0b">{counts["suppress"]}</div>Suppressed</div><div class="card"><div class="num" style="color:#ef4444">{counts["blocked"]}</div>Needs reconciliation</div></div>
<table><thead><tr><th>Work key</th><th>Decision</th><th>Reason</th><th>Evidence</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
</main></body></html>"""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path

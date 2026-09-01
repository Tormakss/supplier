"""Eval palaidējs.

    uv run evals                          # visi gadījumi
    uv run evals --case din-11851-piens   # viens
    uv run evals --compare baseline.json  # salīdzinājums ar iepriekšējo
    uv run evals --save baseline.json     # saglabā arī ar nosaukumu
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from ..agent.loop import build_client, run_turn
from ..catalog import db
from ..config import CACHED_INPUT_DISCOUNT, MODEL, PRICING
from .cases import RESULTS_DIR, Case, Check, evaluate, load_cases
from .judge import judge_answer


def estimate_cost(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> float | None:
    """USD par vienu gadījumu, vai None, ja modeļa cena nav zināma."""
    price = PRICING.get(model)
    if price is None:
        return None
    in_price, out_price = price
    fresh = max(0, input_tokens - cached_tokens)
    return (
        fresh * in_price / 1_000_000
        + cached_tokens * in_price * CACHED_INPUT_DISCOUNT / 1_000_000
        + output_tokens * out_price / 1_000_000
    )


def run_case(case: Case, client: Any, conn: Any) -> dict[str, Any]:
    """Viens gadījums jaunā sarunā — bez vēstures piesārņojuma."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": case.email}]
    result = run_turn(messages, conn=conn, client=client)

    tool_blob = "\n".join(c.output for c in result.tool_calls)
    checks = evaluate(case, result.text, tool_blob, len(result.tool_calls))

    judge_tokens = (0, 0)
    if case.judge:
        verdict = judge_answer(client, case.email, result.text, case.judge)
        checks.append(Check("judge", verdict.passed, verdict.reason))
        judge_tokens = (verdict.input_tokens, verdict.output_tokens)

    total_in = result.input_tokens + judge_tokens[0]
    total_out = result.output_tokens + judge_tokens[1]

    return {
        "id": case.id,
        "passed": all(c.passed for c in checks),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
        "answer": result.text,
        "tool_calls": [
            {
                "name": c.name,
                "input": c.input,
                "result_count": c.result_count,
                "is_error": c.is_error,
            }
            for c in result.tool_calls
        ],
        "n_tool_calls": len(result.tool_calls),
        "duration_s": round(result.duration_s, 2),
        "input_tokens": total_in,
        "cached_tokens": result.cached_tokens,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cost_usd": estimate_cost(MODEL, total_in, result.cached_tokens, total_out),
    }


def print_case(console: Console, row: dict[str, Any]) -> None:
    mark = "[green]✓[/green]" if row["passed"] else "[red]✗[/red]"
    console.print(
        f"{mark} [bold]{row['id']:<24}[/bold] "
        f"{row['n_tool_calls']:>2} rīki · {row['duration_s']:>5.1f}s · "
        f"{row['total_tokens']:>7,} tok".replace(",", " ")
    )
    for check in row["checks"]:
        if not check["passed"]:
            console.print(f"    [red]✗ {check['name']}:[/red] {check['detail']}")


def summarise(console: Console, rows: list[dict[str, Any]]) -> None:
    passed = sum(1 for r in rows if r["passed"])
    n = len(rows)
    avg_time = sum(r["duration_s"] for r in rows) / n
    avg_tok = sum(r["total_tokens"] for r in rows) / n
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]

    line = (
        f"[bold]{passed}/{n} izturēti[/bold] · vidēji {avg_time:.1f}s · "
        f"{avg_tok:,.0f} tok".replace(",", " ")
    )
    if costs:
        line += f" · ~${sum(costs) / len(costs):.4f}/gadījums"
    else:
        line += f" · izmaksas: n/a ([dim]{MODEL} nav PRICING sarakstā[/dim])"
    console.print()
    console.print(line)


def compare(console: Console, rows: list[dict[str, Any]], baseline_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    before = {r["id"]: r for r in baseline.get("cases", [])}

    console.print()
    console.print(f"[bold]Salīdzinājums ar {baseline_path.name}[/bold]")
    changes = 0
    for row in rows:
        old = before.get(row["id"])
        if old is None:
            console.print(f"  {row['id']:<24} [dim]jauns gadījums[/dim]")
            continue
        was, now = old["passed"], row["passed"]
        if was == now:
            continue
        changes += 1
        if now:
            console.print(f"  {row['id']:<24} [red]✗[/red] → [green]✓[/green]   (+1)")
        else:
            console.print(
                f"  {row['id']:<24} [green]✓[/green] → [red]✗[/red]   [bold red]REGRESIJA[/bold red]"
            )
    missing = set(before) - {r["id"] for r in rows}
    for case_id in sorted(missing):
        console.print(f"  {case_id:<24} [dim]nav palaists[/dim]")
    if not changes:
        console.print("  [dim]izmaiņu nav[/dim]")

    old_tok = sum(r["total_tokens"] for r in before.values()) / max(1, len(before))
    new_tok = sum(r["total_tokens"] for r in rows) / max(1, len(rows))
    console.print(
        f"  [dim]vidēji tokeni: {old_tok:,.0f} → {new_tok:,.0f}[/dim]".replace(",", " ")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aģenta eval palaidējs")
    parser.add_argument("--case", action="append", help="palaist tikai šo id (var vairākas reizes)")
    parser.add_argument("--compare", type=Path, help="salīdzināt ar iepriekšēju rezultātu failu")
    parser.add_argument("--save", type=Path, help="papildus saglabāt ar šo nosaukumu")
    parser.add_argument("--cases-file", type=Path, help="cits cases.jsonl")
    args = parser.parse_args(argv)

    console = Console()
    cases = load_cases(args.cases_file)
    if args.case:
        wanted = set(args.case)
        unknown = wanted - {c.id for c in cases}
        if unknown:
            console.print(f"[red]Nezināmi gadījumi: {', '.join(sorted(unknown))}[/red]")
            return 2
        cases = [c for c in cases if c.id in wanted]

    console.print(f"[dim]Modelis: {MODEL} · {len(cases)} gadījumi[/dim]")
    console.print()

    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    with db.session() as conn:
        client = build_client()
        for case in cases:
            row = run_case(case, client, conn)
            rows.append(row)
            print_case(console, row)

    summarise(console, rows)

    payload = {
        "model": MODEL,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "passed": sum(1 for r in rows if r["passed"]),
        "total": len(rows),
        "cases": rows,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[dim]Saglabāts: {out.relative_to(out.parents[2])}[/dim]")

    if args.save:
        target = args.save if args.save.is_absolute() else RESULTS_DIR / args.save
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]Saglabāts arī: {target}[/dim]")

    if args.compare:
        path = args.compare if args.compare.is_absolute() else RESULTS_DIR / args.compare
        if path.exists():
            compare(console, rows, path)
        else:
            console.print(f"[yellow]Nav ar ko salīdzināt: {path}[/yellow]")

    # Izejas kods 1, ja kāds krita — lai var iebāzt CI.
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Merge every per-issue ``result.json`` into the run's report."""

from __future__ import annotations

import argparse
import json
from typing import Any, cast

import proposal_model as model
import proposal_policy as policy
from proposal_policy import PublishError

SCHEMA = 1


def report_row(item: dict[str, Any]) -> str:
    """One table row for a result."""
    verdict = str(item.get("verdict"))
    requests = model.usage_number(item.get("premium_requests"))
    output = item.get("pull_request_url") or item.get("branch_url") or "—"
    if item.get("dry_run") and verdict == "proposed":
        output = "dry run"
    notes = [str(r) for r in cast("list[Any]", item.get("reasons") or [])]
    notes += [str(w) for w in cast("list[Any]", item.get("warnings") or [])]
    # Choose the text first, then make it one escaped table cell: the
    # title and the reasons alike descend from agent output.
    text = "; ".join(notes) or str(item.get("pr_title") or "")
    detail = policy.log_safe(text).replace("|", "\\|")[:300]
    return (
        f"| {item.get('repository')}#{item.get('issue')} | {verdict} | {output} "
        f"| {'—' if requests is None else f'{requests:g}'} | {detail} |"
    )


def attempt_of(item: dict[str, Any]) -> int:
    """The run attempt a result came from; results without one count as 0."""
    value = item.get("run_attempt")
    return value if type(value) is int else 0


def total_spend(every: list[dict[str, Any]], shown: list[dict[str, Any]]) -> float:
    """Premium requests the run paid for, each author session once.

    A rerun author job is a new session with its own cost, even when a
    later attempt supersedes its row; a publish retry reuses the same
    session. Results without a session count only where shown.
    """
    sessions: dict[str, float] = {}
    for item in every:
        session = item.get("author_session")
        if isinstance(session, str) and session:
            cost = model.usage_number(item.get("premium_requests")) or 0.0
            sessions[session] = max(sessions.get(session, 0.0), cost)
    unattributed = sum(
        model.usage_number(item.get("premium_requests")) or 0.0
        for item in shown
        if not item.get("author_session")
    )
    return sum(sessions.values()) + unattributed


def run_report(args: argparse.Namespace) -> None:
    """Merge every result.json into one table."""
    unreadable: list[str] = []
    every: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []
    for path in sorted(args.results.rglob("result.json")):
        try:
            item = model.load_json(path, str(path))
        except PublishError as exc:
            unreadable.append(str(exc))
            continue
        every.append(item)
        key = item.get("key")
        if not isinstance(key, str):
            unkeyed.append(item)
        elif attempt_of(item) >= attempt_of(latest.get(key, {})):
            # A retried publish entry uploads a result per attempt; the
            # latest one describes the issue, so count it once.
            latest[key] = item
    results = [latest[key] for key in sorted(latest)] + unkeyed
    lines = [
        "## Code monkey results",
        "",
        "| Issue | Verdict | Output | Premium requests | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    totals: dict[str, int] = dict.fromkeys(policy.VERDICTS, 0)
    for item in results:
        verdict = str(item.get("verdict"))
        totals[verdict] = totals.get(verdict, 0) + 1
        lines.append(report_row(item))
    spend = total_spend(every, results)
    for problem in unreadable:
        lines.append(f"| — | unreadable | — | — | {problem.replace('|', '/')[:300]} |")
    if not results and not unreadable:
        lines.append("| — | — | — | — | no proposals |")
    lines += [
        "",
        f"Proposed {totals['proposed']}, abstained {totals['abstain']}, "
        f"rejected {totals['rejected']}, failed {totals['author-failed']}, "
        f"publish failures {totals.get('publish-failed', 0)}; "
        f"premium requests {spend:g}.",
        "",
    ]
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines), encoding="utf-8")
    args.output_json.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "totals": totals,
                "premium_requests": spend,
                "unreadable": unreadable,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

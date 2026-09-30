# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Rules a proposal must follow, stated without git or GitHub plumbing.

The publisher's offline check (``proposal_check``) walks a bundle and
asks these functions whether each path, mode, message and title is
acceptable. Keeping the rules here means a reviewer can read the
policy in one place, and the tests can exercise it without a clone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_COMMITS = 5
# createCommitOnBranch accepts at most 100 file changes per call.
MAX_FILES_PER_COMMIT = 100
# A commit message larger than this is not a message a reviewer reads.
MAX_MESSAGE_BYTES = 64 * 1024
MAX_ADDED_BYTES = 4 * 1024 * 1024
MAX_BINARY_BYTES = 512 * 1024
DEFAULT_TITLE_LIMIT = 50
BODY_LINE_LIMIT = 72
MAX_PR_TITLE = 256
# GitHub rejects pull request bodies over 65,536 characters.
MAX_PR_BODY = 65_536
# An agent's abstention or failure reason as recorded and as it may
# appear in an issue comment; the manifest itself may be up to 1 MiB.
MAX_REASON = 2_000
MAX_COMMENT_REASON = 4_000
SHA_RE = re.compile(r"[0-9a-f]{40}")
KEY_RE = re.compile(r"[A-Za-z0-9_.-]+")
COMMIT_TYPES = (
    "Fix",
    "Feat",
    "Chore",
    "Docs",
    "Style",
    "Refactor",
    "Perf",
    "Test",
    "Revert",
    "CI",
    "Build",
)
# Matches the organisation's gitlint rule (AGENTS.md 6.6): a
# capitalised type from the list, and a capitalised description.
HEADLINE_RE = re.compile(
    r"^(?:" + "|".join(COMMIT_TYPES) + r")(?:\([^()]+\))?!?: [A-Z].*$"
)
TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z-]*: .+$")
URL_RE = re.compile(r"https?://")
# Same-line whitespace alone: GitHub needs the keyword and reference on
# one line, and four spaces of indentation make the line code.
CLOSES_TEMPLATE = (
    r"(?im)^[ ]{{0,3}}(?:closes|fixes|resolves)[ \t]+(?:{repository})?#{number}\b"
)
PROTECTED_PATHS = ("AGENTS.md", "REUSE.toml", ".gitlint")
PROTECTED_PREFIXES = ("LICENSE", "LICENSES/")
WORKFLOW_PREFIX = ".github/workflows/"
MODE_REGULAR = "100644"
MODE_EXECUTABLE = "100755"
MODE_SYMLINK = "120000"
MODE_SUBMODULE = "160000"
LABEL = "code-monkey"
VERDICTS = ("proposed", "abstain", "rejected", "author-failed", "publish-failed")
MANIFEST_OUTCOMES = ("proposed", "abstain", "author-failed")
# The one manifest schema this publisher reads (docs/DESIGN.md 18.2).
MANIFEST_SCHEMA = 1


class PublishError(Exception):
    """An operational failure: the publisher could not do its job."""


class Rejection(Exception):
    """The proposal broke a rule; recorded as a verdict, not an error."""


@dataclass(frozen=True)
class Identity:
    """The trailer identities the publisher appends to every commit."""

    coauthor: str
    bot_login: str
    bot_email: str

    @property
    def sign_off(self) -> str:
        """The bot's DCO trailer."""
        return f"Signed-off-by: {self.bot_login} <{self.bot_email}>"


def parse_title_limit(gitlint_text: str | None) -> int:
    """Read a gitlint subject limit, or fall back to the organisation rule."""
    if gitlint_text is None:
        return DEFAULT_TITLE_LIMIT
    section = None
    for raw in gitlint_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "title-max-length" and line.startswith("line-length"):
            _, _, value = line.partition("=")
            if value.strip().isdigit():
                return int(value.strip())
    return DEFAULT_TITLE_LIMIT


def safe_path(path: str) -> bool:
    """Reject paths that escape the tree or touch git metadata."""
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return False
    parts = path.split("/")
    return all(part not in ("", ".", "..", ".git") for part in parts)


def protected(path: str) -> bool:
    """Files the agent must never change."""
    return path in PROTECTED_PATHS or any(
        path.startswith(prefix) for prefix in PROTECTED_PREFIXES
    )


def check_change_mode(path: str, status: str, old_mode: str, new_mode: str) -> None:
    """Refuse anything ``createCommitOnBranch`` cannot reproduce faithfully."""
    if status.startswith("T"):
        raise Rejection(f"{path}: type change (file/symlink) is not replayable")
    if new_mode == MODE_SYMLINK:
        raise Rejection(f"{path}: symlinks cannot be created through the API")
    if new_mode == MODE_SUBMODULE:
        raise Rejection(f"{path}: submodules are out of scope")
    if new_mode == MODE_EXECUTABLE:
        raise Rejection(
            f"{path}: executable files cannot be set or preserved through "
            "the API; leave this step to a human"
        )
    if new_mode != MODE_REGULAR:
        raise Rejection(f"{path}: unsupported mode {new_mode}")
    if status.startswith("M") and old_mode != new_mode:
        raise Rejection(f"{path}: mode change {old_mode} -> {new_mode}")


def split_message(message: str) -> tuple[str, list[str], list[str]]:
    """Return headline, body lines and the trailer block.

    The line after the subject must be blank before anything else is
    interpreted, so a trailer glued to the subject is a rejection
    rather than a trailer.
    """
    lines = message.rstrip("\n").split("\n")
    headline = lines[0].rstrip()
    rest = lines[1:]
    if rest and rest[0].strip():
        raise Rejection("commit message lacks a blank line after the subject")
    while rest and not rest[-1].strip():
        rest.pop()
    trailers: list[str] = []
    body = list(rest)
    while body and TRAILER_RE.match(body[-1]):
        trailers.insert(0, body.pop())
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return headline, body, trailers


def check_headline(headline: str, limit: int) -> None:
    """Apply the subject rules a machine can judge."""
    if len(headline) > limit:
        raise Rejection(f"subject exceeds {limit} characters: {headline!r}")
    if not HEADLINE_RE.match(headline):
        raise Rejection(
            f"subject lacks a capitalised Conventional Commit type: {headline!r}"
        )
    if headline.rstrip()[-1] in ".;:!?,":
        raise Rejection(f"subject ends with punctuation: {headline!r}")


def check_body(body: list[str]) -> None:
    """Wrap rule: 72 columns unless the line carries a URL."""
    for line in body:
        if len(line) > BODY_LINE_LIMIT and not URL_RE.search(line):
            raise Rejection(f"body line exceeds {BODY_LINE_LIMIT} characters: {line!r}")


def comment_safe(text: str) -> str:
    """Flatten untrusted text for a one-line issue comment.

    Newlines would let agent output add rendered blocks, and an
    @-mention would notify whoever it names; a zero-width space after
    each @ keeps the text readable without the ping.
    """
    return " ".join(text.split()).replace("@", "@\u200b")


MENTION_RE = re.compile(r"(?<![\w@/.-])@(?=[A-Za-z0-9][A-Za-z0-9-]*(?:/[\w.-]+)?)")


def defuse_mentions(text: str) -> str:
    """Stop agent-written text notifying people, keeping it readable.

    A zero-width space after each @ that would start a user or team
    mention; addresses such as ``user@example.com`` and references
    such as ``actions/checkout@v4`` are left as they are.
    """
    return MENTION_RE.sub("@\u200b", text)


def log_safe(text: str) -> str:
    """Make untrusted text safe to print from a job whose log GitHub parses.

    A newline followed by ``::error::`` or ``::stop-commands::`` would
    let agent output spoof annotations or alter log-command parsing.
    """
    flat = " ".join(text.split())
    return flat.replace("::", ": :").replace("##[", "# #[")


def coauthor_for(model: str, mapping: dict[str, Any]) -> str:
    """Map a model identifier prefix to its trailer identity."""
    for prefix, trailer in mapping.items():
        if isinstance(trailer, str) and model.startswith(str(prefix)):
            return trailer
    raise PublishError(f"no co-author mapping for model {model!r}")


def trailer_address(line: str) -> str | None:
    """The bracketed address of a trailer line, lower-cased, if it has one."""
    found = re.search(r"<([^<>]+)>\s*$", line)
    return found.group(1).strip().lower() if found else None


def compose_trailers(trailers: list[str], identity: Identity) -> list[str]:
    """Ensure the model's co-author and the bot's sign-off close the block."""
    kept = [line for line in trailers if line.strip() != identity.sign_off]
    address = trailer_address(identity.coauthor)
    present = any(
        line.startswith("Co-authored-by:") and trailer_address(line) == address
        for line in kept
    )
    if not present:
        kept.append(f"Co-authored-by: {identity.coauthor}")
    kept.append(identity.sign_off)
    return kept


def compose_message(message: str, identity: Identity, limit: int) -> tuple[str, str]:
    """Check one commit message and return the headline and composed body."""
    headline, body, trailers = split_message(message)
    check_headline(headline, limit)
    check_body(body)
    composed = compose_trailers(trailers, identity)
    body_text = "\n".join(body).rstrip("\n")
    full_body = (body_text + "\n\n" if body_text else "") + "\n".join(composed)
    return headline, full_body


FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})[ \t]*$")


HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)


def strip_code(markdown: str) -> str:
    """Drop text where GitHub ignores closing keywords.

    That is HTML comments, fenced blocks and code spans. An unclosed
    comment or fence hides everything after it, so that tail goes too.
    """
    markdown = HTML_COMMENT_RE.sub("", markdown)
    kept: list[str] = []
    fence = ""
    for line in markdown.split("\n"):
        opener = FENCE_OPEN_RE.match(line)
        if fence:
            # A closer is indented at most three spaces, uses the
            # opener's character, and is at least as long.
            closer = FENCE_CLOSE_RE.match(line)
            if (
                closer
                and closer.group(1)[0] == fence[0]
                and len(closer.group(1)) >= len(fence)
            ):
                fence = ""
            continue
        if opener:
            fence = opener.group(1)
            continue
        kept.append(line)
    return strip_code_spans("\n".join(kept))


def strip_code_spans(text: str) -> str:
    """Remove inline code spans in one linear pass.

    A span opens with a run of N backticks and closes at the next run
    of exactly N; an opener with no closer is literal text. Run lengths
    are indexed once, so each closer is found without rescanning, and
    no input can make this slower than linear.
    """
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i] == "`":
            start = i
            while i < len(text) and text[i] == "`":
                i += 1
            runs.append((start, i - start))
        else:
            i += 1
    # Later runs of each length, nearest first, found by popping.
    pending: dict[int, list[int]] = {}
    for index in range(len(runs) - 1, -1, -1):
        pending.setdefault(runs[index][1], []).append(index)
    out: list[str] = []
    position = 0
    index = 0
    while index < len(runs):
        start, length = runs[index]
        later = pending[length]
        while later and later[-1] <= index:
            later.pop()
        if not later:
            index += 1
            continue
        closer = later.pop()
        out.append(text[position:start])
        position = runs[closer][0] + length
        index = closer + 1
    out.append(text[position:])
    return "".join(out)


def check_pull_request_text(
    title: Any,
    body: Any,
    *,
    repository: str,
    issue: int,
    single_headline: str | None,
) -> tuple[str, str]:
    """Check the title and body the agent proposed; return them stripped.

    The closing reference must name this issue in this repository: a
    bare ``#N`` or ``owner/repo#N`` for the selected repository alone,
    so the merged pull request closes what the run set out to close.
    """
    if not isinstance(title, str) or not title.strip():
        raise Rejection("manifest lacks a pull request title")
    if not isinstance(body, str) or not body.strip():
        raise Rejection("manifest lacks a pull request body")
    title = title.strip()
    if single_headline is not None and title != single_headline:
        raise Rejection("single-commit pull request title must equal the subject")
    if len(title) > MAX_PR_TITLE:
        raise Rejection(f"pull request title exceeds {MAX_PR_TITLE} characters")
    # Size first: nothing over GitHub's limit is worth scanning.
    check_pull_request_body_size(body)
    pattern = CLOSES_TEMPLATE.format(repository=re.escape(repository), number=issue)
    if not re.search(pattern, strip_code(body)):
        raise Rejection(f"pull request body lacks a 'Closes #{issue}' line")
    return title, body.rstrip("\n")


def check_pull_request_body_size(body: str) -> None:
    """Refuse a composed body GitHub would reject, before any write happens."""
    if len(body) > MAX_PR_BODY:
        raise Rejection(
            f"pull request body exceeds {MAX_PR_BODY} characters after provenance"
        )


def provenance_block(
    *,
    model: str,
    run_url: str,
    issue_url: str,
    base_sha: str,
    commands: list[dict[str, Any]],
) -> str:
    """The block appended to every pull request body."""
    lines = [
        "",
        "---",
        "",
        "<details>",
        "<summary>Authored by an AI agent; human review required</summary>",
        "",
        f"- Issue: {issue_url}",
        f"- Model: `{model}` via GitHub Copilot CLI",
        f"- Run: {run_url or 'n/a'}",
        f"- Base: `{base_sha}`",
        "",
    ]
    if commands:
        lines += ["| Command | Exit |", "| --- | --- |"]
        for item in commands:
            # Agent-written text: one line, then escaped, so it stays in
            # its cell and cannot close the details block.
            command = " ".join(str(item["command"]).split())
            command = command.replace("|", "\\|").replace("`", "'").replace("<", "&lt;")
            code = item["exit_code"]
            lines.append(f"| `{command}` | {code if code is not None else '?'} |")
        lines.append("")
    lines += [
        "The `github-code-monkey` workflow opened this pull request.",
        "Nobody has merged it; a maintainer must review it first.",
        "</details>",
        "",
    ]
    return "\n".join(lines)

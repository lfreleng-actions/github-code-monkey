# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Find the parts of a Markdown body GitHub treats as code.

Closing keywords count only outside code, so the pull request check
strips HTML comments, fenced blocks and code spans before it looks
for ``Closes #N``. Every scan here is linear in the input: the body
is agent output and may be hostile.
"""

from __future__ import annotations

import re

# A fence may sit inside list items and block quotes: the container
# markers come first (group 1), then the fence's own indent. Each
# repetition must consume a marker, so the scan stays linear.
FENCE_OPEN_RE = re.compile(r"^((?:[ ]*(?:[-+*]|\d{1,9}[.)]|>))*)([ ]*)(`{3,}|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^([ >]*)(`{3,}|~{3,})[ \t]*$")


HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)


def fence_opener(line: str) -> tuple[str, int, int] | None:
    """The fence run, content column and quote depth a line opens, if any."""
    found = FENCE_OPEN_RE.match(line)
    if not found:
        return None
    container, indent, fence = found.groups()
    if not container and len(indent) > 3:
        return None  # four spaces at top level make indented code
    column = len(container) + len(indent) if container else 0
    return fence, column, container.count(">")


def closes_fence(line: str, fence: str, column: int, quotes: int) -> bool:
    """Whether ``line`` closes the fence ``fence_opener`` described.

    The closer uses the opener's character, is at least as long, has
    the same quote depth, and sits at the content column or up to
    three spaces deeper. Anything else keeps the fence open, which
    hides more text rather than less.
    """
    found = FENCE_CLOSE_RE.match(line)
    if not found:
        return False
    prefix, run = found.groups()
    return (
        run[0] == fence[0]
        and len(run) >= len(fence)
        and prefix.count(">") == quotes
        and column <= len(prefix) <= column + 3
    )


def strip_code(markdown: str) -> str:
    """Drop text where GitHub ignores closing keywords.

    That is HTML comments, fenced blocks (at top level or inside list
    items and quotes) and code spans. An unclosed comment or fence
    hides everything after it, so that tail goes too.
    """
    markdown = HTML_COMMENT_RE.sub("", markdown)
    kept: list[str] = []
    fence: tuple[str, int, int] | None = None
    for line in markdown.split("\n"):
        if fence:
            if closes_fence(line, *fence):
                fence = None
            continue
        fence = fence_opener(line)
        if fence:
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

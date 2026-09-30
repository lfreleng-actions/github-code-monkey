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

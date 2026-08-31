"""GitHub-compatible Markdown heading-to-anchor slugification.

Extracted from `dev/bin/check-doc-links`'s own `anchor_for` (unchanged
behavior — a pure refactor) so a second caller, `dev/bin/generate-help-
html`, can compute the identical anchor for the same heading rather than
reimplementing the same algorithm a second, possibly drifting way —
one slugger, two callers, zero drift (`doc/fim-gui-design.md` §11).
`check-doc-links` still owns the
one place this anchor's correctness against real GitHub rendering is
exercised; every other caller trusts it rather than re-deriving it.
"""

from __future__ import annotations

import re

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
CODE_SPAN_PATTERN = re.compile(r"`([^`]*)`")
INLINE_MARKUP_PATTERN = re.compile(r"[`*_~]")
NON_SLUG_PATTERN = re.compile(r"[^\w\- ]", re.UNICODE)


def anchor_for(heading: str) -> str:
    """Return the GitHub-style anchor for one heading.

    Code-span content (`` `like_this` ``) is markup only at its backtick
    delimiters — GitHub's own slugger does not treat an underscore or
    asterisk *inside* a code span as emphasis syntax, so stripping
    ``INLINE_MARKUP_PATTERN`` from the whole heading in one pass would
    incorrectly delete a literal underscore from an identifier such as
    `` `convergence_statistic` ``. Code spans are unwrapped to their literal
    content instead; only text outside them goes through markup stripping.
    """
    plain = HTML_TAG_PATTERN.sub("", heading)
    segments: list[str] = []
    cursor = 0
    for span in CODE_SPAN_PATTERN.finditer(plain):
        segments.append(INLINE_MARKUP_PATTERN.sub("", plain[cursor : span.start()]))
        segments.append(span.group(1))
        cursor = span.end()
    segments.append(INLINE_MARKUP_PATTERN.sub("", plain[cursor:]))
    plain = "".join(segments).strip().lower()
    plain = NON_SLUG_PATTERN.sub("", plain)
    return re.sub(r"\s+", lambda match: "-" * len(match.group(0)), plain)

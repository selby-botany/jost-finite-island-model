"""The Python guide's code blocks run as written, in order."""

from __future__ import annotations

import re
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[1] / "doc" / "python-api.md"
BLOCK = re.compile(r"```python\n(.*?)\n```", re.S)


def test_every_code_block_in_the_python_guide_runs() -> None:
    """Blocks share one namespace and run top to bottom, like a notebook."""
    blocks = BLOCK.findall(GUIDE.read_text(encoding="utf-8"))
    assert len(blocks) >= 10
    namespace: dict[str, object] = {"__name__": "python_api_guide"}
    for index, block in enumerate(blocks, start=1):
        try:
            exec(compile(block, f"python-api.md block {index}", "exec"), namespace)
        except Exception as error:  # surface which block failed
            raise AssertionError(
                f"block {index} failed:\n{block}\n{error!r}"
            ) from error

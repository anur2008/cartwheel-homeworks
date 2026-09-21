"""Homework 4 review app.

Serves the adapted UI in this directory. Reuses the reference file-backed API
in ``analysis.server`` (student state files, Langfuse score writes). The
reference ``analysis/ui/index.html`` is not the submission.
"""

from __future__ import annotations

import sys
from pathlib import Path

import analysis.server as ref

ref.UI_DIR = Path(__file__).resolve().parent


def main() -> None:
    if "--student" not in sys.argv:
        sys.argv.append("--student")
    ref.main()


if __name__ == "__main__":
    main()

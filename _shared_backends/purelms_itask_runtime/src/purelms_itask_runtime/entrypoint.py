"""Select one-shot or HTTP Service execution for the same backend image."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from purelms_itask_runtime.service import serve


def main(argv: list[str] | None = None) -> int:
    """Run the task script once, or host it when runtime mode is ``service``."""
    arguments = list(argv if argv is not None else sys.argv[1:])
    if len(arguments) != 1:
        print("usage: python -m purelms_itask_runtime.entrypoint BACKEND_SCRIPT")
        return 2
    script = Path(arguments[0]).resolve()
    if os.environ.get("PURELMS_RUNTIME_MODE", "oneshot") == "service":
        serve(script)
        return 0
    os.execv(sys.executable, [sys.executable, str(script)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

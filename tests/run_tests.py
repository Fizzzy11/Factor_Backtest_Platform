from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    try:
        import pytest
    except ImportError:
        print(
            "tests/run_tests.py delegates to pytest and requires pytest. "
            "Install pytest or run in the project venv.",
            file=sys.stderr,
        )
        return 2

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        args = [str(repo_root / "tests"), "-q"]
    return int(pytest.main(args))


if __name__ == "__main__":
    raise SystemExit(main())

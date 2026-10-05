#!/usr/bin/env python3
"""Look up one organisation number in Brreg and print the parsed company as JSON.

Meant for checking the field mapping in src/integrations/brreg.py against the live API, which
was not reachable when the mapping was written. Compare the output with the register entry.
Fields the API did not return show as null. Makes one real request, so it is not part of the
test suite.

Usage, from the repo root:
    python3 scripts/check_brreg.py 912345678

Exit code 0 when the company was found, 1 on any error or when the number is not in the
register, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

# Make "from src..." work when the script is run as scripts/check_brreg.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.automation.errors import AutomationError  # noqa: E402
from src.integrations.brreg import BrregClient  # noqa: E402
from src.integrations.http import UrllibHttpClient  # noqa: E402


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Look up one organisation number in Brreg.")
    parser.add_argument("organization_number", help="9 digits, spaces and an NO prefix are accepted")
    args = parser.parse_args(argv)

    try:
        company = BrregClient(UrllibHttpClient()).lookup(args.organization_number)
    except AutomationError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if company is None:
        print("Not found in Brreg (HTTP 404).", file=sys.stderr)
        return 1

    output = dataclasses.asdict(company)
    output["is_active"] = company.is_active
    print(json.dumps(output, default=_json_default, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

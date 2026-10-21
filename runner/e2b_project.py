"""Fixed entry point for an approved project inside an isolated sandbox.

The caller supplies two JSON files. This script has no source-host or service
credentials; the source snapshot and approved adapter name arrive as data.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Expected snapshot directory, input file and adapter file")
    source_dir = Path(sys.argv[1]).resolve()
    input_path = Path(sys.argv[2]).resolve()
    adapter_path = Path(sys.argv[3]).resolve()
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    input_json = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(input_json, dict):
        raise ValueError("The approved input must be a JSON object")
    sys.path.insert(0, str(source_dir))
    module = importlib.import_module(adapter["module"])
    function = getattr(module, adapter["function"])
    result = function(input_json)
    if not isinstance(result, dict):
        raise ValueError("The approved adapter must return a JSON object")
    encoded = json.dumps({"result": result}, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 131_072:
        raise ValueError("Adapter output exceeds 128 KB")
    print(encoded)


if __name__ == "__main__":
    main()

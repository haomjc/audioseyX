import ast
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / "src/audioseyx/__init__.py").read_text(encoding="utf-8"))
version = next(ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets))
tag = os.environ.get("RELEASE_TAG") or (sys.argv[1] if len(sys.argv) > 1 else "")
if tag != "v" + version:
    raise SystemExit("The release tag must equal v" + version)
print("Release version matches " + tag)

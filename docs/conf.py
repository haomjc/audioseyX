from pathlib import Path
import ast
import os

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / "src/audioseyx/__init__.py").read_text(encoding="utf-8"))
release = next(ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets))
project = "audioseyX"
author = "haomjc"
copyright = "2026, haomjc"
version = release
language = "en"
extensions = ["myst_parser", "sphinx.ext.mathjax"]
myst_enable_extensions = ["colon_fence", "dollarmath", "deflist"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "research/**"]
html_theme = "furo"
html_title = "audioseyX documentation"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
html_theme_options = {
    "light_css_variables": {"color-brand-primary": "#007f80", "color-brand-content": "#006d70"},
    "dark_css_variables": {"color-brand-primary": "#67cbbb", "color-brand-content": "#80d8ca"},
}
html_show_sourcelink = False
nitpicky = True

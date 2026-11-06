"""Check a Sphinx Pages artifact without running an audio engine or making requests."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

FORBIDDEN_PARTS = {
    ".git", ".github", ".doctrees", "research", "UserData", ".private", ".native-profiles",
}
FORBIDDEN_NAMES = {"native_tables.json", "channel_addresses.json", ".env", ".pypirc"}
FORBIDDEN_SUFFIXES = (
    ".exe", ".ady", ".oca", ".awb", ".awbcal.json", ".npz", ".npy", ".log", ".pickle", ".doctree",
)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.canonical = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")


def check_site(directory, base_url):
    root = Path(directory).resolve()
    base_url = base_url.rstrip("/") + "/"
    base = urlsplit(base_url)
    if base.scheme != "https" or not base.hostname:
        raise ValueError("Provide the HTTPS documentation base URL")
    required = ["index.html", "search.html", "searchindex.js", ".nojekyll", "_static/custom.css"]
    for name in required:
        if not (root / name).is_file():
            raise ValueError("Missing site asset: " + name)
    if (root / "CNAME").exists() and base.hostname.endswith(".github.io"):
        raise ValueError("A github.io project site must not publish a custom-domain CNAME")
    files = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if (
            path.is_symlink()
            or set(relative.parts) & FORBIDDEN_PARTS
            or path.name in FORBIDDEN_NAMES
            or path.name.lower().endswith(FORBIDDEN_SUFFIXES)
        ):
            raise ValueError("Unexpected private, generated or linked site path: " + str(relative))
        if path.is_file():
            files.append(path)
    errors = []
    checked = 0
    pages = 0
    for page in files:
        if page.suffix != ".html":
            continue
        pages += 1
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        expected = base_url + page.relative_to(root).as_posix()
        if parser.canonical != expected:
            errors.append(f"{page.name}: incorrect canonical URL {parser.canonical!r}")
        for value in parser.links:
            link = urlsplit(value)
            if link.scheme in ("https", "http", "mailto", "data") or link.netloc:
                continue
            if link.scheme:
                errors.append(f"{page.name}: unexpected link scheme in {value}")
                continue
            if not link.path:
                continue
            name = unquote(link.path)
            if name.startswith("/"):
                if not name.startswith(base.path):
                    errors.append(f"{page.name}: link leaves project path: {value}")
                    continue
                target = (root / name[len(base.path):]).resolve()
            else:
                target = (page.parent / name).resolve()
            if not target.is_relative_to(root):
                errors.append(f"{page.name}: link leaves site: {value}")
                continue
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                errors.append(f"{page.name}: missing local target: {value}")
            checked += 1
    if errors:
        raise ValueError("\n".join(errors[:40]))
    return dict(
        passed=True,
        html_pages=pages,
        local_links_and_assets_checked=checked,
        files=len(files),
        base_url=base_url,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    print(json.dumps(check_site(args.directory, args.base_url), indent=2))

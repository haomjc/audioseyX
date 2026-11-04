"""Small maintenance CLI for the official Read the Docs Community API v3.

Set READTHEDOCS_TOKEN in the local environment for authenticated operations.
The token is never accepted as a command-line argument or written to a file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an authorization header to a redirect destination.
        return None


def api(method, path, body=None):
    token = os.environ.get("READTHEDOCS_TOKEN", "").strip()
    if any(ord(char)<33 or ord(char)>126 for char in token):
        raise ValueError("READTHEDOCS_TOKEN must be one token without whitespace or control characters")
    if method != "GET" and not token:
        raise ValueError("Set READTHEDOCS_TOKEN locally before modifying Read the Docs. Do not paste tokens into chat or commit them.")
    headers = {"Accept": "application/json", "User-Agent": "audioseyX-maintenance"}
    if token:
        headers["Authorization"] = "Token " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request("https://app.readthedocs.org/api/v3/" + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Read the Docs response exceeds the client limit")
            return json.loads(raw) if raw else {"status": response.status}
    except HTTPError as error:
        raise ValueError(f"Read the Docs returned HTTP {error.code}; check the project slug, token permissions and API settings.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "import", "build", "versions", "sync", "activate"])
    parser.add_argument("--project", default="audioseyx")
    parser.add_argument("--version", default="latest")
    parser.add_argument("--repository", default="https://github.com/haomjc/audioseyX")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        parser.error("Use the Read the Docs project slug, containing lowercase letters, numbers, hyphens or underscores")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.version):
        parser.error("Invalid version slug")
    root = "projects/" + args.project + "/"
    try:
        if args.command == "status":
            project = api("GET", root)
            builds = api("GET", root + "builds/?limit=1")
            last = next(iter(builds.get("results", [])), {})
            result = {"project": {key: project.get(key) for key in ["name", "slug", "default_version", "repository", "urls"]},
                      "latest_build": {key: last.get(key) for key in ["id", "version", "state", "success", "commit", "error"]}}
        elif args.command == "import":
            if not args.repository.startswith("https://github.com/"):
                parser.error("This project's maintenance client expects a public HTTPS GitHub repository")
            result = api("POST", "projects/", {"name": "audioseyX", "repository": {"url": args.repository, "type": "git"},
                          "language": "en", "programming_language": "py", "readthedocs_yaml_path": ".readthedocs.yaml"})
        elif args.command == "build":
            result = api("POST", root + "versions/" + args.version + "/builds/")
        elif args.command == "versions":
            result = api("GET", root + "versions/?limit=100")
        elif args.command == "sync":
            result = api("POST", root + "sync-versions/")
        else:
            result = api("PATCH", root + "versions/" + args.version + "/", {"active": True})
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()

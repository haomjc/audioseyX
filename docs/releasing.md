# Publishing and maintenance

## Publication destinations

| Component | Destination | Setup required |
|---|---|---|
| Source, issues and tags | [haomjc/audioseyX](https://github.com/haomjc/audioseyX) on GitHub | Public repository exists; CI checks subsequent source updates |
| Python package | `audioseyX` on PyPI | Existing PyPI account; verify name availability and configure a Trusted Publisher |
| Main documentation | [GitHub Pages](https://haomjc.github.io/audioseyX/) | GitHub Actions builds the English Sphinx site from `main` |
| Optional versioned documentation | Read the Docs | Import the repository and build `.readthedocs.yaml`; online deployment must be verified separately |
| Windows executable | GitHub Actions artifact, then a reviewed GitHub Release | Run the Windows workflow against a version tag |

The source and main documentation are public on GitHub. PyPI publishing and the optional Read the Docs deployment require separate service configuration; their local configuration is not a live-publication claim. Package-index names are not reserved by a local configuration or a pending publisher.

## First GitHub push

The public tree is separate from the local research prototype. Only reviewed source, documentation, public synthetic fixtures and notices belong in the repository. Do not add private compatibility tables, extracted binaries, personal measurement files or credentials.

The public repository already exists at `haomjc/audioseyX`. Use it as `origin` and push reviewed changes to `main`. The CI workflow checks the public tests, package contents and documentation. Existing cached CLI credentials may need to be refreshed through the normal GitHub login flow.

## GitHub Pages documentation

The main site is **<https://haomjc.github.io/audioseyX/>**. It uses the English Markdown sources in `docs/`, Sphinx and the Furo theme, including navigation, client-side search and light/dark themes. Documentation builds do not import the audio engine or require numerical dependencies or receiver access.

The repository's Pages publishing source is **GitHub Actions**. `.github/workflows/docs.yml`, named **Publish documentation**, runs when documentation, included project guides, the version source or the workflow changes on `main`. It can also be run manually from the Actions tab. Ordinary pull requests are checked by CI and do not deploy the public site.

The build installs `docs/requirements.txt`, treats Sphinx warnings as errors, and checks the generated site's local links, canonical URLs and file tree. It uploads only the HTML output, with `.nojekyll`; build caches and research notes are excluded. The separate deployment job publishes through the `github-pages` environment with short-lived GitHub Actions credentials. No publishing token is stored in the repository.

To preview the documentation locally:

```console
python -m pip install -r docs/requirements.txt
python -m sphinx -W --keep-going -b html -d docs/_build/doctrees docs docs/_build/html
python -m http.server 8000 --directory docs/_build/html
```

Open `http://localhost:8000/`. For a Pages-equivalent build, set `AUDIOSEYX_DOCS_BASE_URL` to `https://haomjc.github.io/audioseyX/` before building, then run:

```console
python tools/check_docs_site.py docs/_build/html --base-url https://haomjc.github.io/audioseyX/
```

In Actions, inspect both **build** and **deploy** for the latest documentation commit. A successful local build alone does not establish that the public site was updated. The current Pages site tracks `main`; release tags remain available in GitHub, but this site does not offer a version selector.

Official instructions: [custom workflows for GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## PyPI Trusted Publishing

Use PyPI's GitHub Actions Trusted Publisher flow. This avoids storing a long-lived PyPI API token in the repository.

1. In the existing PyPI account, open Publishing and add a pending GitHub publisher for a new project, or a publisher on an existing project you own.
2. Use project name `audioseyX`, GitHub owner `haomjc`, repository `audioseyX`, workflow `publish.yml` and environment `pypi`.
3. Configure a separate publisher on TestPyPI with environment `testpypi` if you want a staging upload. TestPyPI is a separate account/service.
4. Update the version in `src/audioseyx/__init__.py`, update the changelog, commit and create the matching `vVERSION` tag.
5. Run the **Publish Python package** workflow for that tag. Select the destination explicitly. Tests, version checks, distribution audit and metadata checks precede upload.

No publisher has been configured merely by adding workflow files. Only the account owner can complete the account authorization and verify package ownership. Do not paste a password, recovery code or API token into an issue or chat.

Official instructions: [new project with a Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) and [publisher configuration](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Read the Docs

Read the Docs remains an optional destination for versioned builds of the same documentation. With an authorized account, import `haomjc/audioseyX` and verify a successful public build before advertising its URL.

The repository includes `.readthedocs.yaml`, `docs/conf.py` and a pinned documentation requirements file. Documentation builds do not import the audio engine or require native numerical resources. The default language is English. The same Markdown sources can later support translations.

Enable the default branch for the development documentation. Activate release tags as versions when ready. Do not advertise a stable documentation version before a stable software release exists. After the optional site is live, add its verified URL alongside the main GitHub Pages link.

Official instructions: [add a project](https://docs.readthedocs.com/platform/stable/intro/add-project.html), [configuration](https://docs.readthedocs.com/platform/stable/config-file/v2.html), and [versions](https://docs.readthedocs.com/platform/stable/versions.html).

### API and command-line maintenance

The official [API v3](https://docs.readthedocs.com/platform/stable/api/v3.html) supports importing projects, changing project settings, synchronizing/activating versions, triggering builds and reading build results. Browser login does not authenticate a separate command-line client. Get an API token from the account's profile settings and configure it locally as `READTHEDOCS_TOKEN`; do not put it in source, shell command arguments, issues or chat.

This repository includes a small independent maintenance client, not an official Read the Docs CLI:

```console
python tools/readthedocs.py status
python tools/readthedocs.py import
python tools/readthedocs.py versions
python tools/readthedocs.py build --version latest
python tools/readthedocs.py sync
python tools/readthedocs.py activate --version v0.5.0a1
```

Use the exact version slug returned by `versions`. Import creates an English project using the public GitHub repository and `.readthedocs.yaml`. Import is a one-time operation; do not repeat it to update an existing project. The client takes its token only from the environment and rejects redirects rather than forwarding authorization headers. A 404 can indicate a nonexistent, differently named or inaccessible project.

After the initial GitHub integration is configured, pushes and tags can trigger builds through the repository webhook. API commands are useful for setup, inspection and requested rebuilds; normal documentation changes remain reviewed source changes in Git.

## Maintenance

- CI runs on changes and pull requests; failures should be resolved before release.
- Dependabot proposes dependency and workflow updates; review and test them before merging.
- Use issue templates for bugs, feature requests and hardware evidence.
- Maintain English primary documentation and a changelog for each release.
- Keep hardware claims tied to the exact tested model, firmware and measurement path.
- Publishing, issue review and support still require a maintainer; these files do not create an unattended monitoring service.

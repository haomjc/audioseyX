# Publishing and maintenance

## Planned destinations

| Component | Destination | Setup required |
|---|---|---|
| Source, issues and tags | [haomjc/audioseyX](https://github.com/haomjc/audioseyX) on GitHub | Public repository exists; CI checks subsequent source updates |
| Python package | `audioseyX` on PyPI | Existing PyPI account; verify name availability and configure a Trusted Publisher |
| Main documentation | Read the Docs | Create/connect the account, import the GitHub repository and build `.readthedocs.yaml` |
| Windows executable | GitHub Actions artifact, then a reviewed GitHub Release | Run the Windows workflow against a version tag |

GitHub is public. The PyPI and Read the Docs destinations still require account setup; their local configuration is not a live-publication claim. Package-index names are not reserved by a local configuration or a pending publisher.

## First GitHub push

The public tree is separate from the local research prototype. Only reviewed source, documentation, public synthetic fixtures and notices belong in the repository. Do not add private compatibility tables, extracted binaries, personal measurement files or credentials.

Create `haomjc/audioseyX`, add the repository as `origin`, and push `main`. The CI workflow checks the public tests, package contents and documentation. Existing cached CLI credentials may need to be refreshed through the normal GitHub login flow.

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

Read the Docs is the main documentation target. The account owner must complete signup and any terms or authorization prompts, then import `haomjc/audioseyX`.

The repository includes `.readthedocs.yaml`, `docs/conf.py` and a pinned documentation requirements file. Documentation builds do not import the audio engine or require native numerical resources. The default language is English. The same Markdown sources can later support translations.

Enable the default branch for the development documentation. Activate release tags as versions when ready. Do not advertise a stable documentation version before a stable software release exists. After the site is live, add its real URL to the project metadata and README.

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

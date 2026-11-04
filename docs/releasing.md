# Publishing and maintenance

## Planned destinations

| Component | Destination | Setup required |
|---|---|---|
| Source, issues and tags | `haomjc/audioseyX` on GitHub | Create the repository and push the reviewed source |
| Python package | `audioseyX` on PyPI | Existing PyPI account; verify name availability and configure a Trusted Publisher |
| Main documentation | Read the Docs | Create/connect the account, import the GitHub repository and build `.readthedocs.yaml` |
| Windows executable | GitHub Actions artifact, then a reviewed GitHub Release | Run the Windows workflow against a version tag |

These are intended destinations, not claims of live publication. Package-index names are not reserved by a local configuration or a pending publisher.

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

## Maintenance

- CI runs on changes and pull requests; failures should be resolved before release.
- Dependabot proposes dependency and workflow updates; review and test them before merging.
- Use issue templates for bugs, feature requests and hardware evidence.
- Maintain English primary documentation and a changelog for each release.
- Keep hardware claims tied to the exact tested model, firmware and measurement path.
- Publishing, issue review and support still require a maintainer; these files do not create an unattended monitoring service.

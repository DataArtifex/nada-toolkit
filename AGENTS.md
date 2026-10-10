# AI Agent Instructions

Welcome, fellow AI. This file provides context and instructions for working on this repository effectively.

- **Mission**: Provide a high-level, type-safe Python toolkit for programmatic interaction with IHSN [NADA](https://nada.ihsn.org/) (National Data Archive) repositories.
- **Primary Focus**: Discovery, catalog browsing, and bulk metadata harvesting of microdata and survey documentation. Currently focuses on searching, information gathering, and resilient harvesting rather than content management (CRUD operations on catalogs).
- **Core Capabilities**:
    - **Global Discovery**: Fetching and interacting with the worldwide network of NADA installations.
    - **Advanced Search**: Wrapper for the NADA Catalog API supporting filtering, keywords, collections, and country queries.
    - **Study Metadata & Exports**: Retrieving DDI 2.5 CodeBook XML (`ddi`), Study Metadata JSON (`json`), and external documentation resources (`resources`).
    - **Resilient Metadata Harvester**: Incremental synchronization with SHA-256 verification, manifest tracking, 24-hour catalog and statistics caching, and comprehensive error classification.
    - **Robust Interoperability**: Type-safe Pydantic models for request parameters and metadata structures.
    - **Performance Optimizations**: Native support for request caching via `requests-cache`.
- **Target Audience**: Researchers, data scientists, statistical agencies, and developers building tools on top of NADA catalogs.
- **Status**: Early development (Beta). APIs are functional but subject to refinement.

## Project Stack

- **Language**: Python 3.12+ (Strictly required)
- **Dependency Management & Workflow**: [uv](https://github.com/astral-sh/uv) (Recommended) and [Hatch](https://hatch.pypa.io/).
- **Linting & Formatting**: [Ruff](https://docs.astral.sh/ruff/) (extremely fast linter/formatter).
- **Type Checking**: [Pyrefly](https://github.com/astral-sh/ruff) / static type checker.
- **Git Hooks**: [pre-commit](https://pre-commit.com/) (ensures code quality before commits).
- **Testing**: [pytest](https://docs.pytest.org/) with [coverage](https://coverage.readthedocs.io/).
- **Documentation**: [Sphinx](https://www.sphinx-doc.org/) with [MyST-Parser](https://myst-parser.readthedocs.io/) (Markdown support) and [Read the Docs theme](https://sphinx-rtd-theme.readthedocs.io/).
- **Version Control**: Git.

## Bootstrapping a New Project

> **Note for agents**: This section applies only when using this repo as a project template to start a new project. If you are working on the existing `dartfx-nada` codebase, skip this section.

To rename the project and package from the template defaults:
1. Run `./rename.sh "new-project-name" "new_package_name"`
2. Run `uv sync` to refresh the environment.
3. **DeepWiki**: Register the new project at [DeepWiki.com](https://deepwiki.com/) to enable AI-optimized documentation indexing.

## Environment Management

This project uses `hatch` for environment management, but `uv` is preferred for speed.

- To run tests: `uv run pytest` or `hatch run test`
- To check types: `uv run pyrefly check src tests` or `hatch run types:check`
- To build docs: `uv run sphinx-build -b html docs/source docs/build/html` or `hatch run docs:build`

## Coding Standards

- Follow PEP 8.
- Use type hints for all public APIs.
- Docstrings should be in Google style or NumPy style (Sphinx compatible).
- Prefer `pathlib` over `os.path`.
- Prefer Pydantic for modeling over Python dataclasses or other similar packages.
- Prefer Polars package for data management over Pandas or other similar packages.
- Strictly follow the project's Ruff configuration. Run `uv run ruff check .` and `uv run ruff format .` to ensure compliance before submitting changes.
- Type checking is performed with `pyrefly`. Run `uv run pyrefly check src tests` and resolve all errors before submitting.

## Testing Policy

- All new features must be accompanied by tests.
- Maintain or improve test coverage.
- Use `pytest` fixtures for setup/teardown.
- Tests are located in the `tests/` directory.

## Documentation Policy

- Documentation is located in the `docs/source` directory.
- Main documentation is in `.rst` or `.md` (via MyST).
- Keep `README.md` up to date with core installation and usage instructions.
- Keep a dedicated `IMPLEMENTATION.md` document up to date that describes the package/code technical implementation.
- Maintain `CHANGELOG.md` with every significant change, ensuring the latest version is always at the top using the version number as heading (e.g., `## [0.2.0]`). Use short, concise bullet points.

## Version Management

- This project uses **dynamic versioning** via Hatch.
- The source of truth for the version is located in: `src/dartfx/nada/__about__.py`.
- To bump versions, modify that file manually or use `hatch version <segment>` (e.g., `hatch version minor`).
- Follow [Semantic Versioning (SemVer)](https://semver.org/).

## Secret Management

- **Local Development**: Use a `.env` file in the project root for local environment variables and secrets.
- **Loading**: Secrets are automatically loaded in tests via `tests/conftest.py` using `python-dotenv`.
- **Git Hygiene**: Never commit `.env` files. Ensure they are covered by `.gitignore`.
- **CI/CD**: Add secrets to GitHub Repository Secrets for use in GitHub Actions. Reference them in workflows as `${{ secrets.SECRET_NAME }}`.

## GitHub Actions CI/CD

- **CI**: Located in `.github/workflows/test.yml`. Runs tests and linting on push/PR to `main` across Ubuntu, macOS, and Windows.
- **Docs**: Located in `.github/workflows/sphinx.yaml`. Builds and deploys documentation to GitHub Pages on push to `main`.
- All workflows use `astral-sh/setup-uv` for fast execution and caching.

## Working with this Repo

1. **Analysis**: Always start by reviewing `pyproject.toml` and `src/` structure.
2. **Context**: Check `KIs/` (Knowledge Items) directory if available for domain logic.
3. **Execution**: Use `uv` or `hatch` for running scripts and tests.
4. **Validation**: Always run `uv run ruff check .`, `uv run ruff format .`, `uv run pyrefly check src tests`, and `uv run pytest` before finalizing changes.

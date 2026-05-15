# This is AI generated code
"""Consumer-facing overrides loaded from ``pyproject.toml``.

The delivered ``shared/tests/test_*.py`` files (which vendor into
the consumer at ``_repo_shared/tests/test_*.py`` and run via
pytest's ``testpaths`` entry) import accessors from this module to
read their knobs at test-load time. Consumers configure those knobs
by adding ``[tool.repo-shared.<section>]`` blocks to their own
``pyproject.toml`` -- the delivered test files stay canonical and
any future repo-shared update to the test flows through on the
next ``upgrade``.

Loaders walk up from the configured ``repo_root`` (default
``Path.cwd()``) to find the nearest ``pyproject.toml``, then read
the relevant section. Missing sections, malformed values, or a
missing ``pyproject.toml`` all return an overrides instance with
the documented defaults so a fresh consumer with no
``[tool.repo-shared.*]`` blocks behaves as documented.

This module is pure: no pytest import, no shared-content reads. It
sits under the CLI's import chain so ``repo-shared --help`` stays
cheap; only consumer test files (which already import pytest)
should call into the accessors.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOL_SECTION = "repo-shared"


def _find_pyproject(start: Path) -> Path | None:
    """Walk up from ``start`` to the first ``pyproject.toml``."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate / "pyproject.toml"
    return None


def _load_section(name: str, repo_root: Path | None = None) -> dict[str, Any]:
    """Return ``[tool.repo-shared.<name>]`` or an empty dict."""
    root = (repo_root or Path.cwd()).resolve()
    pyproject = _find_pyproject(root)
    if pyproject is None:
        return {}
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    raw = data.get("tool", {}).get(_TOOL_SECTION, {}).get(name, {})
    if not isinstance(raw, dict):
        return {}
    return raw


def _as_str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    return None


@dataclass(frozen=True)
class CodeQualityOverrides:
    """Effective values for the delivered ``test_code_quality.py``.

    The delivered test runs ruff lint + format + ``mypy --strict``
    per discovered ``.py`` file. These fields carry the optional
    consumer overrides, with defaults that keep auto-discovery as
    the source of truth.

    ``additional_targets`` is *additive* to discovery -- typically a
    list of extension-less shebang scripts that ``rglob("*.py")``
    cannot find on its own (``bin/foo``, etc.). It does not restrict
    the discovered set.

    ``extra_exclude_dirs`` is appended to the discovery
    exclude-prefix list (which already covers ``_repo_shared/``,
    ``.venv/``, ``__pycache__/``, etc.), so a consumer adds without
    replacing -- the shared baseline keeps applying after a
    repo-shared upgrade that grows the default list.

    ``mypy_extra_deps`` and ``mypy_python_version`` are project-wide
    defaults applied to any file *without* a PEP 723 ``# /// script``
    block. A file with a PEP 723 block uses its own values.
    """

    additional_targets: list[str] = field(default_factory=list)
    extra_exclude_dirs: tuple[str, ...] = ()
    mypy_extra_deps: list[str] = field(default_factory=list)
    mypy_python_version: str | None = None


def code_quality_overrides(
    repo_root: Path | None = None,
) -> CodeQualityOverrides:
    """Read ``[tool.repo-shared.code-quality]`` from ``pyproject.toml``.

    Recognised keys (all optional):

    - ``python-targets`` (list of str, default ``[]``) -- *additive*
      to the auto-discovered ``.py`` set. List explicit file paths
      here for non-``.py`` shebang scripts you want lint + type
      checked (e.g. ``bin/foo``, ``bin/bar``). Directories nominate
      every ``.py`` they contain, but discovery already finds those,
      so the typical use is enumerating extension-less files.
    - ``extra-exclude-dirs`` (list of str, default ``[]``) --
      appended to the discovery exclude list (which already covers
      ``_repo_shared/``, ``.venv/``, ``__pycache__/``, etc.). Use to
      exclude per-repo directories that should not be lint /
      type-checked (vendored third-party Python, generated code,
      etc.).
    - ``mypy-extra-deps`` (list of str, default ``[]``) -- project-
      wide fallback installed via ``uvx --with`` for files *without*
      their own PEP 723 ``# /// script`` block.
    - ``mypy-python-version`` (str, default unset) -- project-wide
      fallback ``--python-version`` for files without PEP 723. Files
      with PEP 723 use their own ``requires-python``.
    """
    s = _load_section("code-quality", repo_root=repo_root)
    additional = _as_str_list(s.get("python-targets")) or []
    extras = _as_str_list(s.get("extra-exclude-dirs")) or []
    deps = _as_str_list(s.get("mypy-extra-deps")) or []
    pyver_raw = s.get("mypy-python-version")
    pyver = pyver_raw if isinstance(pyver_raw, str) and pyver_raw else None
    return CodeQualityOverrides(
        additional_targets=additional,
        extra_exclude_dirs=tuple(extras),
        mypy_extra_deps=deps,
        mypy_python_version=pyver,
    )


@dataclass(frozen=True)
class MarkdownOverrides:
    """Effective values for ``MdformatCheckBase`` subclass attrs.

    ``extra_exclude_dirs`` is appended to the base class' default
    set -- a consumer adds, never replaces, so the shared baseline
    (``_repo_shared/``, ``node_modules/`` etc.) keeps applying after
    a repo-shared upgrade that grows the default list.
    """

    wrap: int = 79
    extra_exclude_dirs: tuple[str, ...] = ()


def markdown_overrides(
    repo_root: Path | None = None,
) -> MarkdownOverrides:
    """Read ``[tool.repo-shared.markdown]`` from ``pyproject.toml``.

    Recognised keys (all optional):

    - ``wrap`` (int, default 79) -- ``mdformat --wrap`` value.
    - ``extra-exclude-dirs`` (list of str, default ``[]``) --
      appended to the base class' default exclude-dirs set, so the
      consumer's additions stack on top of the shared baseline rather
      than replacing it.
    """
    s = _load_section("markdown", repo_root=repo_root)
    wrap_raw = s.get("wrap", 79)
    wrap = wrap_raw if isinstance(wrap_raw, int) else 79
    extras = _as_str_list(s.get("extra-exclude-dirs")) or []
    return MarkdownOverrides(
        wrap=wrap,
        extra_exclude_dirs=tuple(extras),
    )

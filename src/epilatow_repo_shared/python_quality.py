# This is AI generated code
"""Internals consumed by the delivered ``test_code_quality.py``.

Consumers don't import these directly -- the delivered test (a
canonical-path symlink into the consumer's vendored
``_repo_shared/``) does. The shape of the test is:

- ``discover_python_files`` returns every tracked ``.py`` under
  the consumer repo, plus a tracked-but-skip post-filter for
  ``_repo_shared/`` (whose content is also reachable via in-tree
  symlinks).
- ``resolve_files`` walks the discovered list, reads each file's
  PEP 723 ``# /// script`` block, and returns a ``ResolvedFile`` per
  file with ``deps`` / ``python_version``. Files without a block
  fall back to consumer-configured project-wide defaults
  (``mypy-extra-deps`` / ``mypy-python-version`` in pyproject).
- ``run_ruff_lint`` / ``run_ruff_format_check`` / ``run_mypy_strict``
  shell out per-file so per-file deps actually apply.

The consumer-facing customisation surface is two pyproject knobs and
PEP 723 in the file -- never a Python import:

- ``[tool.repo-shared.code-quality] python-targets`` is *additive*
  to discovery, used for extension-less shebang scripts that
  ``rglob("*.py")`` cannot find.
- A ``# /// script`` block in a file declares its mypy deps + the
  ``requires-python`` minimum.
"""

from __future__ import annotations

import dataclasses
import io
import os
import re
import sys
import tokenize
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from epilatow_repo_shared import sp

# Repo-shared's own vendored scaffolding gets the same shared
# content reachable at two paths (the canonical-path symlink and
# the vendored copy under ``_repo_shared/``); a default ``["."]``
# walk would lint both and trip mypy's duplicate-module detection.
# Always exclude the vendor dir from every helper invocation so the
# default works on a fresh consumer that hasn't customised targets.
_VENDOR_EXCLUDE_RE = r"^_repo_shared/"

DEFAULT_RUFF_LINE_LENGTH = 79


def _consumer_has_ruff_line_length(repo_root: Path) -> bool:
    """True if the consumer pins a ``line-length`` ruff would read.

    Mirrors ruff's actual config-file precedence: ``ruff.toml`` wins
    over ``[tool.ruff]`` in ``pyproject.toml`` (when both exist,
    ruff uses ``ruff.toml`` entirely). So:

    - ``ruff.toml`` exists -> only its ``line-length`` matters.
    - No ``ruff.toml`` -> ``pyproject.toml``'s ``[tool.ruff]
      line-length`` matters.

    The delivered ruff invocation passes ``--line-length`` ONLY when
    this returns ``False`` -- a consumer's explicit setting always
    wins, ruff's built-in default of 88 never silently takes effect.
    """
    ruff_toml = repo_root / "ruff.toml"
    if ruff_toml.is_file():
        try:
            doc = tomllib.loads(ruff_toml.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return False
        return "line-length" in doc
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        doc = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return False
    tool_ruff = doc.get("tool", {}).get("ruff", {})
    return isinstance(tool_ruff, dict) and "line-length" in tool_ruff


def _ruff_line_length_args(repo_root: Path) -> list[str]:
    """``["--line-length", "<N>"]`` when the consumer hasn't pinned one."""
    if _consumer_has_ruff_line_length(repo_root):
        return []
    return ["--line-length", str(DEFAULT_RUFF_LINE_LENGTH)]


def run_ruff_lint(targets: Sequence[str], *, cwd: Path) -> None:
    """Run ``ruff check`` on ``targets``; raise on failure.

    Defaults ``--line-length`` to ``DEFAULT_RUFF_LINE_LENGTH`` (79,
    matching the prose default) when the consumer hasn't pinned one
    in ``ruff.toml`` or ``[tool.ruff]``. A consumer's explicit value
    always wins -- the flag is suppressed in that case.
    """
    if not targets:
        pytest.skip("no ruff targets")
    result = sp.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            f"--exclude={_VENDOR_EXCLUDE_RE}",
            *_ruff_line_length_args(cwd),
            *targets,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise AssertionError(
            "ruff lint failed. Run `uvx ruff check --fix .` to "
            f"auto-fix.\n\n{result.stdout}{result.stderr}"
        )


def run_ruff_format_check(targets: Sequence[str], *, cwd: Path) -> None:
    """Run ``ruff format --check`` on ``targets``; raise on failure.

    Same ``--line-length`` defaulting as ``run_ruff_lint``: a
    consumer's pinned value wins; otherwise 79 is passed so the
    Python wrap matches the prose wrap.
    """
    if not targets:
        pytest.skip("no ruff targets")
    result = sp.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            f"--exclude={_VENDOR_EXCLUDE_RE}",
            *_ruff_line_length_args(cwd),
            *targets,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise AssertionError(
            "ruff format failed. Run `uvx ruff format .` to "
            f"auto-fix.\n\n{result.stdout}{result.stderr}"
        )


_MYPY_INTERNAL_ERROR_MARKER = "error: INTERNAL ERROR --"
_MYPY_MAX_ATTEMPTS = 3


def run_mypy_strict(
    targets: Sequence[str],
    *,
    cwd: Path,
    extra_deps: Sequence[str] = (),
    python_version: str | None = None,
) -> None:
    """Run ``mypy --strict`` per target; raise on failure.

    With ``extra_deps`` empty, mypy runs from the current project env
    (``sys.executable -m mypy``). With ``extra_deps`` non-empty, each
    target is checked via ``uvx --with <dep>... mypy --strict
    <target>`` -- a fresh isolated env per call, with the listed
    deps installed alongside mypy. This is how HA-coupled targets
    type-check against real ``homeassistant.*`` types without the
    consumer carrying HA in its project ``dependencies``.

    ``python_version`` overrides ``[tool.mypy] python_version`` from
    the project's ``pyproject.toml`` for this group only. Useful when
    a group's ``extra_deps`` install a package whose source uses
    syntax newer than the project's declared floor (e.g. HA needs
    3.12+ type-parameter-list syntax, but the consumer's project
    target is 3.11 for its own code).

    Iteration is per-target either way: bare-Python scripts without
    ``__init__.py`` all register as ``__main__`` to mypy, so passing
    several together would fail with "Duplicate module named
    __main__". Per-target spawn sidesteps this; the cost is small.

    Every invocation passes ``--exclude '_repo_shared/'`` so the
    vendored scaffolding (which mirrors content reachable at the
    canonical-path symlinks) doesn't trip mypy's duplicate-module
    detection on the default ``["."]`` walk.

    Every invocation also passes ``--show-traceback`` so a crash
    leaves a Python traceback in the output for upstream filing
    instead of just the bare ``INTERNAL ERROR -- ... version: X.Y.Z``
    line, and crashes that surface mypy's
    ``error: INTERNAL ERROR --`` banner are retried up to
    ``_MYPY_MAX_ATTEMPTS`` total attempts before being surfaced as a
    failure. The mypyc-compiled wheel occasionally crashes on
    otherwise-clean files (an upstream bug, not a real type error --
    a standalone re-run of the same command typechecks cleanly), and
    a one-off crash should not break the consumer's test run.
    """
    if not targets:
        pytest.skip("no mypy targets")
    failures: list[str] = []
    for target in targets:
        cmd: list[str]
        if extra_deps:
            cmd = ["uvx"]
            if python_version:
                # Pin the temporary env's Python so mypy's parser
                # supports any newer syntax used by ``extra_deps``
                # source (e.g. HA installs that ship 3.14 syntax).
                cmd.extend(["--python", python_version])
            for dep in extra_deps:
                cmd.extend(["--with", dep])
            cmd.append("mypy")
        else:
            cmd = [sys.executable, "-m", "mypy"]
        cmd.extend(
            ["--strict", "--show-traceback", "--exclude", _VENDOR_EXCLUDE_RE]
        )
        if python_version:
            # Set mypy's type-analysis target so semantics match
            # the env's interpreter and follow-imports parsing
            # accepts the same syntax the parser does.
            cmd.extend(["--python-version", python_version])
        cmd.append(target)
        for attempt in range(1, _MYPY_MAX_ATTEMPTS + 1):
            result = sp.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=sp.LONG_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                break
            crashed = _MYPY_INTERNAL_ERROR_MARKER in (
                result.stdout + result.stderr
            )
            if crashed and attempt < _MYPY_MAX_ATTEMPTS:
                continue
            prefix = (
                f"--- {target} "
                f"(mypy INTERNAL ERROR after {attempt} attempts) ---\n"
                if crashed
                else f"--- {target} ---\n"
            )
            failures.append(f"{prefix}{result.stdout}{result.stderr}")
            break
    if failures:
        raise AssertionError("mypy strict failed.\n\n" + "\n".join(failures))


# ``_repo_shared`` is tracked, but the canonical-path symlinks
# expose its content at a second path, so linting both would trip
# mypy's duplicate-module detection.
DEFAULT_IGNORED_DIRS: tuple[str, ...] = ("_repo_shared",)


def _normalize_exclude_dirs(exclude_dirs: Sequence[str]) -> frozenset[str]:
    """Canonicalize the user-facing ``exclude_dirs`` shape.

    Used by both the python-discovery and markdown-discovery walks so
    consumers see the same accept / reject rules for the
    ``extra-exclude-dirs`` pyproject keys under
    ``[tool.repo-shared.code-quality]`` and
    ``[tool.repo-shared.markdown]``.

    Tolerated input shapes, normalised silently:

    - Trailing ``/`` (e.g. ``"htmlcov/"``) -- stripped.
    - Leading ``**/`` glob (e.g. ``"**/tmp/"``) -- stripped; entries
      are directory names pruned anywhere in the tree by default.

    Rejected with ``ValueError``:

    - Multi-segment entries (e.g. ``"docs/_build"``). Entries are
      bare directory names; a path prefix would imply a from-root
      anchor that this exclude semantic does not support.
    """
    result: set[str] = set()
    for entry in exclude_dirs:
        cleaned = entry
        if cleaned.startswith("**/"):
            cleaned = cleaned[3:]
        cleaned = cleaned.rstrip("/")
        if not cleaned:
            continue
        if "/" in cleaned:
            raise ValueError(
                f"exclude-dirs entry {entry!r} contains '/': "
                "entries are directory names pruned anywhere in the "
                "tree, not path prefixes. Use the bare directory name "
                "(e.g. `_build` instead of `docs/_build`)."
            )
        result.add(cleaned)
    return frozenset(result)


def _git_tracked_files(repo_root: Path, suffix: str) -> list[str] | None:
    """Tracked + untracked-not-ignored ``*<suffix>`` files.

    Returns repo-relative POSIX paths or ``None`` when ``repo_root``
    is not a git working tree. Callers pass the leading dot in
    ``suffix`` (e.g. ``.py``).
    """
    # ``.exists()`` (not ``.is_dir()``): a linked worktree's ``.git``
    # is a regular file pointing at the main repo's ``gitdir``.
    if not (repo_root / ".git").exists():
        return None
    try:
        result = sp.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                f"*{suffix}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=sp.SHORT_TIMEOUT_SECONDS,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    if not result.stdout:
        return []
    return [entry for entry in result.stdout.split("\0") if entry]


def _path_has_excluded_dir_segment(path: str, skip: frozenset[str]) -> bool:
    """True if any DIRECTORY segment of ``path`` is in ``skip``.

    Mirrors the ``os.walk`` ``dirnames[:]`` pruning semantic: only
    directory components are matched, never the filename itself, so
    a skip entry like ``"htmlcov"`` excludes ``htmlcov/x.py`` but
    keeps a top-level file literally named ``htmlcov.py``.
    """
    return any(segment in skip for segment in path.split("/")[:-1])


def discover_python_files(
    repo_root: Path,
    *,
    exclude_dirs: Sequence[str] = DEFAULT_IGNORED_DIRS,
) -> list[str]:
    """Return tracked ``.py`` files under ``repo_root``, repo-relative.

    ``exclude_dirs`` is an additional post-filter for tracked-but-
    skip directories; see ``_normalize_exclude_dirs`` for the
    accepted entry shapes and ``_path_has_excluded_dir_segment`` for
    the match semantic. Sorted for stable parametrize IDs.

    Falls back to ``os.walk`` when ``repo_root`` is not a git
    working tree -- the unit-test case using ``tmp_path``.
    """
    skip = _normalize_exclude_dirs(exclude_dirs)
    tracked = _git_tracked_files(repo_root, ".py")
    if tracked is not None:
        return sorted(
            p for p in tracked if not _path_has_excluded_dir_segment(p, skip)
        )
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        rel_dir = Path(dirpath).relative_to(repo_root)
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            found.append((rel_dir / name).as_posix())
    return sorted(found)


# Match the leading ``MAJOR.MINOR`` of a PEP 440 specifier so a value
# like ``">=3.14"`` or ``"==3.14.1"`` collapses to ``"3.14"`` for
# mypy's ``--python-version`` flag.
_PEP723_PYTHON_VERSION_RE = re.compile(r"(\d+\.\d+)")

_PEP723_OPEN = "# /// script"
_PEP723_CLOSE = "# ///"


@dataclasses.dataclass(frozen=True)
class Pep723Metadata:
    """Parsed deps + python-version target from a file's PEP 723 block."""

    deps: tuple[str, ...]
    python_version: str | None


def _extract_pep723_body(text: str) -> str | None:
    """Return the body TOML text of a PEP 723 ``script`` block, or ``None``.

    The ``# /// script`` opener is located by tokenizing the source
    and matching a genuine top-level (column 0) ``COMMENT`` token, so
    the block may sit anywhere a top-level comment can -- after the
    module docstring or imports, not only in the shebang prelude.
    Matching a comment *token* rather than scanning raw lines means a
    ``# /// script`` sitting inside a string literal (a PEP 723
    fixture embedded in a test) is a ``STRING`` token and is ignored.
    This mirrors uv's whole-file opener search while keeping the
    linter safe against fixture data.

    Once the opener is found, body lines must be ``#`` (a blank TOML
    line, becomes ``""``) or ``# <content>``; the block ends at the
    ``# ///`` closer. Anything else marks the block malformed and
    returns ``None``.
    """
    lines = [raw.rstrip("\r") for raw in text.splitlines()]
    opener_idx = _find_pep723_opener(text, lines)
    if opener_idx is None:
        return None
    return _collect_pep723_body(lines, opener_idx)


def _find_pep723_opener(text: str, lines: Sequence[str]) -> int | None:
    """Index in ``lines`` of the ``# /// script`` opener, or ``None``.

    Prefers a genuine column-0 ``COMMENT`` token so an opener inside
    a string literal is skipped. Tokenizing requires parseable
    source; when it fails (a syntax error mypy would reject anyway),
    the opener is sought in the shebang + comment + blank-line
    prelude only -- the conservative subset that needs no tokenizing.
    """
    try:
        reader = io.StringIO(text).readline
        for tok in tokenize.generate_tokens(reader):
            if (
                tok.type == tokenize.COMMENT
                and tok.start[1] == 0
                and tok.string.rstrip() == _PEP723_OPEN
            ):
                return tok.start[0] - 1
        return None
    except (tokenize.TokenError, SyntaxError):
        return _find_pep723_opener_in_prelude(lines)


def _find_pep723_opener_in_prelude(lines: Sequence[str]) -> int | None:
    """Index of the opener within the top-of-file comment prelude.

    Stops at the first non-comment line, so only a block among the
    leading shebang / comment / blank lines is found. Used as the
    fallback when the source can't be tokenized.
    """
    for idx, line in enumerate(lines):
        if line.rstrip() == _PEP723_OPEN:
            return idx
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        return None
    return None


def _collect_pep723_body(lines: Sequence[str], opener_idx: int) -> str | None:
    """Body TOML text between the opener and the ``# ///`` closer.

    Body lines must be ``#`` (a blank TOML line, becomes ``""``) or
    ``# <content>``; anything else marks the block malformed and
    returns ``None``. An unclosed block (no ``# ///``) is ``None``.
    """
    body: list[str] = []
    for line in lines[opener_idx + 1 :]:
        if line.rstrip() == _PEP723_CLOSE:
            return "\n".join(body)
        if line == "#":
            body.append("")
            continue
        if line.startswith("# "):
            body.append(line[2:])
            continue
        return None
    return None


def extract_pep723_metadata(path: Path) -> Pep723Metadata | None:
    """Return PEP 723 metadata from ``path`` or ``None`` if absent.

    Reads the ``# /// script`` ... ``# ///`` top-level comment block
    from a Python file and returns its ``dependencies`` plus a
    ``--python-version`` argument derived from ``requires-python``
    (the leading ``MAJOR.MINOR``). Returns ``None`` for files
    without a block, with a malformed block, or with a block that
    contains neither field.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    body = _extract_pep723_body(text)
    if body is None:
        return None
    try:
        data = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        return None
    deps_raw = data.get("dependencies")
    deps: tuple[str, ...]
    if isinstance(deps_raw, list) and all(
        isinstance(d, str) for d in deps_raw
    ):
        deps = tuple(deps_raw)
    elif deps_raw is None:
        deps = ()
    else:
        return None
    python_version: str | None = None
    requires = data.get("requires-python")
    if isinstance(requires, str):
        version_match = _PEP723_PYTHON_VERSION_RE.search(requires)
        if version_match:
            python_version = version_match.group(1)
    if not deps and python_version is None:
        return None
    return Pep723Metadata(deps=deps, python_version=python_version)


@dataclasses.dataclass(frozen=True)
class ResolvedFile:
    """A discovered ``.py`` file paired with its resolved mypy deps.

    ``source`` records where the deps came from -- ``"pep723"`` when
    the file's ``# /// script`` block supplied them, ``"default"``
    when the caller's defaults were used. Consumers can inspect this
    to assert (e.g.) that every HA-coupled file has its own PEP 723
    block instead of relying on a project-wide default.
    """

    path: str
    deps: tuple[str, ...]
    python_version: str | None
    source: str


def validate_additional_targets(
    repo_root: Path,
    *,
    additional_targets: Sequence[str],
) -> list[str]:
    """Return human-readable errors for malformed ``python-targets``.

    ``python-targets`` is additive to auto-discovery and intended for
    extension-less shebang scripts that ``rglob("*.py")`` cannot find
    on its own. The following classes of mistake are caught:

    - **Missing target**: the path doesn't exist (typo, rename, stale
      entry after a delete) or isn't a regular file (a directory).
      Without this check the entry slips through to ruff / mypy and
      fails with cryptic "file not found" output well into the run.
    - **Redundant `.py` target**: a `.py` file path is already
      auto-discovered, so listing it under ``python-targets`` is
      either dead weight or a misunderstanding of the additive
      semantic.
    - **Symlink target**: ``python-targets`` takes the underlying
      real file only; the symlink alias is rejected outright. The
      symlink's target is either auto-discovered, listed elsewhere
      in ``python-targets``, or genuinely not meant to be linted --
      none of those want a symlink entry on top. ``Path.is_file()``
      follows symlinks, so the directory check below wouldn't catch
      this on its own.

    Empty list means the entries are well-formed.
    """
    errors: list[str] = []
    for target in additional_targets:
        full = repo_root / target
        if target.endswith(".py"):
            errors.append(
                f"python-targets entry `{target}` ends in `.py`. "
                "All `.py` files are auto-discovered -- "
                "python-targets is additive for extension-less "
                "shebang scripts only. Drop this entry."
            )
            continue
        if not full.exists():
            errors.append(
                f"python-targets entry `{target}` does not exist "
                "under the repo root. (Typo? Renamed? Deleted?)"
            )
            continue
        if full.is_symlink():
            errors.append(
                f"python-targets entry `{target}` is a symlink. "
                "python-targets does not accept symlinks; drop the "
                "entry."
            )
            continue
        if not full.is_file():
            errors.append(
                f"python-targets entry `{target}` is not a regular "
                "file. python-targets takes file paths, not "
                "directories (auto-discovery walks directories)."
            )
    return errors


def resolve_files(
    repo_root: Path,
    *,
    discovered: Sequence[str],
    default_deps: Sequence[str] = (),
    default_python_version: str | None = None,
) -> list[ResolvedFile]:
    """Resolve each discovered file to its mypy deps.

    The PEP 723 ``# /// script`` block in the file is the sole
    per-file dep declaration; consumers that need to add
    ``--with`` deps to mypy for a specific module file add the
    block as a top-level comment in that file. Files without a block
    fall back
    to ``default_deps`` / ``default_python_version`` -- which a
    consumer typically leaves at the empty defaults so plain
    ``mypy --strict`` (in the project venv) handles the catch-all.
    """
    resolved: list[ResolvedFile] = []
    for rel in discovered:
        pep723 = extract_pep723_metadata(repo_root / rel)
        if pep723 is not None:
            resolved.append(
                ResolvedFile(
                    path=rel,
                    deps=pep723.deps,
                    python_version=pep723.python_version,
                    source="pep723",
                )
            )
            continue
        resolved.append(
            ResolvedFile(
                path=rel,
                deps=tuple(default_deps),
                python_version=default_python_version,
                source="default",
            )
        )
    return resolved

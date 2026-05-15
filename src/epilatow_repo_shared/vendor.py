# This is AI generated code
"""Vendor logic + drift test base.

The package ships ``shared/`` content as ``_shared/`` data inside the
installed package; this module reads it via ``importlib.resources`` and
copies it into a consumer's tree under ``_repo_shared/``, with
canonical-path symlinks pointing into the vendored copies.

Path-mapping rules (from the source-of-truth in repo-shared's
``shared/`` directory):

- ``shared/files/<rel>`` -> consumer's ``_repo_shared/files/<rel>``
  (vendored real file) and ``<consumer-root>/<rel>`` (symlink).
- ``shared/dotfiles/<rel>`` -> consumer's
  ``_repo_shared/dotfiles/<rel>`` (vendored real file) and
  ``<consumer-root>/.<first-segment>[/<rest>]`` (symlink), i.e.
  dot-prefix only the first path segment.
- ``shared/templates/<rel>`` and ``shared/dottemplates/<rel>``
  vendor the same way (real file under ``_repo_shared/<kind>/<rel>``),
  but the canonical-path entry is *copied*, not symlinked: it is
  seeded when the consumer has no file there, and on a later
  ``upgrade`` it is refreshed to the new master only while it still
  byte-matches the *old* master -- i.e. the consumer never customized
  it. Once customized it is left alone (never overwritten, never
  removed by cleanup). ``dottemplates`` dot-prefixes the canonical
  path the way ``dotfiles`` does. Used for files the consumer must
  own a real copy of: ``.gitignore`` (git won't follow a symlinked
  one) and ``CLAUDE.md`` (Claude's ``@``-includes resolve against
  the file's real on-disk location).

The wrapper script under ``shared/files/_repo_shared/repo-shared``
follows the same rule -- its real-file destination is
``consumer/_repo_shared/files/_repo_shared/repo-shared`` and its
symlink lands at ``consumer/_repo_shared/repo-shared``. No special
case in the path mapping; the user-visible wrapper path falls out of
the ``files/`` rule applied to a path whose first segment happens to
be ``_repo_shared``.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from collections.abc import Iterator
from importlib.resources import files as _resources_files
from pathlib import Path
from typing import ClassVar

VENDOR_DIRNAME = "_repo_shared"


def package_shared_root() -> Path:
    """Return the absolute path to the package's bundled ``_shared/``."""
    pkg_root = _resources_files("epilatow_repo_shared")
    return Path(str(pkg_root / "_shared"))


SHARED_KINDS: tuple[str, ...] = (
    "files",
    "dotfiles",
    "tests",
    "templates",
    "dottemplates",
)

# Kinds whose canonical-path entry is a one-time *copy* the consumer owns,
# not a symlink into the vendored tree. Seeded on init/upgrade only when
# the consumer has no file at the path; never overwritten, never removed
# by cleanup. The vendored copy still lands under ``_repo_shared/<kind>/``
# so the drift gate enforces it matches the package and ``init`` has a
# source to seed from.
TEMPLATE_KINDS: frozenset[str] = frozenset({"templates", "dottemplates"})


def _dotfile_link_rel(rel: str) -> str:
    """Apply the dot-prefix rule to a ``dotfiles`` / ``dottemplates`` rel.

    Dot-prefixes the first path segment only:
    ``markdownlint.json`` -> ``.markdownlint.json``;
    ``foo/bar`` -> ``.foo/bar``. Used by ``consumer_paths`` for both
    the ``dotfiles`` (symlink) and ``dottemplates`` (copy) kinds so
    the prefix rule lives in exactly one place.
    """
    first, _, rest = rel.partition("/")
    return "." + first + ("/" + rest if rest else "")


_CRUFT_SUFFIXES: tuple[str, ...] = ("~", ".swp", ".swo", ".bak", ".orig")
_CRUFT_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})


def _is_cruft(name: str) -> bool:
    """Filter out editor backups and OS metadata files.

    ``vendor()`` walks the maintainer's working tree via this iterator
    (the package is editable-installed at runtime, so ``shared/`` resolves
    to the live source). Without this filter, a stray ``*~`` editor
    backup or ``.DS_Store`` in the maintainer's working tree would land
    in every consumer's ``_repo_shared/`` on the next ``upgrade`` -- and
    the delivered drift test would fail on the next consumer pytest run.
    """
    if name in _CRUFT_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _CRUFT_SUFFIXES)


def iter_shared(shared_root: Path) -> Iterator[tuple[str, Path, str]]:
    """Yield ``(kind, source_path, rel)`` for every file under ``shared/``.

    ``kind`` is one of ``SHARED_KINDS``; ``rel`` is the path relative
    to ``shared/<kind>/``.
    Editor backups (``*~``, ``*.swp``) and OS metadata files
    (``.DS_Store``, ``Thumbs.db``) are skipped via ``_is_cruft`` -- they
    are never intentional shared content.
    """
    for kind in SHARED_KINDS:
        kind_dir = shared_root / kind
        if not kind_dir.is_dir():
            continue
        for path in sorted(kind_dir.rglob("*")):
            if path.is_file() and not path.is_symlink():
                if _is_cruft(path.name):
                    continue
                rel = path.relative_to(kind_dir).as_posix()
                yield kind, path, rel


def consumer_paths(
    consumer_root: Path,
    kind: str,
    rel: str,
) -> tuple[Path, Path | None]:
    """Return ``(vendor_path, link_path)`` for a shared file.

    ``link_path`` is the canonical-path location in the consumer's
    tree -- a symlink target for ``files`` / ``dotfiles``, a copy
    destination for ``templates`` / ``dottemplates`` (the caller
    distinguishes via ``TEMPLATE_KINDS``). It is ``None`` for
    ``tests``, whose files pytest finds via the ``testpaths`` entry
    init injects into the consumer's ``pyproject.toml``.
    """
    vendor_path = consumer_root / VENDOR_DIRNAME / kind / rel
    if kind in ("files", "templates"):
        link_path: Path | None = consumer_root / rel
    elif kind in ("dotfiles", "dottemplates"):
        link_path = consumer_root / _dotfile_link_rel(rel)
    elif kind == "tests":
        link_path = None
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    return vendor_path, link_path


def _is_correct_symlink(link_path: Path, vendor_path: Path) -> bool:
    if not link_path.is_symlink():
        return False
    try:
        return link_path.resolve(strict=False) == vendor_path.resolve(
            strict=False
        )
    except OSError:
        return False


@dataclasses.dataclass(frozen=True)
class VendorResult:
    """Outcome of a ``vendor()`` call.

    - ``installed``: canonical-path symlinks newly created this run
      (``files`` / ``dotfiles`` kinds).
    - ``seeded``: canonical-path copies newly written this run
      (``templates`` / ``dottemplates`` kinds) -- the consumer owns
      the copy thereafter.
    - ``updated``: template copies refreshed to the new master this
      run because the consumer's copy still byte-matched the *old*
      master (it had not been customized), so the master update is
      carried forward. A customized copy is never auto-updated -- it
      lands in ``out_of_sync`` instead.
    - ``skipped_ignored``: canonical-path entries not created because the
      consumer listed the path in ``.repo-shared-ignore`` (explicit
      opt-out -- consumer is free to have their own file at that path,
      or no file at all).
    - ``out_of_sync``: every canonical-path entry the run could not
      bring into agreement with the master, with a per-entry reason.
      Covers both kinds uniformly: a symlink-kind path shadowed by a
      local file or pointing at the wrong target, and a template-kind
      copy whose content has drifted from the master. ``init`` and
      ``upgrade`` treat any non-empty list as an error and surface
      every entry at once -- no whack-a-mole. ``.repo-shared-ignore``
      exempts a path from this check.
    - ``vendored``: every file written under ``_repo_shared/``.
    """

    installed: list[Path] = dataclasses.field(default_factory=list)
    seeded: list[Path] = dataclasses.field(default_factory=list)
    updated: list[Path] = dataclasses.field(default_factory=list)
    skipped_ignored: list[Path] = dataclasses.field(default_factory=list)
    out_of_sync: list[tuple[Path, str]] = dataclasses.field(
        default_factory=list
    )
    vendored: list[Path] = dataclasses.field(default_factory=list)


def _copy_file(src: Path, dest: Path) -> None:
    """Copy ``src`` to ``dest``, preserving mode (+ exec bit on shebang).

    Used both for vendored copies under ``_repo_shared/`` and for the
    canonical-path copies of the ``templates`` / ``dottemplates`` kinds.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    mode = src.stat().st_mode & 0o7777
    # Wheels do not preserve the exec bit, so files marked executable
    # in the master tree come back as 0o644 once uv installs them.
    # Detect a shebang line and force the exec bit on so the wrapper
    # script under ``_repo_shared/files/_repo_shared/repo-shared`` is
    # actually executable in the consumer.
    try:
        with src.open("rb") as fh:
            head = fh.read(2)
    except OSError:
        head = b""
    if head == b"#!":
        mode |= 0o755
    os.chmod(dest, mode)


def _create_symlink(link_path: Path, vendor_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    rel_target = os.path.relpath(vendor_path, link_path.parent)
    link_path.symlink_to(rel_target)


IGNORE_FILE = ".repo-shared-ignore"


def _read_ignore_file(consumer_root: Path) -> set[str]:
    """Parse ``.repo-shared-ignore`` into a set of consumer-relative paths.

    Each non-blank, non-``#``-comment line is treated as a path
    relative to the consumer root (e.g. ``CLAUDE.md``) -- the
    canonical-path location the consumer would see, not the
    ``_repo_shared/...`` vendor path.
    Whitespace is trimmed; trailing slashes are kept verbatim so a
    future directory-shaped ignore stays distinct from a file.

    Missing file returns the empty set.
    """
    ignore_path = consumer_root / IGNORE_FILE
    if not ignore_path.is_file():
        return set()
    entries: set[str] = set()
    for raw in ignore_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def check_in_sync(consumer_root: Path) -> list[tuple[Path, str]]:
    """List canonical-path entries that are out of sync with the master.

    Walks the package's shared content; for each entry (skipping the
    ``tests`` kind and any path listed in ``.repo-shared-ignore``):

    - Symlink kinds (``files`` / ``dotfiles``): the canonical path
      must be a symlink and the content it resolves to must
      byte-match the master.
    - Template kinds (``templates`` / ``dottemplates``): the canonical
      path must be a real file whose content byte-matches the master.

    Content comparison is used in both branches so repo-shared can
    dogfood the same check on its own clone -- there the symlinks
    point at ``shared/<kind>/`` rather than ``_repo_shared/<kind>/``,
    but the content matches either way. The one structural gap is the
    wrapper script under ``_repo_shared/repo-shared``, which exists
    only on the consumer side; entries whose canonical path lives
    inside ``_repo_shared/`` are skipped when running from repo-shared
    source. Returns ``(path, reason)`` tuples for every violation; an
    empty list means everything is in sync. Used both at the end of
    ``vendor()`` (to populate ``VendorResult.out_of_sync`` so ``init``
    / ``upgrade`` can surface every violation at once) and by the
    delivered ``InSyncBase`` test.
    """
    shared_root = package_shared_root()
    ignored = _read_ignore_file(consumer_root)
    in_source = _is_repo_shared_source_root(consumer_root)
    vendor_dir = consumer_root / VENDOR_DIRNAME
    violations: list[tuple[Path, str]] = []
    for kind, _src, rel in iter_shared(shared_root):
        _vendor_path, link_path = consumer_paths(consumer_root, kind, rel)
        if link_path is None:
            continue  # ``tests`` kind has no canonical-path entry.
        # Source has no ``_repo_shared/`` tree, so skip any canonical
        # path that lives inside it (e.g., the wrapper script).
        if in_source and vendor_dir in link_path.parents:
            continue
        link_rel = link_path.relative_to(consumer_root).as_posix()
        if link_rel in ignored:
            continue
        if kind in TEMPLATE_KINDS:
            try:
                current = link_path.read_bytes()
            except FileNotFoundError:
                violations.append((link_path, "template copy missing"))
                continue
            except OSError:
                violations.append((link_path, "template copy unreadable"))
                continue
            if current != _src.read_bytes():
                violations.append(
                    (link_path, "template copy out of sync with master")
                )
            continue
        # Symlink kind: must be a symlink whose resolved content
        # matches the master.
        if not link_path.is_symlink():
            if link_path.exists():
                violations.append(
                    (
                        link_path,
                        "shadowed by a local file (expected a symlink)",
                    )
                )
            else:
                violations.append((link_path, "symlink missing"))
            continue
        try:
            target_content = link_path.read_bytes()
        except OSError:
            violations.append(
                (link_path, "symlink target unreadable (dangling?)")
            )
            continue
        if target_content != _src.read_bytes():
            violations.append(
                (link_path, "symlink target does not match the master")
            )
    return violations


def vendor(consumer_root: Path) -> VendorResult:
    """Vendor package shared/ content into ``consumer_root``.

    Per shared file: settle the canonical-path entry first, then
    overwrite the vendored copy under ``_repo_shared/<kind>/<rel>``.
    The order is load-bearing for restartability -- see the
    in-function comment for details. For each canonical-path entry:

    - **Listed in ``.repo-shared-ignore``**: leave the canonical path
      alone (and remove any prior symlink the function placed there);
      reported in ``skipped_ignored``.
    - **Symlink kind (``files`` / ``dotfiles``)**: create the symlink
      to the vendored copy when the path is empty or already holds
      the correct symlink. A local file or non-matching symlink
      shadowing the canonical path is left alone -- ``check_in_sync``
      flags it.
    - **Template kind (``templates`` / ``dottemplates``)**: seed the
      copy when the canonical path is empty. If a copy is already
      there, byte-match against the master to decide:

      * matches the new master -> in sync, nothing to do;
      * matches the *old* master (the pre-overwrite vendored
        content) -> consumer never customized it, carry the master
        update forward (``updated``);
      * matches neither -> customized; leave untouched, again to be
        flagged by ``check_in_sync``.

    After all placement, ``check_in_sync`` walks the same surface and
    records every violation in ``VendorResult.out_of_sync`` -- both
    shadowed symlinks and drifted copies -- so ``init`` and ``upgrade``
    can surface them all at once and error out without whack-a-mole.

    Returns a ``VendorResult``.
    """
    shared_root = package_shared_root()
    result = VendorResult()
    ignored_rels = _read_ignore_file(consumer_root)

    for kind, src, rel in iter_shared(shared_root):
        vendor_path, link_path = consumer_paths(consumer_root, kind, rel)
        # Capture the old master (the previous vendored content)
        # before any write. The template branch uses it to tell a
        # consumer still on the old template (safe to auto-update)
        # from one who has customized their copy. The new master
        # write to ``vendor_path`` is deferred to the bottom of this
        # iteration so a crash partway through leaves the operation
        # restartable: if the canonical-path copy gets the new master
        # but the process dies before ``vendor_path`` is updated, the
        # next run reads the still-old ``prev_vendored``, sees the
        # canonical copy already matches the new master, and just
        # finishes the deferred write.
        prev_vendored: bytes | None = (
            vendor_path.read_bytes()
            if kind in TEMPLATE_KINDS and vendor_path.is_file()
            else None
        )

        if link_path is not None:
            link_rel = link_path.relative_to(consumer_root).as_posix()
            if link_rel in ignored_rels:
                # Explicit opt-out via .repo-shared-ignore. For
                # symlink kinds, remove any prior symlink we placed
                # so toggling the ignore on frees the canonical path;
                # a template copy is the consumer's, so leave it in
                # place.
                if kind not in TEMPLATE_KINDS and _is_correct_symlink(
                    link_path, vendor_path
                ):
                    link_path.unlink()
                result.skipped_ignored.append(link_path)
            elif kind in TEMPLATE_KINDS:
                if link_path.is_symlink() or link_path.exists():
                    try:
                        current = link_path.read_bytes()
                    except OSError:
                        current = None
                    new_master = src.read_bytes()
                    if (
                        current != new_master
                        and current is not None
                        and current == prev_vendored
                    ):
                        # The consumer is still on the old template
                        # and has not customized it, so carry the
                        # master update forward.
                        _copy_file(src, link_path)
                        result.updated.append(link_path)
                    # else: already in sync, or drifted (left for
                    # ``check_in_sync`` to record).
                else:
                    # Not present -- seed the copy on first sight.
                    _copy_file(src, link_path)
                    result.seeded.append(link_path)
            elif not _is_correct_symlink(link_path, vendor_path):
                if not (link_path.is_symlink() or link_path.exists()):
                    _create_symlink(link_path, vendor_path)
                    result.installed.append(link_path)
                # else: consumer has a local file (or wrong-target
                # symlink) shadowing the canonical path -- left for
                # ``check_in_sync`` to record.

        # Write the new master to ``vendor_path`` last (see the
        # restartability comment above).
        _copy_file(src, vendor_path)
        result.vendored.append(vendor_path)

    result.out_of_sync.extend(check_in_sync(consumer_root))
    return result


def cleanup_stale_vendored(consumer_root: Path) -> list[Path]:
    """Remove vendored files and dangling canonical-path symlinks.

    The package's bundled content is the source of truth -- a
    vendored file under ``_repo_shared/`` that has no counterpart
    in the package is stale (e.g. the package deleted a file in a
    later version). The corresponding canonical-path symlink at
    ``<consumer>/<rel>`` (or the dotfile-prefixed location) is
    also removed so it doesn't point at a path that just went
    away.

    Returns the list of removed paths (vendored files first, then
    dangling canonical-path symlinks).
    """
    shared_root = package_shared_root()
    expected: set[Path] = set()
    expected_link_for_vendor: dict[Path, Path] = {}
    for kind, _src, rel in iter_shared(shared_root):
        vendor_path, link_path = consumer_paths(consumer_root, kind, rel)
        expected.add(vendor_path)
        if link_path is not None:
            expected_link_for_vendor[vendor_path] = link_path

    vendor_dir = consumer_root / VENDOR_DIRNAME
    removed: list[Path] = []
    if not vendor_dir.is_dir():
        return removed

    stale_vendor_paths: list[Path] = []
    for path in sorted(vendor_dir.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        if path.is_file():
            if path not in expected:
                stale_vendor_paths.append(path)
                path.unlink()
                removed.append(path)
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass

    for stale_vendor in stale_vendor_paths:
        link = _derive_canonical_link(consumer_root, stale_vendor)
        if link is None or not link.is_symlink():
            continue
        try:
            resolved = link.resolve(strict=False)
        except OSError:
            continue
        try:
            stale_resolved = stale_vendor.resolve(strict=False)
        except OSError:
            stale_resolved = stale_vendor
        if resolved == stale_resolved:
            link.unlink()
            removed.append(link)

    return removed


def _derive_canonical_link(
    consumer_root: Path, vendor_path: Path
) -> Path | None:
    """Inverse of ``consumer_paths`` -- map a vendor_path back to its link.

    Returns ``None`` if ``vendor_path`` is not under the consumer's
    ``_repo_shared/<kind>/`` tree for a recognised ``kind``.
    """
    vendor_dir = consumer_root / VENDOR_DIRNAME
    try:
        rel = vendor_path.relative_to(vendor_dir)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    kind = parts[0]
    rest = "/".join(parts[1:])
    if not rest:
        return None
    try:
        _, link = consumer_paths(consumer_root, kind, rest)
    except ValueError:
        return None
    return link


def _is_repo_shared_source_root(consumer_root: Path) -> bool:
    """True when ``consumer_root`` is repo-shared's own source checkout.

    The drift test compares a consumer's vendored ``_repo_shared/``
    against the package's bundled content. repo-shared itself dogfoods
    its own ``shared/tests`` via ``testpaths`` but has no vendored
    copy -- it *is* the source -- so the test must skip there. A
    consumer that's missing ``_repo_shared/`` is a genuine failure
    and still surfaces.
    """
    return (consumer_root / "src" / "epilatow_repo_shared").is_dir() and (
        consumer_root / "shared"
    ).is_dir()


class VendorDriftBase:
    """Assert ``_repo_shared/`` matches the package's bundled content."""

    consumer_root: ClassVar[Path] = Path.cwd()

    def test_vendor_files_match_package(self) -> None:
        if _is_repo_shared_source_root(self.consumer_root):
            import pytest  # noqa: PLC0415

            pytest.skip("running from repo-shared source; no vendored copy")
        shared_root = package_shared_root()
        seen = 0
        for kind, src, rel in iter_shared(shared_root):
            vendor_path, _link = consumer_paths(self.consumer_root, kind, rel)
            rel_for_msg = vendor_path.relative_to(self.consumer_root)
            assert vendor_path.exists(), (
                f"missing vendored file: {rel_for_msg}"
            )
            assert src.read_bytes() == vendor_path.read_bytes(), (
                f"vendored content drift: "
                f"{vendor_path.relative_to(self.consumer_root)}\n"
                f"Run `_repo_shared/repo-shared upgrade` (or "
                f"`upgrade <sha>` to lock to the current pin) to "
                f"refresh the vendored copy."
            )
            seen += 1
        assert seen > 0, "package has no shared content; build glitch?"

    def test_no_extra_vendored_files(self) -> None:
        if _is_repo_shared_source_root(self.consumer_root):
            import pytest  # noqa: PLC0415

            pytest.skip("running from repo-shared source; no vendored copy")
        shared_root = package_shared_root()
        expected: set[Path] = set()
        for kind, _src, rel in iter_shared(shared_root):
            vendor_path, _link = consumer_paths(self.consumer_root, kind, rel)
            expected.add(vendor_path)

        vendor_dir = self.consumer_root / VENDOR_DIRNAME
        if not vendor_dir.is_dir():
            return
        extras: list[Path] = []
        for path in vendor_dir.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            # Skip pytest / mypy / ruff bytecode + cache artefacts
            # that pytest creates under ``_repo_shared/tests/...``
            # when it imports the shared test files; those are
            # runtime cruft, not vendored content drift.
            if any(part.endswith("_cache") for part in path.parts):
                continue
            if "__pycache__" in path.parts:
                continue
            if path not in expected:
                extras.append(path.relative_to(self.consumer_root))
        assert not extras, (
            "extra files under _repo_shared/ not present in the "
            f"package; remove or re-run upgrade: {extras}"
        )


class InSyncBase:
    """Assert every canonical-path entry matches its shared master.

    Drives ``check_in_sync``, which checks both kinds uniformly:

    - Symlink kinds (``files`` / ``dotfiles``) must resolve to the
      vendored copy under ``_repo_shared/<kind>/``.
    - Template kinds (``templates`` / ``dottemplates``) must byte-match
      the master.

    ``.repo-shared-ignore`` exempts a path from the check. The base
    needs no source-root skip: ``check_in_sync`` compares against
    ``package_shared_root()``, which resolves to the live ``shared/``
    when run from repo-shared's own clone -- so repo-shared dogfoods
    the same check every consumer runs.
    """

    consumer_root: ClassVar[Path] = Path.cwd()

    def test_canonical_paths_in_sync(self) -> None:
        violations = check_in_sync(self.consumer_root)
        assert not violations, (
            "canonical-path entries out of sync with the shared "
            "masters:\n"
            + "\n".join(
                f"  - {p.relative_to(self.consumer_root)}: {reason}"
                for p, reason in violations
            )
            + "\n\nFor a symlink kind: delete the shadowing file (or "
            "fix the symlink) and re-run ``init`` / ``upgrade``. For "
            "a template kind: copy the master from "
            "``_repo_shared/<kind>/<rel>`` over your copy. To keep "
            "your own version of an entry, list its path in "
            "``.repo-shared-ignore``."
        )

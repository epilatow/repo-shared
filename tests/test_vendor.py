# This is AI generated code
"""Unit tests for the vendor logic.

Most paths exercised against synthetic ``shared/`` trees in tmp_path
so the tests are independent of the package's bundled content.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from epilatow_repo_shared.vendor import (
    VendorResult,
    _is_correct_symlink,
    cleanup_stale_vendored,
    consumer_paths,
    iter_shared,
)


def _make_synthetic_shared(root: Path) -> None:
    files_dir = root / "files"
    dotfiles_dir = root / "dotfiles"
    templates_dir = root / "templates"
    dottemplates_dir = root / "dottemplates"
    files_dir.mkdir(parents=True)
    dotfiles_dir.mkdir(parents=True)
    templates_dir.mkdir(parents=True)
    dottemplates_dir.mkdir(parents=True)
    (files_dir / "AGENTS.md").write_text("agents\n")
    (files_dir / "CLAUDE.md").write_text("claude\n")
    (files_dir / "DEVELOPMENT_SHARED.md").write_text("dev shared\n")
    nested = files_dir / "_repo_shared"
    nested.mkdir()
    wrapper = nested / "repo-shared"
    wrapper.write_text("#!/bin/bash\necho hi\n")
    os.chmod(wrapper, 0o755)
    (dotfiles_dir / "markdownlint.json").write_text("{}\n")
    (templates_dir / "PROJECT.md").write_text("project template\n")
    (dottemplates_dir / "gitignore").write_text("*.tmp\n")


def test_iter_shared_yields_files_with_kinds(tmp_path: Path) -> None:
    _make_synthetic_shared(tmp_path)
    yielded = sorted((kind, rel) for kind, _src, rel in iter_shared(tmp_path))
    assert yielded == [
        ("dotfiles", "markdownlint.json"),
        ("dottemplates", "gitignore"),
        ("files", "AGENTS.md"),
        ("files", "CLAUDE.md"),
        ("files", "DEVELOPMENT_SHARED.md"),
        ("files", "_repo_shared/repo-shared"),
        ("templates", "PROJECT.md"),
    ]


def test_consumer_paths_files(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "files", "CLAUDE.md")
    assert vp == tmp_path / "_repo_shared" / "files" / "CLAUDE.md"
    assert lp == tmp_path / "CLAUDE.md"


def test_consumer_paths_files_nested(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "files", "_repo_shared/repo-shared")
    assert vp == (
        tmp_path / "_repo_shared" / "files" / "_repo_shared" / "repo-shared"
    )
    assert lp == tmp_path / "_repo_shared" / "repo-shared"


def test_consumer_paths_dotfiles_top_level(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "dotfiles", "markdownlint.json")
    assert vp == (tmp_path / "_repo_shared" / "dotfiles" / "markdownlint.json")
    assert lp == tmp_path / ".markdownlint.json"


def test_consumer_paths_dotfiles_nested(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "dotfiles", "ssh/config")
    assert vp == tmp_path / "_repo_shared" / "dotfiles" / "ssh" / "config"
    assert lp == tmp_path / ".ssh" / "config"


def test_consumer_paths_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        consumer_paths(tmp_path, "unknown", "x")


def test_consumer_paths_templates(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "templates", "PROJECT.md")
    assert vp == tmp_path / "_repo_shared" / "templates" / "PROJECT.md"
    assert lp == tmp_path / "PROJECT.md"


def test_consumer_paths_dottemplates(tmp_path: Path) -> None:
    vp, lp = consumer_paths(tmp_path, "dottemplates", "gitignore")
    assert vp == tmp_path / "_repo_shared" / "dottemplates" / "gitignore"
    assert lp == tmp_path / ".gitignore"


def _vendor_against(shared_root: Path, consumer: Path) -> VendorResult:
    """Run the real ``vendor()`` against a synthetic ``shared_root``.

    ``vendor()`` reads from the package's bundled content; for unit
    tests we point ``package_shared_root`` at a synthetic tree under
    ``tmp_path`` so the tests stay independent of the real package.
    """
    import epilatow_repo_shared.vendor as v

    original = v.package_shared_root
    v.package_shared_root = lambda: shared_root
    try:
        return v.vendor(consumer)
    finally:
        v.package_shared_root = original


def test_vendor_writes_files_and_links(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)

    assert (
        consumer / "_repo_shared" / "files" / "CLAUDE.md"
    ).read_text() == "claude\n"
    assert (consumer / "AGENTS.md").is_symlink()
    assert (consumer / "AGENTS.md").read_text() == "agents\n"
    assert (consumer / "CLAUDE.md").is_symlink()
    assert (consumer / "CLAUDE.md").read_text() == "claude\n"
    assert (consumer / ".markdownlint.json").read_text() == "{}\n"

    wrapper_link = consumer / "_repo_shared" / "repo-shared"
    assert wrapper_link.is_symlink()
    real = wrapper_link.resolve()
    assert real.exists()
    assert real.stat().st_mode & 0o111  # executable bit preserved


def test_vendor_idempotent(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)
    _vendor_against(shared, consumer)
    assert (consumer / "CLAUDE.md").read_text() == "claude\n"


def test_vendor_skips_symlink_when_preexisting_local_file(
    tmp_path: Path,
) -> None:
    """Pre-existing local file at canonical path takes precedence.

    The local file isn't overwritten; the canonical-path symlink isn't
    created; the vendored copy still lands under ``_repo_shared/``.
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    (consumer / "CLAUDE.md").write_text("local content\n")

    result = _vendor_against(shared, consumer)
    assert any(p.name == "CLAUDE.md" for p, _reason in result.out_of_sync)
    # Local file is untouched.
    assert (consumer / "CLAUDE.md").read_text() == "local content\n"
    assert not (consumer / "CLAUDE.md").is_symlink()
    # Vendored copy still lands.
    assert (
        consumer / "_repo_shared" / "files" / "CLAUDE.md"
    ).read_text() == "claude\n"
    # Sibling symlinks (no conflict) still get created.
    assert (consumer / "DEVELOPMENT_SHARED.md").is_symlink()


def test_cleanup_stale_vendored_removes_orphans(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)

    stale = consumer / "_repo_shared" / "files" / "STALE.md"
    stale.write_text("stale\n")

    import epilatow_repo_shared.vendor as v

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        removed = cleanup_stale_vendored(consumer)
    finally:
        v.package_shared_root = original

    assert stale in removed
    assert not stale.exists()
    assert (consumer / "_repo_shared" / "files" / "CLAUDE.md").exists()


def test_cleanup_removes_dangling_canonical_symlink(tmp_path: Path) -> None:
    """When a vendored file is removed, its canonical symlink follows."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)

    # Remove the shared CLAUDE.md so vendor and link become stale.
    (shared / "files" / "CLAUDE.md").unlink()

    import epilatow_repo_shared.vendor as v

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        removed = cleanup_stale_vendored(consumer)
    finally:
        v.package_shared_root = original

    vendored = consumer / "_repo_shared" / "files" / "CLAUDE.md"
    link = consumer / "CLAUDE.md"
    assert vendored in removed
    assert not vendored.exists()
    assert link in removed
    assert not link.exists() and not link.is_symlink()


def test_cleanup_keeps_unrelated_symlinks(tmp_path: Path) -> None:
    """A canonical-path symlink whose vendor target still exists stays."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)

    import epilatow_repo_shared.vendor as v

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        removed = cleanup_stale_vendored(consumer)
    finally:
        v.package_shared_root = original

    assert removed == []
    assert (consumer / "CLAUDE.md").is_symlink()
    assert (consumer / ".markdownlint.json").is_symlink()


def test_is_correct_symlink_true_after_create(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _is_correct_symlink(link, target)


def test_is_correct_symlink_false_for_real_file(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("x")
    target = tmp_path / "target"
    target.write_text("y")
    assert not _is_correct_symlink(real, target)


def test_read_ignore_file_missing(tmp_path: Path) -> None:
    from epilatow_repo_shared.vendor import _read_ignore_file

    assert _read_ignore_file(tmp_path) == set()


def test_read_ignore_file_parses(tmp_path: Path) -> None:
    from epilatow_repo_shared.vendor import _read_ignore_file

    (tmp_path / ".repo-shared-ignore").write_text(
        "# leading comment\n"
        "\n"
        "  .markdownlint.json  \n"
        "CLAUDE.md\n"
        "# trailing comment\n",
    )
    assert _read_ignore_file(tmp_path) == {
        ".markdownlint.json",
        "CLAUDE.md",
    }


def test_vendor_skips_ignored_paths(tmp_path: Path) -> None:
    """An ignored path is vendored but not symlinked."""
    import epilatow_repo_shared.vendor as v

    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    (consumer / ".repo-shared-ignore").write_text("CLAUDE.md\n")

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        result = v.vendor(consumer)
    finally:
        v.package_shared_root = original

    # Canonical-path symlink for CLAUDE.md is not installed.
    canonical = consumer / "CLAUDE.md"
    assert not canonical.exists()
    assert not canonical.is_symlink()
    assert canonical not in result.installed
    assert canonical in result.skipped_ignored
    # The vendor copy under _repo_shared/ DOES still land so the
    # drift test continues to gate the file's content.
    vendored_claude = consumer / "_repo_shared" / "files" / "CLAUDE.md"
    assert vendored_claude.is_file()
    assert vendored_claude in result.vendored


def test_vendor_removes_existing_symlink_for_newly_ignored(
    tmp_path: Path,
) -> None:
    """Toggling an ignore on cleans up the prior canonical symlink."""
    import epilatow_repo_shared.vendor as v

    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        # First pass: no ignore, symlink installed.
        v.vendor(consumer)
        assert (consumer / "CLAUDE.md").is_symlink()
        # Second pass: ignore the file; prior symlink should be
        # removed.
        (consumer / ".repo-shared-ignore").write_text("CLAUDE.md\n")
        v.vendor(consumer)
    finally:
        v.package_shared_root = original

    assert not (consumer / "CLAUDE.md").exists()


def test_vendor_ignored_path_classified_separately_from_out_of_sync(
    tmp_path: Path,
) -> None:
    """A path in ``.repo-shared-ignore`` is reported via ``skipped_ignored``.

    Without the ignore, the same scenario lands in ``out_of_sync``
    (the consumer's local file is divergent) and ``init`` errors.
    With the ignore present, the consumer has explicitly opted out --
    the entry moves to ``skipped_ignored`` so ``init`` stays SUCCESS.
    """
    import epilatow_repo_shared.vendor as v

    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    (consumer / "CLAUDE.md").write_text("consumer-owned variant")
    (consumer / ".repo-shared-ignore").write_text("CLAUDE.md\n")

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        result = v.vendor(consumer)
    finally:
        v.package_shared_root = original

    claude = consumer / "CLAUDE.md"
    assert claude.read_text() == "consumer-owned variant"
    assert any(p.name == "CLAUDE.md" for p in result.skipped_ignored)
    assert not any(p.name == "CLAUDE.md" for p, _reason in result.out_of_sync)


def test_vendor_seeds_template_copy_when_absent(tmp_path: Path) -> None:
    """A template kind lands a real copy (not a symlink) at the path."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)

    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.is_file() and not project.is_symlink()
    assert project.read_text() == "project template\n"
    assert project in result.seeded
    gitignore = consumer / ".gitignore"
    assert gitignore.is_file() and not gitignore.is_symlink()
    assert gitignore.read_text() == "*.tmp\n"
    assert gitignore in result.seeded
    # Vendored copy still lands under _repo_shared/ for the drift gate.
    assert (
        consumer / "_repo_shared" / "templates" / "PROJECT.md"
    ).read_text() == "project template\n"


def test_vendor_leaves_preexisting_template_untouched(
    tmp_path: Path,
) -> None:
    """A pre-existing customized template copy lands in ``out_of_sync``.

    The consumer's copy doesn't byte-match the upstream and isn't
    ignored, so ``check_in_sync`` flags it -- the unified divergence
    bucket ``init`` / ``upgrade`` surface as an error.
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    (consumer / "PROJECT.md").write_text("consumer-edited\n")

    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.read_text() == "consumer-edited\n"
    assert project in {p for p, _r in result.out_of_sync}
    assert project not in result.seeded


def test_vendor_template_copy_not_overwritten_on_second_run(
    tmp_path: Path,
) -> None:
    """Re-running vendor never re-seeds an already-placed template."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)
    (consumer / "PROJECT.md").write_text("edited after seed\n")

    _vendor_against(shared, consumer)
    assert (consumer / "PROJECT.md").read_text() == "edited after seed\n"


def test_vendor_in_sync_template_not_flagged(tmp_path: Path) -> None:
    """A pre-existing copy still matching the upstream is silently OK.

    Only a *drifted* template lands in ``out_of_sync``; one that
    byte-matches the upstream (the steady state after a clean seed, or
    a re-run) is in sync and reported nowhere.
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)  # seeds PROJECT.md from upstream

    result = _vendor_against(shared, consumer)  # re-run, copy in sync

    project = consumer / "PROJECT.md"
    assert project.read_text() == "project template\n"
    assert project not in {p for p, _r in result.out_of_sync}
    assert project not in result.seeded
    assert project not in result.updated


def test_vendor_updates_unmodified_template_to_new_upstream(
    tmp_path: Path,
) -> None:
    """A consumer still on the old upstream adopts the upstream update.

    Seed PROJECT.md, change the upstream, re-vendor. The consumer never
    touched their copy (it still byte-matches the old upstream), so
    ``vendor()`` carries the update forward into the canonical copy.
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)  # seeds PROJECT.md v1
    assert (consumer / "PROJECT.md").read_text() == "project template\n"

    (shared / "templates" / "PROJECT.md").write_text("project template v2\n")
    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.read_text() == "project template v2\n"
    assert project in result.updated
    assert project not in {p for p, _r in result.out_of_sync}
    assert project not in result.seeded


def test_vendor_keeps_customized_template_on_upstream_change(
    tmp_path: Path,
) -> None:
    """A customized copy is left alone even when the upstream changes.

    The consumer edited their copy, so it no longer matches the old
    upstream; ``vendor()`` must not clobber it, and reports it as
    drifted rather than updated.
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)  # seeds PROJECT.md v1
    (consumer / "PROJECT.md").write_text("consumer-customized\n")

    (shared / "templates" / "PROJECT.md").write_text("project template v2\n")
    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.read_text() == "consumer-customized\n"
    assert project in {p for p, _r in result.out_of_sync}
    assert project not in result.updated


def test_vendor_resumes_when_canonical_copy_already_at_new_upstream(
    tmp_path: Path,
) -> None:
    """Crash between canonical-copy write and vendor_path write -> retry.

    ``vendor()`` writes the canonical-path copy before the vendored
    upstream under ``_repo_shared/<kind>/<rel>`` so the operation
    converges under retry: a process that died after auto-updating
    the copy but before refreshing ``vendor_path`` should not, on
    the next pass, mistake an un-customized copy for a customized
    one. Simulate that state by hand-syncing the consumer's copy to
    the new upstream while leaving the old upstream under
    ``_repo_shared/``, then re-vendor and assert the retry is silent
    (no out-of-sync flag, no auto-update, no seed).
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)  # seeds PROJECT.md v1 + vendors v1

    (shared / "templates" / "PROJECT.md").write_text("project template v2\n")
    # Mid-upgrade crash state: canonical copy already at the new
    # upstream, _repo_shared/ still on the old upstream.
    (consumer / "PROJECT.md").write_text("project template v2\n")
    assert (
        consumer / "_repo_shared" / "templates" / "PROJECT.md"
    ).read_text() == "project template\n"

    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.read_text() == "project template v2\n"
    assert project not in {p for p, _r in result.out_of_sync}
    assert project not in result.updated
    assert project not in result.seeded
    # ``_repo_shared/`` caught up on this pass.
    assert (
        consumer / "_repo_shared" / "templates" / "PROJECT.md"
    ).read_text() == "project template v2\n"


def test_vendor_first_run_preexisting_template_matching_upstream_is_silent(
    tmp_path: Path,
) -> None:
    """First vendor where the target already byte-matches the upstream.

    The consumer happened to drop in an identical copy before
    onboarding -- there is no ``_repo_shared/`` yet, so no old upstream
    to compare against. It already matches the upstream, so we are
    onboarded: nothing to do, in no bucket (not in ``out_of_sync``,
    ``seeded``, or ``updated``).
    """
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    # Target present and byte-identical to the upstream before any
    # vendoring -- the cold-init "no prev_vendored" path.
    (consumer / "PROJECT.md").write_text("project template\n")

    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert project.read_text() == "project template\n"
    assert project not in {p for p, _r in result.out_of_sync}
    assert project not in result.seeded
    assert project not in result.updated


def test_vendor_template_respects_repo_shared_ignore(
    tmp_path: Path,
) -> None:
    """A template path in ``.repo-shared-ignore`` is not seeded."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    (consumer / ".repo-shared-ignore").write_text("PROJECT.md\n")

    result = _vendor_against(shared, consumer)

    project = consumer / "PROJECT.md"
    assert not project.exists()
    assert project in result.skipped_ignored
    assert project not in result.seeded


def test_cleanup_keeps_seeded_template_copy(tmp_path: Path) -> None:
    """``cleanup_stale_vendored`` never removes a consumer-owned copy."""
    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    _vendor_against(shared, consumer)
    # Drop the template from the shared tree: its vendored copy is now
    # stale, but the consumer's canonical copy must survive.
    (shared / "templates" / "PROJECT.md").unlink()

    import epilatow_repo_shared.vendor as v

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        removed = cleanup_stale_vendored(consumer)
    finally:
        v.package_shared_root = original

    vendored = consumer / "_repo_shared" / "templates" / "PROJECT.md"
    assert vendored in removed
    assert not vendored.exists()
    project = consumer / "PROJECT.md"
    assert project.is_file()
    assert project not in removed


def test_cleanup_then_vendor_migrates_symlink_to_template_copy(
    tmp_path: Path,
) -> None:
    """A canonical path that moved from a symlink kind to a template kind.

    Mirrors the migration where a file used to be a ``files``-kind
    symlink and is now a ``templates``-kind copy. cleanup-before-vendor
    must clear the stale symlink so ``vendor()`` seeds the real copy.
    """
    import epilatow_repo_shared.vendor as v

    shared = tmp_path / "shared"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _make_synthetic_shared(shared)
    # Old layout: PROJECT.md is a ``files``-kind symlink.
    (shared / "templates" / "PROJECT.md").unlink()
    (shared / "files" / "PROJECT.md").write_text("project template\n")
    _vendor_against(shared, consumer)
    assert (consumer / "PROJECT.md").is_symlink()

    # New layout: PROJECT.md moves to ``templates/``.
    (shared / "files" / "PROJECT.md").unlink()
    (shared / "templates" / "PROJECT.md").write_text("project template\n")

    original = v.package_shared_root
    v.package_shared_root = lambda: shared
    try:
        cleanup_stale_vendored(consumer)
        v.vendor(consumer)
    finally:
        v.package_shared_root = original

    project = consumer / "PROJECT.md"
    assert project.is_file() and not project.is_symlink()
    assert project.read_text() == "project template\n"

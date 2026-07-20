# This is AI generated code
"""End-to-end smoke tests driving ``cli.main`` against tmp git repos.

These spin up real ``git init`` repos and shell out to ``uv init`` /
``uv add`` against the local repo-shared clone as the source URL, so
they exercise ``_cmd_init`` + ``_cmd_status`` + the surrounding
helpers (``_ensure_repo_shared_dep``, ``vendor``, ``_read_locked_sha``)
the way a real consumer would.

``init`` / ``upgrade`` / ``status`` refuse based on the *target*
repo's state (``_classify_repo``), not on where the CLI source
lives, so these tests drive them against tmp targets by explicit
path and need no patching of the running-from-a-clone probe.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from epilatow_repo_shared import cli
from epilatow_repo_shared.exit_codes import ExitCode

_NPX_REQUIRED = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason=(
        "npx not on PATH; install Node (see Requirements in README) "
        "to run integration tests that exercise the consumer's full "
        "delivered suite (which gates ``test_markdownlint.py``)."
    ),
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_SOURCE_URL = f"git+file://{REPO_ROOT}"


def _run_cli(argv: list[str]) -> ExitCode:
    parser = cli.args_parser()
    args = parser.parse_args(argv)
    return cli.main(args)


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(path)],
        check=True,
    )


def _init_consumer(tmp_path: Path) -> Path:
    _git_init(tmp_path)
    exit_code = _run_cli(
        [
            "init",
            "--source",
            LOCAL_SOURCE_URL,
            "--repo",
            str(tmp_path),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    return tmp_path


def test_init_creates_pyproject_and_vendor_layout(
    tmp_path: Path,
) -> None:
    consumer = _init_consumer(tmp_path)

    pyproject = (consumer / "pyproject.toml").read_text()
    assert 'name = "epilatow-repo-shared"' in pyproject or (
        '"epilatow-repo-shared"' in pyproject
    )
    assert "[tool.uv.sources]" in pyproject

    assert (consumer / "uv.lock").is_file()

    vendor = consumer / "_repo_shared"
    assert (vendor / "files").is_dir()
    assert (vendor / "dotfiles").is_dir()
    assert (vendor / "templates").is_dir()
    assert (vendor / "dottemplates").is_dir()
    assert (vendor / "tests").is_dir()
    assert (vendor / "repo-shared").is_file()

    # ``files`` + ``dotfiles`` kinds install canonical-path symlinks.
    for canonical in (
        "AGENTS.md",
        "DEVELOPMENT_SHARED.md",
        "DEVELOPMENT_SHARED_AGENT.md",
        ".markdownlint.json",
        ".markdownlint-cli2.jsonc",
    ):
        link = consumer / canonical
        assert link.is_symlink(), f"{canonical} should be a symlink"
        target = link.resolve()
        assert vendor in target.parents, (
            f"{canonical} should resolve into _repo_shared/, got {target}"
        )

    # ``templates`` + ``dottemplates`` kinds seed a real copied file --
    # never a symlink -- that the consumer owns thereafter.
    for canonical in ("CLAUDE.md", ".gitignore"):
        copy = consumer / canonical
        assert copy.is_file() and not copy.is_symlink(), (
            f"{canonical} should be a real copied file, not a symlink"
        )

    agents = (consumer / "AGENTS.md").read_text()
    guidance_files = (
        "README.md",
        "DEVELOPMENT.md",
        "DEVELOPMENT_SHARED.md",
        "DEVELOPMENT_AGENT.md",
        "DEVELOPMENT_SHARED_AGENT.md",
    )
    positions = [agents.index(f"`{path}`") for path in guidance_files]
    assert positions == sorted(positions)

    # ``tests`` kind does NOT install canonical-path symlinks. The
    # shared tests live solely at their vendored path; pytest finds
    # them via the ``testpaths`` entry that init injects.
    for shared_test in (
        "test_code_quality.py",
        "test_markdown_format.py",
        "test_markdownlint.py",
        "test_repo_shared_drift.py",
    ):
        vendored = vendor / "tests" / shared_test
        assert vendored.is_file(), f"missing vendored test: {vendored}"
        assert not (consumer / "tests" / "repo-shared" / shared_test).exists()

    # init injects ``testpaths`` to point at the vendored shared
    # tests.
    assert (
        '"_repo_shared/tests"' in pyproject
        or "'_repo_shared/tests'" in pyproject
    )


def test_init_refuses_when_not_a_git_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.ERROR
    assert "not a git repo" in capsys.readouterr().err
    assert not (tmp_path / "pyproject.toml").exists()


def test_init_refuses_on_already_onboarded_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second ``init`` on an onboarded repo is a usage error.

    Once a repo carries a vendored ``_repo_shared/``, ``init`` has
    nothing to do -- ``upgrade`` bumps the pin and ``status``
    inspects drift. The refusal steers the user there rather than
    silently re-running onboarding.
    """
    consumer = _init_consumer(tmp_path)
    capsys.readouterr()

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(consumer)]
    )
    assert exit_code == ExitCode.USAGE
    assert "already onboarded" in capsys.readouterr().err


def test_status_after_init_reports_pinned_sha_in_sync(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    consumer = _init_consumer(tmp_path)
    capsys.readouterr()

    exit_code = _run_cli(["status", "--repo", str(consumer)])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "pinned:" in out
    assert "vendor in sync" in out

    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    assert head_sha in out


def test_status_ignores_runtime_caches_but_flags_other_extras(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    consumer = _init_consumer(tmp_path)
    tests_dir = consumer / "_repo_shared" / "tests"
    artifacts = (
        tests_dir / "__pycache__" / "test_shared.cpython-314.pyc",
        tests_dir / ".pytest_cache" / "v" / "cache" / "nodeids",
        tests_dir / ".mypy_cache" / "3.14" / "test_shared.data.json",
        tests_dir / ".ruff_cache" / "content",
    )
    for artifact in artifacts:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("generated\n")
    capsys.readouterr()

    exit_code = _run_cli(["status", "--repo", str(consumer)])
    assert exit_code == ExitCode.SUCCESS
    assert "vendor in sync" in capsys.readouterr().out

    extra = tests_dir / "customer_cache" / "unexpected.txt"
    extra.parent.mkdir()
    extra.write_text("not generated\n")
    exit_code = _run_cli(["status", "--repo", str(consumer)])
    assert exit_code == ExitCode.ERROR
    assert f"extra: {extra.relative_to(consumer)}" in capsys.readouterr().err


def test_status_without_lockfile_reports_no_pin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git_init(tmp_path)

    exit_code = _run_cli(["status", "--repo", str(tmp_path)])
    assert exit_code == ExitCode.SUCCESS
    assert "no epilatow-repo-shared pin" in capsys.readouterr().out


def test_status_refuses_outside_git_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_cli(["status", "--repo", str(tmp_path)])
    assert exit_code == ExitCode.ERROR
    assert "not a git repo" in capsys.readouterr().err


def test_status_ignores_user_placed_extra_symlink_into_vendor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Extra symlinks pointing into the vendor dir don't trip status.

    Consumers are free to expose shared content at additional paths
    -- e.g. a ``claude/DEVELOPMENT_SHARED.md`` symlink mirroring the
    canonical-path symlink. As long as the target file still exists,
    ``status`` reports ``vendor in sync``. Regression guard against
    an over-broad "must be at a canonical path" check that flagged
    such symlinks as ``dangling``.
    """
    consumer = _init_consumer(tmp_path)
    extra_dir = consumer / "subdir"
    extra_dir.mkdir()
    target = consumer / "_repo_shared" / "files" / "DEVELOPMENT_SHARED.md"
    assert target.is_file(), "vendored target missing -- fixture broken"
    (extra_dir / "DEVELOPMENT_SHARED.md").symlink_to(
        "../_repo_shared/files/DEVELOPMENT_SHARED.md"
    )
    capsys.readouterr()

    exit_code = _run_cli(["status", "--repo", str(consumer)])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "vendor in sync" in out
    assert "broken symlink" not in out
    assert "dangling" not in out


def test_status_flags_truly_broken_vendor_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A symlink whose vendor target was deleted shows up as broken.

    ``cleanup_stale_vendored`` removes orphaned canonical symlinks
    during ``init`` / ``upgrade``; if one slips through (e.g. a
    consumer-placed symlink at a non-canonical path pointing at a
    file later removed from the vendor), ``status`` flags it.
    """
    consumer = _init_consumer(tmp_path)
    (consumer / "subdir").mkdir()
    (consumer / "subdir" / "ghost.md").symlink_to(
        "../_repo_shared/files/NOT_A_REAL_VENDOR_FILE.md"
    )
    capsys.readouterr()

    exit_code = _run_cli(["status", "--repo", str(consumer)])
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "broken symlink: subdir/ghost.md" in err


def test_init_preexisting_file_errors_on_shadowed_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-existing file at a symlink-kind path -> ERROR.

    The local file shadows the shared symlink and lands in
    ``out_of_sync``. ``init`` reports it and returns ERROR; other
    (non-conflicting) symlinks still get installed and the vendored
    copy remains under ``_repo_shared/`` for reference.
    """
    _git_init(tmp_path)
    consumer_dev = tmp_path / "DEVELOPMENT_SHARED.md"
    consumer_dev.write_text("consumer's own DEVELOPMENT_SHARED.md\n")

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.ERROR
    out = capsys.readouterr().out
    assert "canonical paths out of sync with the upstream" in out
    assert "DEVELOPMENT_SHARED.md" in out
    assert "shadowed by a local file" in out
    # The consumer's file is untouched.
    assert consumer_dev.is_file() and not consumer_dev.is_symlink()
    assert consumer_dev.read_text() == "consumer's own DEVELOPMENT_SHARED.md\n"
    # Non-conflicting canonical symlinks still got installed.
    assert (tmp_path / ".markdownlint.json").is_symlink()
    # The vendored copy is on disk under _repo_shared/.
    assert (
        tmp_path / "_repo_shared" / "files" / "DEVELOPMENT_SHARED.md"
    ).is_file()


def test_init_re_run_recovers_after_clearing_sync_violation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ERROR path's "delete the shadow then re-run init" recovery works.

    A shadowing local file makes the first ``init`` abort with ERROR,
    but ``_repo_shared/`` is already on disk, so the target is now
    onboarded-but-out-of-sync. Re-running ``init`` after clearing the
    shadow must finish onboarding (install the canonical symlink),
    not refuse as "already onboarded".
    """
    _git_init(tmp_path)
    shadow = tmp_path / "DEVELOPMENT_SHARED.md"
    shadow.write_text("consumer's own DEVELOPMENT_SHARED.md\n")
    assert (
        _run_cli(
            ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
        )
        == ExitCode.ERROR
    )
    assert (tmp_path / "_repo_shared").is_dir()
    capsys.readouterr()

    shadow.unlink()
    assert (
        _run_cli(
            ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
        )
        == ExitCode.SUCCESS
    )
    assert shadow.is_symlink()


def test_init_preexisting_template_errors_on_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A customized pre-existing template copy -> ERROR.

    The consumer's CLAUDE.md doesn't byte-match the upstream and isn't
    ignored, so ``check_in_sync`` flags it; ``init`` reports it and
    returns ERROR.
    """
    _git_init(tmp_path)
    consumer_claude = tmp_path / "CLAUDE.md"
    consumer_claude.write_text("consumer's own CLAUDE.md\n")

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.ERROR
    out = capsys.readouterr().out
    assert "canonical paths out of sync with the upstream" in out
    assert "CLAUDE.md" in out
    assert "template copy out of sync with upstream" in out
    # The consumer's copy is untouched.
    assert consumer_claude.read_text() == "consumer's own CLAUDE.md\n"
    # The vendored upstream still lands for the drift gate.
    assert (tmp_path / "_repo_shared" / "templates" / "CLAUDE.md").is_file()


def test_init_preexisting_template_matching_upstream_no_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First init where a pre-existing template already matches the upstream.

    The consumer dropped in a byte-identical ``CLAUDE.md`` before
    onboarding. It is already in sync -- ``init`` is done, stays
    SUCCESS, and says nothing about it being out of sync.
    """
    _git_init(tmp_path)
    upstream = (REPO_ROOT / "shared" / "templates" / "CLAUDE.md").read_text()
    (tmp_path / "CLAUDE.md").write_text(upstream)

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "out of sync" not in out
    # The consumer's already-matching copy is untouched.
    assert (tmp_path / "CLAUDE.md").read_text() == upstream


def test_init_ignored_path_succeeds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Path in ``.repo-shared-ignore`` -> SUCCESS.

    Explicit opt-out via the ignore file is the consumer saying "I
    own this canonical-path entry"; ``check_in_sync`` skips it, so
    there is no out-of-sync violation and ``init`` stays SUCCESS.
    Distinct from the pre-existing-file case (no ignore entry) where
    the same divergence ERRORs.
    """
    _git_init(tmp_path)
    (tmp_path / ".repo-shared-ignore").write_text("CLAUDE.md\n")

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "opted out via .repo-shared-ignore" in out
    # No CLAUDE.md copy (or file) at the canonical path.
    assert not (tmp_path / "CLAUDE.md").exists()
    # Vendored copy still lands.
    assert (tmp_path / "_repo_shared" / "templates" / "CLAUDE.md").is_file()


def test_init_from_clone_onboards_plain_target_by_path(
    tmp_path: Path,
) -> None:
    """``init --repo <plain-repo>`` works from a repo-shared clone.

    These tests run from the clone (pytest lives inside it), so this
    is the ``bin/repo-shared init --repo $REPO`` path: the target, not
    the CLI source location, decides whether ``init`` proceeds.
    """
    _git_init(tmp_path)
    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.SUCCESS
    assert (tmp_path / "_repo_shared").is_dir()


def test_init_refuses_when_target_is_repo_shared_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``init`` pointed at repo-shared's own source is a usage error.

    The refusal keys on the target's state, so this fires whether the
    source tree is reached by explicit path or as the default cwd of
    an ``init`` run with no path argument from inside the clone.
    """
    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(REPO_ROOT)]
    )
    assert exit_code == ExitCode.USAGE
    assert "repo-shared's own source" in capsys.readouterr().err


def test_init_adds_dep_to_pre_existing_pyproject_without_clobbering(
    tmp_path: Path,
) -> None:
    _git_init(tmp_path)
    # Pre-existing project with its own description + an unrelated dep
    # that uv add must preserve.
    existing = (
        "[project]\n"
        'name = "preexisting-consumer"\n'
        'version = "0.0.1"\n'
        'description = "Pre-existing project; init must not clobber."\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["packaging"]\n'
    )
    (tmp_path / "pyproject.toml").write_text(existing)

    exit_code = _run_cli(
        ["init", "--source", LOCAL_SOURCE_URL, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.SUCCESS

    text = (tmp_path / "pyproject.toml").read_text()
    # Pre-existing fields survive.
    assert 'name = "preexisting-consumer"' in text
    assert (
        'description = "Pre-existing project; init must not clobber."' in text
    )
    assert '"packaging"' in text
    # The dep + source got inserted.
    assert '"epilatow-repo-shared"' in text
    assert "[tool.uv.sources]" in text
    assert "epilatow-repo-shared" in text.split("[tool.uv.sources]")[1]
    # Vendor layout landed.
    assert (tmp_path / "_repo_shared" / "files").is_dir()
    claude = tmp_path / "CLAUDE.md"
    assert claude.is_file() and not claude.is_symlink()


def test_init_reports_error_when_source_url_is_invalid(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git_init(tmp_path)
    bogus_source = f"git+file://{tmp_path / 'definitely-not-a-repo'}"

    exit_code = _run_cli(
        ["init", "--source", bogus_source, "--repo", str(tmp_path)]
    )
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "uv could not add epilatow-repo-shared" in err
    # No vendoring happened.
    assert not (tmp_path / "_repo_shared").exists()


# Upgrade tests follow.
#
# Each upgrade test that needs a real bump uses a *fake source* -- a
# fresh local clone of this repo into ``tmp_path/fake-source`` that
# starts at the same HEAD as the live clone. The test then either
# leaves it at that HEAD (to exercise the no-op path) or adds a
# trivial bump commit on top (to exercise an actual SHA bump). The
# consumer is initialized against ``git+file://<fake-source>`` and
# given an ``origin`` remote that's a bare clone of itself, so the
# ``git fetch origin`` + ``origin/HEAD`` discovery in ``_cmd_upgrade``
# behaves the way it would in a real consumer.


def _git_in(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test Author",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _head_sha(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _clone_fake_source(into: Path) -> Path:
    """Copy the working tree to ``into`` and seed it as a fresh git repo.

    Plain ``git clone`` would mirror only what's committed in
    ``REPO_ROOT/.git``; integration tests need fake_source to track
    the working tree so a developer's uncommitted refactor is what
    the consumer's installed package sees. Snapshot the working
    tree, re-init git inside, and commit as the "fake-source
    baseline".
    """
    import shutil

    def _ignore(_dir: str, names: list[str]) -> list[str]:
        skip = {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "dist",
            "build",
            "tmp",
            ".coverage",
        }
        return [n for n in names if n in skip]

    shutil.copytree(REPO_ROOT, into, ignore=_ignore, symlinks=True)
    subprocess.run(
        ["git", "init", "--quiet", str(into)],
        check=True,
        capture_output=True,
    )
    _git_in(into, "add", "-A")
    _git_in(into, "commit", "-m", "fake-source baseline")
    return into


def _add_bump_commit(fake_source: Path) -> str:
    """Add a no-op commit on top of fake_source and return the new SHA."""
    marker = fake_source / "UPGRADE_TEST_MARKER"
    marker.write_text("bump marker for upgrade integration tests\n")
    _git_in(fake_source, "add", "UPGRADE_TEST_MARKER")
    _git_in(fake_source, "commit", "-m", "test: bump marker")
    return _head_sha(fake_source)


def _setup_consumer_with_origin(
    consumer: Path,
    source_url: str,
    *,
    pyproject_extras: str | None = None,
) -> tuple[Path, str]:
    """Init a consumer, commit, wire up a bare-clone origin, push.

    ``pyproject_extras``, if given, is appended to ``pyproject.toml``
    *before* the initial commit -- e.g. a ``[tool.repo-shared]``
    block configuring a custom ``test-command``. Returns
    ``(origin_bare_path, default_branch)``.
    """
    _git_init(consumer)
    exit_code = _run_cli(
        ["init", "--source", source_url, "--repo", str(consumer)]
    )
    assert exit_code == ExitCode.SUCCESS
    if pyproject_extras is not None:
        pyproject = consumer / "pyproject.toml"
        pyproject.write_text(pyproject.read_text() + pyproject_extras)
    _git_in(consumer, "add", "-A")
    _git_in(consumer, "commit", "-m", "initial onboard")

    origin = consumer.parent / f"{consumer.name}-origin.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(consumer), str(origin)],
        check=True,
        capture_output=True,
    )
    _git_in(consumer, "remote", "add", "origin", str(origin))
    _git_in(consumer, "push", "--quiet", "-u", "origin", "main")
    _git_in(consumer, "remote", "set-head", "origin", "main")
    return origin, "main"


def _setup_consumer_no_origin(consumer: Path, source_url: str) -> str:
    """Init + commit a consumer with no ``origin`` remote.

    The local-only analogue of ``_setup_consumer_with_origin`` -- a
    ``git.local`` checkout never pushed to a forge. Same init + commit
    on ``main``, but no bare-clone origin, so the upgrade's fetch /
    ``origin/HEAD`` discovery / push all have to degrade to local-only.
    Returns the default branch name.
    """
    _git_init(consumer)
    exit_code = _run_cli(
        ["init", "--source", source_url, "--repo", str(consumer)]
    )
    assert exit_code == ExitCode.SUCCESS
    _git_in(consumer, "add", "-A")
    _git_in(consumer, "commit", "-m", "initial onboard")
    assert (
        subprocess.check_output(
            ["git", "remote"], cwd=consumer, text=True
        ).strip()
        == ""
    ), "fixture must be genuinely local-only"
    return "main"


def _advance_origin_to_diverge(origin: Path, tmp_path: Path) -> None:
    """Plant a commit directly on ``origin``'s main so it diverges.

    Used to simulate "non-fast-forward push rejected" scenarios:
    after this, any consumer / maintainer-clone tied to ``origin``
    will have its local ``main`` strictly behind ``origin/main``, and
    ``git push --dry-run origin main:main`` rejects.
    """
    workspace = tmp_path / f"{origin.stem}-divergent-workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(workspace)],
        check=True,
        capture_output=True,
    )
    (workspace / "ORIGIN_DIVERGENCE_MARKER").write_text("plant\n")
    _git_in(workspace, "add", "ORIGIN_DIVERGENCE_MARKER")
    _git_in(workspace, "commit", "-m", "test: divergent commit on origin")
    _git_in(workspace, "push", "--quiet", "origin", "main")


_PLACEHOLDER_SHA = "0" * 40


def test_upgrade_refuses_outside_git_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_cli(
        [
            "upgrade",
            _PLACEHOLDER_SHA,
            "--repo",
            str(tmp_path),
            "--source",
            LOCAL_SOURCE_URL,
        ]
    )
    assert exit_code == ExitCode.ERROR
    assert "not a git repo" in capsys.readouterr().err


def test_upgrade_refuses_dirty_working_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    (consumer / "DIRTY_MARKER").write_text("local edit\n")
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            _PLACEHOLDER_SHA,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert exit_code == ExitCode.DIRTY
    assert "working tree has uncommitted changes" in capsys.readouterr().err


def test_upgrade_is_no_op_when_target_matches_current_pin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    current_sha = _head_sha(fake_source)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            current_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert "nothing to do" in capsys.readouterr().out
    wt_parent = consumer / ".wt"
    assert not wt_parent.exists()


def test_upgrade_no_op_ignores_dirty_working_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A consumer already at the target pin must report "nothing to do"
    # regardless of unrelated uncommitted work -- the dirty refusal is
    # for upgrades that would actually touch the tree, not no-ops.
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    current_sha = _head_sha(fake_source)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    (consumer / "DIRTY_MARKER").write_text("local edit\n")
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            current_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert "nothing to do" in capsys.readouterr().out
    assert not (consumer / ".wt").exists()


def test_upgrade_creates_worktree_and_bumps_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert wt_path.is_dir()
    wt_locked_sha = cli._read_locked_sha(wt_path)
    assert wt_locked_sha == bump_sha
    # Consumer's own checkout should still be on the pre-upgrade SHA --
    # the bump lives on the update branch until --push.
    consumer_locked_sha = cli._read_locked_sha(consumer)
    assert consumer_locked_sha is not None
    assert consumer_locked_sha != bump_sha


def test_upgrade_base_builds_worktree_on_local_ref(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--base`` bases the update worktree on a local ref.

    The default base is ``origin/<default-branch>``, which omits local
    commits that aren't pushed yet. ``--base main`` builds on the
    consumer's local ``main`` instead, carrying unpushed work into the
    upgrade.
    """
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)

    # A commit on local main that origin/main does not have.
    (consumer / "LOCAL_ONLY_MARKER").write_text("unpushed local work\n")
    _git_in(consumer, "add", "LOCAL_ONLY_MARKER")
    _git_in(consumer, "commit", "-m", "local: unpushed work")
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--base",
            "main",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert wt_path.is_dir()
    assert cli._read_locked_sha(wt_path) == bump_sha
    # The worktree carries the unpushed local commit -- proof it was
    # based on local main, not origin/main (which lacks the marker).
    assert (wt_path / "LOCAL_ONLY_MARKER").is_file()


@_NPX_REQUIRED
def test_upgrade_with_run_tests_succeeds_against_bumped_pin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--run-tests",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "tests passed" in out


@_NPX_REQUIRED
def test_upgrade_with_push_ff_merges_and_cleans_up(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    origin, default = _setup_consumer_with_origin(
        consumer, f"git+file://{fake_source}"
    )
    pre_push_origin_head = subprocess.check_output(
        ["git", "rev-parse", default], cwd=origin, text=True
    ).strip()
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--push",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    consumer_locked_sha = cli._read_locked_sha(consumer)
    assert consumer_locked_sha == bump_sha
    new_origin_head = subprocess.check_output(
        ["git", "rev-parse", default], cwd=origin, text=True
    ).strip()
    assert new_origin_head != pre_push_origin_head
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert not wt_path.exists()


def test_upgrade_with_push_rejects_when_origin_is_non_fast_forward(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    origin, _ = _setup_consumer_with_origin(
        consumer, f"git+file://{fake_source}"
    )
    bump_sha = _add_bump_commit(fake_source)
    _advance_origin_to_diverge(origin, tmp_path)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--push",
        ]
    )
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "git push --dry-run" in err
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert not wt_path.exists()


def test_upgrade_no_origin_creates_worktree_and_bumps_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A local-only consumer (no ``origin``) can still stage an upgrade.

    Regression guard: ``upgrade`` used to hard-require ``origin`` -- the
    ``git fetch origin`` and ``origin/HEAD`` discovery both failed
    outright -- so a ``git.local`` consumer could not upgrade at all.
    With no origin it bases the worktree on the local default branch.
    """
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_no_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert wt_path.is_dir()
    assert cli._read_locked_sha(wt_path) == bump_sha


@_NPX_REQUIRED
def test_upgrade_no_origin_push_ff_merges_locally(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--push`` on a local-only consumer lands the ff-merge locally.

    With no origin there is nowhere to push, so ``--push`` validates,
    ff-merges the bump into the local default branch, and cleans up the
    worktree -- without failing on the missing remote.
    """
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_no_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--push",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert "no origin remote" in capsys.readouterr().out
    # The bump landed on the consumer's own checkout via the local
    # ff-merge, and the worktree was cleaned up.
    assert cli._read_locked_sha(consumer) == bump_sha
    short = bump_sha[:7]
    assert not (consumer / ".wt" / f"repo-shared-update-{short}").exists()


def test_upgrade_run_tests_failure_leaves_worktree_with_bump_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(
        consumer,
        f"git+file://{fake_source}",
        pyproject_extras=('\n[tool.repo-shared]\ntest-command = "false"\n'),
    )
    bump_sha = _add_bump_commit(fake_source)
    capsys.readouterr()

    exit_code = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--run-tests",
        ]
    )
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "tests failed" in err
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert wt_path.is_dir(), "worktree should be kept for debugging"
    # The bump commit should have landed in the worktree before tests ran.
    assert cli._read_locked_sha(wt_path) == bump_sha
    log_subject = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%s"], cwd=wt_path, text=True
    ).strip()
    assert log_subject.startswith("- repo-shared: upgrade from ")
    assert " to " in log_subject and log_subject.endswith(".")


def test_upgrade_resumes_existing_worktree_on_re_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)

    # First run: stage the bump in the worktree, no tests / no push.
    first = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert first == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    assert wt_path.is_dir()
    head_after_first = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    capsys.readouterr()

    # Second run: same target -- expect resume path, no new commit.
    second = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert second == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "resuming existing update worktree" in out
    head_after_second = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    assert head_after_second == head_after_first


def test_upgrade_dirty_worktree_blocks_re_run_without_force_retry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    first = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert first == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert second == ExitCode.DIRTY
    err = capsys.readouterr().err
    assert "uncommitted changes" in err
    assert "--force-retry" in err
    # Worktree + debug scratch should still be there for inspection.
    assert wt_path.is_dir()
    assert (wt_path / "DEBUG_SCRATCH").is_file()


def test_upgrade_force_retry_recreates_dirty_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_with_origin(consumer, f"git+file://{fake_source}")
    bump_sha = _add_bump_commit(fake_source)
    first = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
        ]
    )
    assert first == ExitCode.SUCCESS
    short = bump_sha[:7]
    wt_path = consumer / ".wt" / f"repo-shared-update-{short}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = _run_cli(
        [
            "upgrade",
            bump_sha,
            "--repo",
            str(consumer),
            "--source",
            f"git+file://{fake_source}",
            "--force-retry",
        ]
    )
    assert second == ExitCode.SUCCESS
    # Worktree is back, scratch is gone, the bump is freshly applied.
    assert wt_path.is_dir()
    assert not (wt_path / "DEBUG_SCRATCH").exists()
    assert cli._read_locked_sha(wt_path) == bump_sha


def _delete_canonical_link(consumer: Path, rel: str) -> Path:
    """Replace a delivered canonical symlink with a consumer-owned file."""
    path = consumer / rel
    assert path.is_symlink(), f"{rel} should be a symlink after init"
    path.unlink()
    path.write_text(f"consumer's own {rel}\n")
    return path


def test_revendor_clean_consumer_returns_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    consumer = _init_consumer(tmp_path)
    capsys.readouterr()

    exit_code = _run_cli(["_revendor", str(consumer)])
    assert exit_code == ExitCode.SUCCESS


def test_revendor_errors_when_canonical_path_is_out_of_sync(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``_revendor`` aborts on any out-of-sync canonical path.

    Upgrade-time re-vendor refuses to silently leave a consumer with a
    shadowed symlink or a drifted template copy: every violation is
    listed on stderr and the exit code is ERROR so ``upgrade`` aborts.
    The consumer fixes them all -- align to the upstream, or list the
    path in ``.repo-shared-ignore`` -- before re-running ``upgrade``.
    """
    consumer = _init_consumer(tmp_path)
    _delete_canonical_link(consumer, "DEVELOPMENT_SHARED.md")
    # Drift a template copy too, so we can verify both violations are
    # reported in a single pass (no whack-a-mole).
    (consumer / "CLAUDE.md").write_text("consumer-customized CLAUDE.md\n")
    capsys.readouterr()

    exit_code = _run_cli(["_revendor", str(consumer)])
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "DEVELOPMENT_SHARED.md" in err
    assert "shadowed by a local file" in err
    assert "CLAUDE.md" in err
    assert "template copy out of sync with upstream" in err
    assert ".repo-shared-ignore" in err
    # The consumer's local file is untouched.
    dev = consumer / "DEVELOPMENT_SHARED.md"
    assert dev.is_file()
    assert not dev.is_symlink()

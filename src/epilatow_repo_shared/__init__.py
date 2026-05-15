# This is AI generated code
"""epilatow-repo-shared: shared dev conventions and pytest helpers.

The package ships two things:

- ``shared/`` content (under the installed package as ``_shared/``)
  consumed by the ``init`` / ``upgrade`` / ``_revendor`` CLI
  subcommands via the internal ``vendor()`` function, and by
  ``VendorDriftBase`` in consumer test suites.
- pytest test bases (``MdformatCheckBase``, ``MarkdownlintCheckBase``,
  ``VendorDriftBase``, ``InSyncBase``) that consumer test files
  subclass, plus parametrize helpers in ``python_quality`` that
  ``test_code_quality.py`` invokes directly with
  ``@pytest.mark.parametrize``.

The pytest-dependent test bases live in their own submodules
(``epilatow_repo_shared.markdown``, ``.python_quality``, ``.vendor``)
and are NOT re-exported at package scope. Re-exporting would pull
``pytest`` into the import chain of the ``repo-shared`` CLI, which
only needs pytest at consumer test-runtime, not at install / CLI-
invocation time. Consumers import the bases directly from the
submodules:

    from epilatow_repo_shared.markdown import MdformatCheckBase
    from epilatow_repo_shared.markdown import MarkdownlintCheckBase
    from epilatow_repo_shared.vendor import VendorDriftBase
    from epilatow_repo_shared.vendor import InSyncBase
"""

from __future__ import annotations

"""Conftest that fails pytest startup once the policy is in place."""

import os

assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
raise RuntimeError("startup failed")

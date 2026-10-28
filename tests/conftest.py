"""Shared pytest setup: Hypothesis profiles.

`ci` is the default: derandomized so a failure on a PR reproduces locally, and with no
per-example deadline because shared runners are noisy. `nightly` is random and larger;
select it with HYPOTHESIS_PROFILE=nightly.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=200,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("nightly", max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

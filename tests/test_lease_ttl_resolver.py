"""Unit tests for state.lease_ttl_for_phase resolver."""
from __future__ import annotations

import unittest

from end_of_line import state as st


class LeaseResolvererTestCase(unittest.TestCase):
    def test_resolver_uses_per_phase_override(self) -> None:
        data = st.empty_state("test-plan", "plans")
        data["phases"] = [{"id": "alpha", "lease_ttl_minutes": 90}]
        self.assertEqual(st.lease_ttl_for_phase(data, "alpha"), 90)

    def test_resolver_falls_back_to_global(self) -> None:
        data = st.empty_state("test-plan", "plans")
        data["config"]["lease_ttl_minutes"] = 45
        self.assertEqual(st.lease_ttl_for_phase(data, "alpha"), 45)

    def test_resolver_falls_back_to_default(self) -> None:
        data: dict = {"phases": [], "config": {}}
        self.assertEqual(st.lease_ttl_for_phase(data, "alpha"), st.DEFAULT_LEASE_TTL_MIN)

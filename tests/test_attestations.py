"""Tests for state.stamp_attestation — attestations slot on current_claim."""

from __future__ import annotations

import unittest

from end_of_line import state as st


def _claim_data() -> dict:
    data = st.empty_state("test-plan", "plans")
    st.claim_phase(data, "some-phase", lease_minutes=30, claimed_by="tok-abc")
    return data


class StampAttestationTests(unittest.TestCase):
    def test_stamp_attestation_adds_verify_key(self) -> None:
        data = _claim_data()
        st.stamp_attestation(data, "verify", "abc123")
        self.assertEqual(data["current_claim"]["attestations"]["verify"]["commit_sha"], "abc123")

    def test_stamp_attestation_adds_simplify_key(self) -> None:
        data = _claim_data()
        st.stamp_attestation(data, "simplify", "def456")
        self.assertEqual(data["current_claim"]["attestations"]["simplify"]["commit_sha"], "def456")

    def test_stamp_attestation_lazy_inits_map(self) -> None:
        data = _claim_data()
        self.assertNotIn("attestations", data["current_claim"])
        st.stamp_attestation(data, "verify", "sha1")
        self.assertIn("attestations", data["current_claim"])

    def test_stamp_attestation_overwrites_existing(self) -> None:
        data = _claim_data()
        st.stamp_attestation(data, "verify", "old-sha")
        st.stamp_attestation(data, "verify", "new-sha")
        self.assertEqual(data["current_claim"]["attestations"]["verify"]["commit_sha"], "new-sha")

    def test_stamp_attestation_iso8601_z_format(self) -> None:
        data = _claim_data()
        st.stamp_attestation(data, "verify", "sha1")
        at = data["current_claim"]["attestations"]["verify"]["at"]
        self.assertRegex(at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_stamp_attestation_raises_without_claim(self) -> None:
        data = st.empty_state("test-plan", "plans")
        with self.assertRaises(ValueError):
            st.stamp_attestation(data, "verify", "sha1")

    def test_release_claim_drops_attestations(self) -> None:
        data = _claim_data()
        st.stamp_attestation(data, "verify", "sha1")
        st.release_claim(data, "tok-abc", "some-phase")
        self.assertIsNone(data["current_claim"])


if __name__ == "__main__":
    unittest.main()

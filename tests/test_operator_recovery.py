from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator"))
from rearm_construction import rearm


def test_candidate_recovery_never_accepts_a_live_instance_path():
    host = SimpleNamespace(store=SimpleNamespace(root=Path("/outside-managed-profile/state")))
    with pytest.raises(PermissionError, match="refuses_live_state"):
        rearm(host, "parent", expected={}, reason="not allowed", stopped_parent_attestation=True)


def test_candidate_recovery_requires_stopped_parent_attestation(tmp_path):
    host = SimpleNamespace(store=SimpleNamespace(root=tmp_path))
    with pytest.raises(PermissionError, match="stopped_parent_operator_attestation"):
        rearm(host, "parent", expected={}, reason="No automatic replay", stopped_parent_attestation=False)

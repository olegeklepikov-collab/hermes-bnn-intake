"""Qualification entry must not accidentally initialize a live optional module."""
import json
from pathlib import Path
import pytest
from bnn_intake.isolation import validate_modules


def test_research_isolation_rejects_foreign_mutable_root_before_initialization(tmp_path):
    config = {"modules": {"research": {"state_root": str(tmp_path / "foundation")}}}
    validate_modules(config, tmp_path)
    config["modules"]["research"]["state_root"] = str(tmp_path.parent / "live-state")
    with pytest.raises(PermissionError):validate_modules(config, tmp_path)


def test_GSD_all_mutable_paths_are_local_even_when_settings_file_is_local(tmp_path):
    fields = ["runtime_root", "workspace", "state_dir", "operator_dir", "anchor_file", "store_key", "native_key", "grant_key"]
    path = tmp_path / "settings.json"
    config = {"modules": {"gsd": {"settings_file": str(path)}}}
    good = {key: str(tmp_path / key) for key in fields}
    path.write_text(json.dumps(good)); validate_modules(config, tmp_path)
    for key in fields:
        path.write_text(json.dumps({**good, key: str(tmp_path.parent / "foreign" / key)}))
        with pytest.raises(PermissionError):validate_modules(config, tmp_path)


def test_unknown_optional_module_requires_own_explicit_isolation_adapter(tmp_path):
    with pytest.raises(PermissionError):validate_modules({"modules": {"unqualified": {}}}, tmp_path)

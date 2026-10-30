"""Fresh optional-module state; vendor sources are reused read-only, never copied auth."""
import hashlib
import json
import os
from pathlib import Path
from isolated_beads import ROOT, BASELINE


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gsd(directory, workspace):
    directory, workspace = Path(directory).resolve(), Path(workspace).resolve()
    test_root = (ROOT / ".test-state").resolve()
    if not directory.is_relative_to(test_root) or directory.exists() or not workspace.is_relative_to(test_root):
        raise PermissionError("new_repository_module_state_required")
    for name, expected in BASELINE["source_hashes"].items():
        if sha(name) != expected:
            raise PermissionError("pinned_baseline_changed")
    reference = json.loads((Path(BASELINE["hermes_core"]).parent / "Memory/settings.json").read_text())["modules"]["gsd"]
    original_path = Path(reference["settings_file"])
    for name, expected in reference["files"].items():
        if Path(name).is_symlink() or sha(name) != expected:
            raise PermissionError("native_GSD_source_integrity_failed")
    original = json.loads(original_path.read_text())
    directory.mkdir(mode=0o700)
    operator = directory / "operator"; operator.mkdir(mode=0o700)
    state = directory / "state"; state.mkdir(mode=0o700)
    # Explicit path/manifest/limit fields only. No live state, trust key, or identity.
    settings = {key: original[key] for key in ["bundle_root", "python", "provider", "limits",
                "integration_manifest_sha256", "engine_manifest_sha256"]}
    settings.update(runtime_root=str(directory), workspace=str(workspace), state_dir=str(state),
                    operator_dir=str(operator), anchor_file=str(operator / "anchor.json"))
    for name in ["store_key", "native_key", "grant_key"]:
        path = operator / (name + ".key")
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600); stream.write(os.urandom(32))
        settings[name] = str(path)
    path = operator / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n"); path.chmod(0o600)
    files = {name: expected for name, expected in reference["files"].items() if Path(name) != original_path}
    files[str(path)] = sha(path)
    return {"host_package": reference["host_package"], "settings_file": str(path), "files": files}


def research(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / ".test-state").resolve()) or directory.exists():
        raise PermissionError("new_repository_module_state_required")
    reference = json.loads((Path(BASELINE["hermes_core"]).parent / "Memory/settings.json").read_text())["modules"]["research"]
    for name, expected in BASELINE["source_hashes"].items():
        if sha(name) != expected:raise PermissionError("pinned_baseline_changed")
    for name, expected in reference["files"].items():
        if Path(name).is_symlink() or sha(name) != expected:
            raise PermissionError("native_Research_Foundation_source_integrity_failed")
    directory.mkdir(mode=0o700)
    return {"state_root": str(directory), **{key: reference[key] for key in ["source_paths", "dolt_executable", "files"]}}

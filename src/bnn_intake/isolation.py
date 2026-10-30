"""Guard the disposable qualification entry before constructing optional modules."""
import json
from pathlib import Path


def validate_modules(config, case_root, *, inspect_mutable_settings=True):
    root = Path(case_root).resolve()
    def local(value):
        path = Path(value)
        if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PermissionError("intake_isolated_module_refuses_foreign_mutable_path")
    for name, settings in config.get("modules", {}).items():
        if name == "research":
            local(settings["state_root"])
        elif name == "gsd":
            local(settings["settings_file"])
            if not inspect_mutable_settings:
                continue  # Native Modules.require verifies this module only when used.
            value = json.loads(Path(settings["settings_file"]).read_text())
            for field in ["runtime_root", "workspace", "state_dir", "operator_dir", "anchor_file", "store_key", "native_key", "grant_key"]:
                local(value[field])
        else:
            raise PermissionError("intake_isolated_module_needs_explicit_path_contract")

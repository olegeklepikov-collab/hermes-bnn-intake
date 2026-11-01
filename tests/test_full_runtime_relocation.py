"""Actual full-platform compute relocation; no provider or source-state access."""
import copy,json,tempfile
from pathlib import Path
import pytest
from conftest import BASELINE
from bn_memory.isolated_runtime import runtime,run


def installed():
    return json.loads((Path(BASELINE['hermes_core']).parent/'Memory/settings.json').read_text())


def test_relocated_runtime_uses_only_the_declared_own_program_root():
    cfg=installed();pin=runtime(cfg);root=Path(cfg['package_root']).resolve().parents[2]
    assert Path(pin['python']).is_relative_to(root)
    sealed=json.loads((Path(cfg['package_root'])/'compute-runtime.json').read_text())
    assert pin['python_sha256']==sealed['python_sha256'] and pin['sandbox_sha256']==sealed['sandbox_sha256']
    assert pin['python']!=sealed['python']


def test_missing_alias_does_not_silently_use_the_source_machine():
    cfg=installed();cfg.pop('path_relocations',None)
    with pytest.raises(RuntimeError,match='runtime_mismatch'):runtime(cfg)


def test_alias_of_a_different_owner_is_rejected():
    cfg=installed();cfg['namespace']['owner_id']='different-fixture-owner'
    with pytest.raises(PermissionError,match='owner_or_root_mismatch'):runtime(cfg)


def test_different_executable_is_not_admitted_by_the_alias():
    cfg=installed();cfg['compute_python']='/usr/bin/false'
    with pytest.raises(RuntimeError,match='runtime_mismatch'):runtime(cfg)


def test_real_relocated_compute_cannot_read_the_original_owner_state():
    cfg=installed();origin=Path(cfg['path_relocations'][0]['from']);workspace=Path(cfg['projects'][cfg['default_project']])
    with tempfile.TemporaryDirectory(dir=workspace) as name:
        job=Path(name);(job/'control').mkdir();script=job/'script.py';inputs=job/'inputs.json'
        script.write_text("from pathlib import Path\ntry:\n Path("+repr(str(origin/'data/auth.json'))+").read_bytes()\n print('UNEXPECTED_READ')\nexcept PermissionError:\n print('SOURCE_STATE_DENIED')\n")
        inputs.write_text('{}');result=run(cfg,job,script,inputs)
        assert result['exit_code']==0 and result['stdout'].strip()==b'SOURCE_STATE_DENIED'
        assert result['os_isolation'] and all(result['probe'].values())

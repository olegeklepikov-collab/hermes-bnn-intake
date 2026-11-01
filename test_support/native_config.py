"""Read-only local dependency selection; never ships an owner's baseline."""
import hashlib,json,os
from pathlib import Path

def load(repo):
    value=os.environ.get('HERMES_INTAKE_NATIVE_ROOT')
    if not value:raise RuntimeError('Set HERMES_INTAKE_NATIVE_ROOT to your qualified BNN installation')
    root=Path(value).resolve(strict=True);core=root/'hermes-agent'
    contract=json.loads((Path(repo)/'src/bnn_intake/data/native-compatibility.json').read_text())
    memory=root/'Memory/releases'/contract['memory_version'];pins={}
    for base,entries in [(memory,contract['memory_files']),(core,contract['core_files'])]:
        for name,expected in entries.items():
            path=base/name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise RuntimeError('Unqualified native dependency: '+name)
            pins[str(path)]=expected
    settings=root/'Memory/settings.json'
    if not settings.is_file():raise RuntimeError('Qualified native memory settings required')
    pins[str(settings)]=hashlib.sha256(settings.read_bytes()).hexdigest()
    document=Path(os.environ.get('HERMES_INTAKE_DOCUMENT_PYTHON',str(root/'DocumentRuntime/venv/bin/python')))
    if not document.is_file():raise RuntimeError('Native document execution environment required; no success without an executor')
    return {'memory_release':str(memory),'hermes_core':str(core),'document_python':str(document),'source_hashes':pins,'path_relocations':json.loads(settings.read_text()).get('path_relocations',[])}

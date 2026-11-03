"""Read-only compatibility check; no Host, profile migration or model call."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
repo=Path(__file__).resolve().parents[1];cfg=json.loads(a.config.read_text());root=a.root.resolve(strict=True)
contract=json.loads((repo/'src/bnn_intake/data/native-compatibility.json').read_text())
import hashlib
for base,entries in [(Path(cfg['package_root']),contract['memory_files']),(root/'hermes-agent',contract['core_files'])]:
 for name,expected in entries.items():
  path=base/name
  if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise SystemExit('Unsupported dependency: '+name)
sys.path[:0]=[str(repo/'src'),str(Path(cfg['package_root'])),str(root/'hermes-agent')]
from bnn_intake.deployment import config_at,common_plugin_target
from bnn_intake.bootstrap import verify_native
config_at(root,a.config);verify_native(cfg);target=common_plugin_target(root,Path(cfg['profile_home']))
print(json.dumps({'compatible':True,'read_only':True,'component':__import__('bnn_intake').__version__,'memory_version':contract['memory_version'],
 'core_commit':contract['core_git_commit'],'single_common_plugin_target':str(target),'model_or_network_calls':0}))

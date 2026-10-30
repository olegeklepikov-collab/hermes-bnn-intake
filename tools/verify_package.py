"""Verify the exported payload and all wheel members without native imports."""
import csv,hashlib,io,json,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];m=json.loads((root/'PACKAGE-MANIFEST.json').read_text())
for name,h in m['files'].items():
 p=root/name
 if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise SystemExit('Payload mismatch: '+name)
wheel=root/'packages/hermes_bnn_intake-0.14.2-py3-none-any.whl';contract=json.loads((root/'packages/manifest.json').read_text())
if hashlib.sha256(wheel.read_bytes()).hexdigest()!=contract['wheel_sha256']:raise SystemExit('Wheel mismatch')
with zipfile.ZipFile(wheel) as archive:
 for name,h in contract['members_sha256'].items():
  raw=archive.read(name)
  if hashlib.sha256(raw).hexdigest()!=h:raise SystemExit('Wheel member mismatch: '+name)
  if name.startswith('bnn_intake/') and (root/'src'/name).read_bytes()!=raw:raise SystemExit('Source/wheel divergence: '+name)
print(json.dumps({'verified':True,'version':m['version'],'payload_files':len(m['files']),'wheel_sha256':contract['wheel_sha256']}))

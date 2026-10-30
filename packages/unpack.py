"""Install only the verified pure-Python intake wheel into a NEW directory."""
import argparse,base64,csv,hashlib,io,json
from pathlib import Path
import zipfile
def unpack(wheel, expected, target):
    if target.exists(): raise PermissionError("new_installation_target_required")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected: raise PermissionError("wheel_digest_mismatch")
    target.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        record = next(n for n in archive.namelist() if n.endswith(".dist-info/RECORD"))
        rows = {r[0]: r[1:] for r in csv.reader(io.StringIO(archive.read(record).decode()))}
        for info in archive.infolist():
            if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts or info.filename not in rows:
                raise PermissionError("unsafe_wheel_member")
            if not info.filename.startswith(("bnn_intake/", "hermes_bnn_intake-")): raise PermissionError("unexpected_wheel_member")
            data = archive.read(info); encoded, size = rows[info.filename]
            if encoded and encoded != "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode():
                raise PermissionError("wheel_RECORD_mismatch")
            if size and int(size) != len(data): raise PermissionError("wheel_RECORD_size_mismatch")
            path = target / info.filename; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);p.add_argument('--expected-sha256',required=True);p.add_argument('--target',type=Path,required=True);a=p.parse_args()
    unpack(a.wheel,a.expected_sha256,a.target)
    print(json.dumps({'installed':str(a.target),'wheel_sha256':a.expected_sha256,'profile_or_credentials_copied':False}))

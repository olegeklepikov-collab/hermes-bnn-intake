"""One real embedded Beads graph inside candidate test state; no live wrapper."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"test_support"))
from native_config import load
BASELINE = load(ROOT)
BINARY = Path(BASELINE["hermes_core"]).parent / "Beads/bin/bd"


def create(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / ".test-state") or directory.exists():
        raise PermissionError("new_isolated_beads_directory_required")
    expected = "c06a59fda589d1fc00e394027ee90992d5083aea487c301c9e9eaf592e22e8b4"
    if hashlib.sha256(BINARY.read_bytes()).hexdigest() != expected:
        raise PermissionError("pinned_beads_binary_changed")
    directory.mkdir(parents=True, mode=0o700)
    if sys.platform == "darwin":
        for name in ["com.dropbox.ignored", "com.apple.fileprovider.ignore#P"]:
            subprocess.run(["/usr/bin/xattr", "-w", name, "1", str(directory)], check=True, capture_output=True)
    project = directory / "project"; project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    for key, value in [("user.name", "Intake qualification"), ("user.email", "intake@example.invalid")]:
        subprocess.run(["git", "config", "--local", key, value], cwd=project, check=True, capture_output=True)
    script = directory / "cli.py"
    script.write_text('''import fcntl,os,sys
from pathlib import Path
root=Path(__file__).resolve().parent
for arg in sys.argv[1:]:
 if arg.startswith('-C') or any(arg==flag or arg.startswith(flag+'=') for flag in ['--db','--database','--directory','--global','--remote','--server','--shared-server','--proxied-server','--force','--reinit-local']):
  raise SystemExit('isolated_database_routing_required')
for key in list(os.environ):
 if key.startswith(('BEADS_','BD_')) or key=='DOLT_ROOT_PATH':os.environ.pop(key,None)
os.environ.update(BEADS_DIR=str(root/'project/.beads'),BEADS_WORKING_DIR=str(root/'project'),BEADS_ACTOR='Intake qualification',
 BEADS_REQUIRE_CONTEXT='1',BEADS_CONTEXT_SET='1',BEADS_NO_GIT_OPS='1',BD_NON_INTERACTIVE='1',
 DOLT_ROOT_PATH=str(root/'dolt-state'),BD_DISABLE_METRICS='1',BD_DISABLE_EVENT_FLUSH='1',DO_NOT_TRACK='1',NO_COLOR='1')
os.chdir(root/'project')
lock=(root/'cli.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX);os.set_inheritable(lock.fileno(),True)
binary=''' + repr(str(BINARY)) + '''
os.execv(binary,[binary,'--sandbox',*sys.argv[1:]])
''')
    command = directory / "bd-intake"
    python = Path(BASELINE["hermes_core"]) / "venv/bin/python"
    command.write_text("#!/bin/bash\nset -euo pipefail\nexec " + shlex.quote(str(python)) + " -B " + shlex.quote(str(script)) + ' "$@"\n')
    command.chmod(0o700)
    completed = subprocess.run([str(command), "init", "--prefix", "it", "--skip-agents", "--skip-hooks", "--non-interactive"], capture_output=True, text=True, timeout=60)
    (directory / "init.stdout").write_text(completed.stdout)
    (directory / "init.stderr").write_text(completed.stderr)
    if completed.returncode:
        raise RuntimeError("isolated_Beads_init_failed_see_test_state")
    output = subprocess.run([str(command), "create", "--title", "Isolated intake root", "--description", "Synthetic qualification; no external research", "--type", "task", "--json"], capture_output=True, text=True, timeout=45)
    if output.returncode:
        raise RuntimeError("isolated_Beads_root_create_failed_see_test_state")
    issue = json.loads(output.stdout)
    result = {"working_beads": {"command": str(command), "files": {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [command, script, BINARY]}},
        "task_pin_probe": issue["id"], "primary_issue": issue, "project": str(project),
        "version": subprocess.run([str(command), "--version"], capture_output=True, text=True, check=True).stdout.strip()}
    (directory / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result

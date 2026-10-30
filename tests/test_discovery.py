"""New OS process, actual profile discovery and command routing; no model access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from conftest import BASELINE, ROOT
from bnn_intake.policy import action_rules


def test_actual_profile_plugin_discovery_and_commands_in_a_new_process(fixture, tmp_path):
    store, engine, work = fixture
    profile = Path(store.config["profile_home"]); profile.mkdir()
    plugin = profile / "plugins/bnn-intake-isolated"
    shutil.copytree(ROOT / "native_plugin", plugin)
    settings = tmp_path / "native-settings.json"
    signing_key = tmp_path / "isolated-only-key"; signing_key.write_bytes(os.urandom(32)); signing_key.chmod(0o600)
    config = {**store.config, "package_root": BASELINE["memory_release"], "host_key": str(signing_key),
              "context_bytes": 200000, "max_request_bytes": 1048576,
              "background_sync_enabled": False, "semantic_context": False, "semantic_intake_bridge": True,
              "command_owners": ["fixture-owner"], "tool_policy": {"version": 1, "rules": action_rules()}}
    settings.write_text(json.dumps(config))
    (profile / "config.yaml").write_text("execution_controls_required: true\nplugins:\n  enabled: [bnn-intake-isolated]\n  entries:\n    bnn-intake-isolated:\n      settings:\n        settings_file: " + json.dumps(str(settings)) + "\n")
    required = {kind: ["bnn-intake-isolated"] for kind in ["tool", "tool_result", "llm", "auxiliary", "subagent_launch"]}
    (profile / "execution-controls.json").write_text(json.dumps({"version": 1, "required": required, "timeout_seconds": 10}))
    script = '''
import json
from hermes_cli.plugins import discover_plugins, get_plugin_manager, get_plugin_command_handler
from gateway.session_context import set_session_vars, clear_session_vars
discover_plugins()
manager = get_plugin_manager()
command = get_plugin_command_handler('intake')
assert command is not None, [(k, str(v)) for k,v in manager._plugins.items()]
tokens = set_session_vars(platform='cli', session_id='parent')
try:
    before = json.loads(command('status'))
    changed = json.loads(command('off'))
    after = json.loads(command('status'))
finally:
    clear_session_vars(tokens)
assert before['mode'] == 'on', before
assert after['mode'] == 'off', after
assert {'intake_work','intake_methods','intake_questions','intake_documents','intake_analysis'} <= manager._plugin_tool_names
from hermes_cli.lifecycle import invoke_hook
from hermes_cli.middleware import run_llm_execution_middleware
text = 'Isolated discovery check'
invoke_hook('pre_llm_call', session_id='parent', user_message=text, turn_id='discovery-turn', platform='cli')
calls = []
def synthetic_callback(request):
    assert 'BN_CONTEXT_ADMISSION' in json.dumps(request)
    assert 'BN_INTAKE_CONTEXT' not in json.dumps(request)
    calls.append(True)
    return {'choices':[{'finish_reason':'stop','message':{'content':'Synthetic','tool_calls':[]}}]}
run_llm_execution_middleware({'input':[{'role':'user','content':text}]}, synthetic_callback,
    session_id='parent', turn_id='discovery-turn', api_request_id='discovery-api', platform='cli')
assert len(calls) == 1
print(json.dumps({'plugin_discovered':True,'before':before,'after':after,'tools':sorted(manager._plugin_tool_names),
    'native_signed_input_and_common_llm_control':True,'callback_is_synthetic':True,'provider_calls':0}))
'''
    env = {key: os.environ[key] for key in ["PATH", "LANG", "PYTHONPATH"] if key in os.environ}
    env.update(HERMES_HOME=str(profile), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run([sys.executable, "-B", "-c", script], env=env, cwd=tmp_path,
                               text=True, capture_output=True, timeout=45)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    value = json.loads(completed.stdout.splitlines()[-1])
    assert value["plugin_discovered"] and not value["provider_calls"]
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        (Path(target) / "native-profile-discovery.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

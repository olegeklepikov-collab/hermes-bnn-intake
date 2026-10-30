"""Full native suite in disposable state. Raw reports stay local and ignored."""
import datetime,hashlib,json,os,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo/'test_support'))
from native_config import load
baseline=load(repo);stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
state=repo/'.test-state'/stamp;state.mkdir(parents=True);out=repo/'.test-output'/stamp;out.mkdir(parents=True)
if sys.platform=='darwin':
 for name in ['com.dropbox.ignored','com.apple.fileprovider.ignore#P']:
  subprocess.run(['/usr/bin/xattr','-w',name,'1',str(state)],check=True,capture_output=True)
env=dict(os.environ);env.update(PYTHONPATH=os.pathsep.join([str(repo/'src'),baseline['memory_release'],baseline['hermes_core'],str(repo/'test_support')]),
 HERMES_HOME=str(state/'home'),PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',BNN_INTAKE_TEST_EVIDENCE=str(out))
inputs={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['src','tests','native_plugin','operator','test_support','tools'] for p in (repo/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
command=[str(Path(baseline['hermes_core'])/'venv/bin/python'),'-B','-m','pytest','-q','--tb=short','--basetemp='+str(state/'pytest'),'--junitxml='+str(out/'junit.xml'),*sys.argv[1:]]
result=subprocess.run(command,cwd=repo,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(out/'pytest.log').write_text(result.stdout)
unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline['source_hashes'].items())
same_inputs=all((repo/p).is_file() and hashlib.sha256((repo/p).read_bytes()).hexdigest()==h for p,h in inputs.items())
suite=ET.parse(out/'junit.xml').getroot().find('testsuite');counts=dict(suite.attrib)
receipt={'exit_code':result.returncode,'counts':counts,'native_dependencies_unchanged':unchanged,'test_inputs_unchanged':same_inputs,
 'model_calls':0,'external_search_calls':0,'network_gateway_tested':False,'test_input_sha256':inputs}
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(result.stdout[-6000:]);print('Local receipt:',out/'receipt.json')
raise SystemExit(result.returncode if unchanged and same_inputs else 2)

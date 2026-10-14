"""Run the copied and original engines against one deterministic TDX fixture."""
import hashlib,json,os,runpy,subprocess,sys,tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
directory=Path(tempfile.mkdtemp(prefix='atrade-finaltrade-parity-'))
runpy.run_path(str(ROOT/'tests/finaltrade-fixture.py'))['create_fixture'](directory)
oracle=Path(sys.argv[1]) if len(sys.argv)>1 else Path('G:/CODE/Final trade/backend')
manifest=json.loads((ROOT/'vendor/finaltrade/source-manifest.json').read_text(encoding='utf-8'))
for item in manifest['files']:
 if item['path'].startswith('backend/'):
  source=oracle/item['path'].removeprefix('backend/')
  if hashlib.sha256(source.read_bytes()).hexdigest()!=item.get('upstreamSha256',item['sha256']):raise RuntimeError('Oracle source changed: '+item['path'])
def run(name):
 target=directory/name;target.mkdir()
 command=[sys.executable,'-B',str(ROOT/'tests/finaltrade-parity-runner.py'),str(directory),str(target/'result.json')]
 if name=='original':command.extend(['--oracle',str(oracle)])
 with (target/'run.log').open('w',encoding='utf-8') as output:
  subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'},stdout=output,stderr=subprocess.STDOUT,check=True,timeout=900)
 return json.loads((target/'result.json').read_text(encoding='utf-8'))
print('Fixture:',directory,flush=True)
with ThreadPoolExecutor(max_workers=2) as executor:
 original_future=executor.submit(run,'original');copied_future=executor.submit(run,'copied')
 original,copied=original_future.result(),copied_future.result()
subprocess.run([sys.executable,'-B',str(ROOT/'scripts/compare-finaltrade-results.py'),str(directory)],check=True)

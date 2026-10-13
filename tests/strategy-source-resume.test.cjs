const {test}=require('node:test');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
test('interrupted research retains its data directory across restart and migrates existing task records',()=>{
  execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-c',`
import json, tempfile
from pathlib import Path
from service.finaltrade_runtime import source_for_pending_tasks
from service.strategy_integration import StrategyIntegration
from types import SimpleNamespace
from threading import RLock
integration=object.__new__(StrategyIntegration)
integration.requests=0
integration.tasks=lambda: {'items':[{'status':'cancelled'}]}
integration.store=SimpleNamespace(_backtest_task_lock=RLock(),_backtest_plateau_task_lock=RLock(),_backtest_running_worker_ids={'finishing-old-directory'},_backtest_plateau_running_worker_ids=set())
assert integration.active(), 'a cancelled record is not proof that its worker has released the old source'
integration.store._backtest_running_worker_ids.clear()
assert not integration.active()
integration.requests=1
assert integration.active(), 'synchronous screener reads must also finish before changing directories'
with tempfile.TemporaryDirectory() as root:
    workspace=Path(root); directory=workspace/'finaltrade'; directory.mkdir()
    old=str(workspace/'old'); new=str(workspace/'new')
    tasks=directory/'plateau-tasks.json'
    tasks.write_text(json.dumps({'tasks':[{'task':{'status':'paused'}}]}))
    (directory/'market-source.json').write_text(json.dumps({'source':old}))
    assert source_for_pending_tasks(workspace,new)==old, 'a paused task must never resume on another directory'
    tasks.write_text(json.dumps({'tasks':[{'task':{'status':'succeeded'}}]}))
    assert source_for_pending_tasks(workspace,new)==new, 'completed records must not block new data'
    tasks.write_text(json.dumps({'tasks':[{'task':{'status':'running'}}]}))
    (directory/'market-source.json').unlink()
    (directory/'app-state.json').write_text(json.dumps({'config':{'tdx_data_path':str(Path(old)/'vipdoc')}}))
    assert source_for_pending_tasks(workspace,new)==str(Path(old)/'vipdoc')
    assert source_for_pending_tasks(workspace,old)==old, 'installation root and vipdoc are the same source'
`],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:'pipe'});
});

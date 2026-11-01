"""Close a reviewed diagnostic receipt without promoting a strategy trial."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'artifacts/strategy-search'
OUT = BASE/'independent-shadow-labels-v1'
RENDER = 'scripts/render-independent-shadow-labels.py'
QA = 'scripts/verify-independent-shadow-preview.cjs'
READOUT_AUDITOR = 'scripts/verify-independent-shadow-labels-readout.py'
ORDER = ['beforeChart','beforeSources','afterChart','afterSources']
TABLE_ROWS = [[18,18,18,36,72,12,12,18]] * 2


def require(ok, message):
    if not ok: raise ValueError(message)
def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
def ref(path): return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def bound(item, base=ROOT, external=False):
    require(isinstance(item,dict) and isinstance(item.get('path'),str)
            and isinstance(item.get('sha256'),str), 'Malformed artifact reference')
    path=Path(item['path'])
    path=(path if path.is_absolute() else base/path).resolve()
    if not external: require(path.is_relative_to(base.resolve()), 'Artifact escapes declared root')
    require(path.is_file() and sha(path)==item['sha256'], 'Changed artifact '+str(path))
    return path


def verify_presentation():
    """Only exact audited inputs and actually reviewed final QA may be delivered."""
    render_path=OUT/'render-receipt.json'
    render=read(render_path); visual=read(OUT/'visual-review.json')
    required=('plan.json','library-completion.json','validation.json','readout.json','readout-validation.json',
              'regime-maturity-context.json','regime-maturity-validation.json','presentation-source-pins-v4.json')
    require(all(render['inputPins'].get(name)==sha(OUT/name) for name in required), 'Presentation audit input missing/stale')
    for name,expected in render['inputPins'].items(): bound({'path':name,'sha256':expected},OUT)
    freeze=read(OUT/'presentation-source-pins-v4.json')
    require(freeze['passed'] is True and freeze['planSha256']==sha(OUT/'plan.json')
            and freeze['sourcePins'][RENDER]==sha(ROOT/RENDER) and freeze['sourcePins'][QA]==sha(ROOT/QA), 'Presentation source freeze changed')
    require(render['builderSha256']==sha(ROOT/RENDER) and render['deliveryOrder']==ORDER
            and set(render['fragments'])==set(render['payloads'])==set(ORDER)
            and render['chartRows']==36 and render['tableRows']==TABLE_ROWS
            and render['posthocMaturityIncluded'] is True and render['newAccountExecutions']==0
            and render['analysisArtifactsModified'] is False, 'Presentation builder/scope/matrix differs')
    for mapping in (render['fragments'],render['payloads']):
        for item in mapping.values(): bound(item,OUT)
    helpers=render['presentationHelpers']
    require(len(helpers)==3 and {Path(item['path']).name for item in helpers}==
            {'render-inline-chart.mjs','render-inline-sources.mjs','render.py'}, 'Actual shared presentation helper set differs')
    helper_paths=[bound(item,external=True) for item in helpers]
    require(len(set(helper_paths))==3, 'Duplicate presentation helper')
    require(visual['passed'] is True and visual['reviewedBy']=='root' and visual['actualImagesViewed'] is True
            and visual['renderReceiptSha256']==sha(render_path) and visual['observations'], 'Actual root image review missing/stale')
    qa_path=bound({'path':visual['qaPath'],'sha256':visual['qaSha256']})
    require(qa_path.is_relative_to(OUT/'browser-qa') and qa_path.name=='preview-verification.json', 'Wrong final QA receipt')
    qa=read(qa_path)
    require(qa['passed'] is True and qa['renderReceiptSha256']==sha(render_path) and qa['scriptSha256']==sha(ROOT/QA)
            and qa['errors']==[] and qa['actualMarketRuns']==0 and qa['analysisArtifactsModified'] is False,
            'Browser QA missing/stale or changed research')
    require(all(qa[k]==render[k] for k in ('fragments','payloads','deliveryOrder','tableRows','presentationHelpers')),
            'QA presentation differs from final render')
    require(len(qa['images'])==len(visual['images'])==8 and len({i['path'] for i in qa['images']})==8,
            'All eight final images must be reviewed')
    qa_images={bound(i,OUT):i['sha256'] for i in qa['images']}
    reviewed_images={bound(i):i['sha256'] for i in visual['images']}
    require(len(qa_images)==8 and qa_images==reviewed_images, 'Visual review does not bind all final QA images')
    require(all(path.is_relative_to(qa_path.parent) and path.suffix=='.png' for path in qa_images), 'Review contains unrelated screenshots')
    require(len(qa['chartViews'])==6 and len(qa['tableChecks'])==16 and len(qa['interactions'])==2
            and sum(row['rows'] for row in qa['tableChecks'])==408, 'Final source/chart QA is incomplete')
    return render,qa_path,helper_paths


def main():
    require(not (OUT/'delivery.json').exists(), 'Diagnostic already finalized')
    spec=importlib.util.spec_from_file_location('independent_runner', ROOT/'scripts/run-independent-shadow-labels.py')
    runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    p=runner.verify_plan()
    library=read(OUT/'library-completion.json')
    validation=read(OUT/'validation.json')
    readout_validation=read(OUT/'readout-validation.json')
    readout=read(OUT/'readout.json')
    regime_validation=read(OUT/'regime-maturity-validation.json')
    regime=read(OUT/'regime-maturity-context.json')
    decision=read(OUT/'decision.json')
    require(library['independentPathExecutions']==16310 and library['newCandidateAccounts']==0, 'Library counts differ')
    require(validation['passed'] and readout_validation['passed'] and regime_validation['passed'], 'Independent validation missing')
    for item in (library, validation, readout_validation, regime_validation):
        require(item['planSha256']==sha(OUT/'plan.json'), 'Stale plan binding')
    require(validation['libraryCompletionSha256']==sha(OUT/'library-completion.json') and
            validation['verifierSha256']==sha(ROOT/'scripts/verify-independent-shadow-labels.py'), 'Stale path audit')
    require(readout_validation['readoutSha256']==sha(OUT/'readout.json') and
            readout_validation['pathValidationSha256']==sha(OUT/'validation.json') and
            readout_validation['libraryCompletionSha256']==sha(OUT/'library-completion.json') and
            readout_validation['verifierSha256']==sha(ROOT/READOUT_AUDITOR), 'Stale readout audit')
    require(readout['builderSha256']==sha(ROOT/'scripts/summarize-independent-shadow-labels.py'), 'Changed readout implementation')
    require(regime_validation['contextSha256']==sha(OUT/'regime-maturity-context.json') and
            regime_validation['readoutSha256']==sha(OUT/'readout.json') and
            regime_validation['readoutValidationSha256']==sha(OUT/'readout-validation.json') and
            regime_validation['pathValidationSha256']==sha(OUT/'validation.json') and
            regime_validation['libraryCompletionSha256']==sha(OUT/'library-completion.json') and
            regime_validation['dailyRowsArtifactSha256']==regime['dailyRowsArtifact']['sha256'] and
            regime_validation['verifierSha256']==sha(ROOT/READOUT_AUDITOR) and
            regime_validation['posthoc'] is True, 'Stale regime maturity audit')
    runner.bound(regime['dailyRowsArtifact'])
    for proof in (validation,readout_validation,regime_validation):
        for name, expected in proof.get('sourcePins',{}).items():
            require(sha(ROOT/name)==expected,'Changed audit dependency '+name)
    render,qa_path,helper_paths=verify_presentation()
    require(decision['targetAchieved'] is False and decision['certified'] is False and
            decision['newCandidateAccounts']==0 and decision['nextStage'], 'Decision overclaims scope')
    require(sha(BASE/'program.json')==p['registryBefore']['sha256'], 'Registry changed during diagnostic')
    for group in library['groups']: runner.bound(group['paths'])
    added=['scripts/summarize-independent-shadow-labels.py','scripts/verify-independent-shadow-labels.py',
           'scripts/finalize-independent-shadow-labels.py','tests/test_independent_shadow_labels_readout.py',
           'tests/test_independent_shadow_labels_verifier.py']
    optional=['scripts/render-independent-shadow-labels.py','scripts/verify-independent-shadow-preview.cjs',
              'scripts/verify-independent-shadow-labels-readout.py',
              'scripts/summarize-independent-shadow-regime-maturity.py',
              'tests/test_independent_shadow_regime_maturity.py']
    added += [f for f in optional if (ROOT/f).exists()]
    for proof in (validation,readout_validation,regime_validation):
        added.extend(proof.get('sourcePins',{}))
    added=sorted(set(added))
    post_pins={}
    for name in added:
        require((ROOT/name).exists(), 'Missing supporting source '+name)
        target=OUT/'sources'/name; target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): require(sha(target)==sha(ROOT/name), 'Snapshot source changed '+name)
        else: shutil.copy2(ROOT/name,target)
        post_pins[name]=sha(target)
    external_helpers=[]
    for index,path in enumerate(helper_paths,1):
        target=OUT/'sources'/'external-presentation'/(str(index)+'-'+path.name)
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): require(sha(target)==sha(path),'Changed external helper snapshot')
        else: shutil.copy2(path,target)
        require(sha(target)==sha(path),'External helper copy differs')
        external_helpers.append({'original':{'path':str(path),'sha256':sha(path)},'archived':ref(target)})
    archive=OUT/'reproduction-sources.zip'
    require(not archive.exists(), 'Source archive already exists')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,strict_timestamps=False) as z:
        for path in sorted((OUT/'sources').rglob('*')):
            if path.is_file(): z.write(path,path.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z: require(z.testzip() is None,'Source archive corrupt')
    program=read(BASE/'program.json')
    count=program['progress']['completedFixedCandidateTrials']
    require(count==39, 'Unexpected candidate trial count')
    program['progress']['independentShadowLabels']={
        'directory':OUT.name, 'status':'completed-development-diagnostic',
        'independentPathExecutions':16310, 'newCandidateAccounts':0, 'candidateTrialsAdded':0,
        'plan':ref(OUT/'plan.json'), 'library':ref(OUT/'library-completion.json'),
        'validation':ref(OUT/'validation.json'), 'readout':ref(OUT/'readout.json'),
        'readoutValidation':ref(OUT/'readout-validation.json'), 'decision':ref(OUT/'decision.json'),
        'regimeMaturity':ref(OUT/'regime-maturity-context.json'),
        'regimeMaturityValidation':ref(OUT/'regime-maturity-validation.json'),
        'prePathTests':ref(OUT/'pre-path-tests.json'),
        'renderReceipt':ref(OUT/'render-receipt.json'),'browserQA':ref(qa_path),'visualReview':ref(OUT/'visual-review.json'),
        'deliveryPath':(OUT/'delivery.json').relative_to(ROOT).as_posix(),
        'certified':False, 'targetAchieved':False}
    program['progress']['nextStage']=decision['nextStage']
    program['progress']['lastTurnClassification']='progress: 16310 independently executed opportunity labels; same-library membership and maturity diagnostic; no new candidate portfolio trial or promotion.'
    program['goalStatus']='active'; program['targetAchieved']=False
    program['updatedAt']=datetime.now(timezone.utc).isoformat()
    program['updatedDate']=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    write(OUT/'program-after.json',program)
    inventory=[{**ref(path),'bytes':path.stat().st_size} for path in sorted(OUT.rglob('*'))
               if path.is_file() and path.name!='delivery.json']
    delivery={'schema':OUT.name+'-delivery','completedAt':datetime.now(timezone.utc).isoformat(),
        'files':inventory,'sourceArchive':ref(archive),'postprocessingSourcePins':post_pins,
        'externalPresentationHelpers':external_helpers,'renderReceipt':ref(OUT/'render-receipt.json'),
        'browserQA':ref(qa_path),'visualReview':ref(OUT/'visual-review.json'),
        'programBeforeSha256':p['registryBefore']['sha256'],'programAfterSha256':sha(OUT/'program-after.json'),
        'candidateTrialCount':39,'independentPathExecutions':16310,'newCandidateAccounts':0,
        'holdoutClaim':False,'certified':False,'targetAchieved':False}
    write(OUT/'delivery.json',delivery)
    for item in inventory: require(sha(ROOT/item['path'])==item['sha256'],'Changed delivery member')
    shutil.copyfile(OUT/'program-after.json',BASE/'program.json')
    require(sha(BASE/'program.json')==delivery['programAfterSha256'],'Registry write differs')
    print(json.dumps({'delivery':ref(OUT/'delivery.json'),'files':len(inventory),'candidateTrials':count,
                      'independentPaths':16310,'newCandidateAccounts':0},ensure_ascii=False))


if __name__=='__main__': main()

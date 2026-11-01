"""Separate Sources receipt for the audited post-hoc amount-level sidecar."""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/board-environment-context-v1'
NODE = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
TOOLS = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')
BOARDS = {'main': '主板', 'chinext': '创业板', 'star': '科创板'}
LIMITS = [
    '按成交额实际观察日A划分：2024-01-02至2024-09-23，以及2024-09-24至2025-12-30。与主环境图按形成日T、仅看到T−1的时钟不同；没有补入2025-12-31成交额。',
    '可观察总额是当日金额有效股票的成交额之和，单位为人民币；表中展示每日总额的区间中位数和后段/前段倍数，不是累计总额、资金净流入或各股滚动20日相对成交额。未知金额不补零。',
    '完整篮子要求股票在全部484个观察日都有有效金额，是利用全期可用性事后选出的固定交集，含未来信息；只用于描述对照，不能充当当时可执行的股票池或入场规则。',
    '可观察总额受每日数据覆盖影响，完整篮子也可能不具代表性。两套金额必须同时结合股票覆盖、篮子股票份额及其占可观察金额的份额阅读，不把完整篮子结果推广至所有股票。',
    '金额继承原有效性规则：缺失、零金额、无效量额或已知停牌可能保留为未知。本次覆盖样本不是历史全市场，板块也不是纯科技行业，不能从成交额推断主力、散户或量化身份。',
    '这是看到阶段环境结果后追加的描述复核，没有新增账户、信号、参数或策略试验。2022—2025均属已检视开发历史，2026未用于本表；量能抬升不证明某个策略稳定小亏大赚。',
]


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path): return json.loads(Path(path).read_text('utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, obj):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, allow_nan=False, indent=2); stream.write('\n')


def finite(x): return type(x) in (int, float) and math.isfinite(x)


def text_pct(x): return '—' if x is None else f'{x * 100:.4f}'


def distribution(value):
    return text_pct(value['min']) + ' / ' + text_pct(value['median'])


def payload(data, method):
    require(data['posthoc'] is True and all(data[k] == 0 for k in ('newSignals', 'newAccounts', 'parameterChanges', 'newStrategyTrials')) and
        not data['holdoutClaim'] and not data['certified'] and not data['targetAchieved'], 'Post-hoc descriptive scope required')
    require(data['method']['observationDate'] == 'asOfDate A, not formationDate T' and data['method']['start'] == '2024-01-02' and
        data['method']['end'] == '2025-12-30' and data['method']['splitAsOfDate'] == '2024-09-24', 'Actual-date scope differs')
    require(data['unit']['amount'] == 'CNY' and data['unit']['scaleApplied'] == 1, 'Source amount unit differs')
    epochs = data['epochRows']; comparisons = data['comparisonRows']
    require(len(epochs) == 6 and len(comparisons) == 3 and
        {(r['board'], r['epoch']) for r in epochs} == {(b, e) for b in BOARDS for e in ('before20240924', 'from20240924')} and
        {r['board'] for r in comparisons} == set(BOARDS), 'All boards/epochs required')
    for r in epochs:
        require(r['observedKnownDays'] + r['observedMissingDays'] == r['dayCount'] and
            0 <= r['balancedMemberCount'] <= r['fixedPopulation'] and 0 <= r['balancedStockShare'] <= 1, 'Population count differs')
        require(math.isclose(r['balancedStockShare'], r['balancedMemberCount'] / r['fixedPopulation']), 'Basket stock share differs')
        require(all(r[k] is None or (finite(r[k]) and r[k] > 0) for k in ('medianObservedTotalCny', 'medianBalancedTotalCny')), 'Missing amount not preserved')
    for r in comparisons:
        before = next(e for e in epochs if e['board'] == r['board'] and e['epoch'] == 'before20240924')
        after = next(e for e in epochs if e['board'] == r['board'] and e['epoch'] == 'from20240924')
        require(r['beforeDayCount'] == before['dayCount'] and r['afterDayCount'] == after['dayCount'] and
            r['beforeDayCount'] + r['afterDayCount'] == 484, 'Comparison date count differs')
        for name in ('Observed', 'Balanced'):
            for prefix, source in (('before', before), ('after', after)):
                require(r[prefix + 'Median' + name + 'TotalCny'] == source['median' + name + 'TotalCny'], 'Comparison amount differs')
            denominator, numerator = r['beforeMedian' + name + 'TotalCny'], r['afterMedian' + name + 'TotalCny']
            ratio = r[name.lower() + 'MedianPostPreRatio']
            require(ratio is None if denominator is None or numerator is None else finite(ratio) and math.isclose(ratio, numerator / denominator), 'Post/pre ratio differs')
    epoch_rows = [{'board': BOARDS[r['board']], 'interval': r['asOfDateFrom'] + ' 至 ' + r['asOfDateThrough'], 'days': r['dayCount'],
        'observedYiCny': None if r['medianObservedTotalCny'] is None else r['medianObservedTotalCny'] / 1e8,
        'balancedYiCny': None if r['medianBalancedTotalCny'] is None else r['medianBalancedTotalCny'] / 1e8,
        'stockCounts': str(r['fixedPopulation']) + ' / ' + str(r['balancedMemberCount']), 'balancedStockSharePct': r['balancedStockShare'] * 100,
        'observedCoverage': distribution(r['observedCoverage']), 'balancedAmountShare': distribution(r['balancedShareOfObservedTotal']),
        'knownMissing': str(r['observedKnownDays']) + ' / ' + str(r['observedMissingDays'])} for r in epochs]
    comparison_rows = [{'board': BOARDS[r['board']], 'observedRatio': r['observedMedianPostPreRatio'], 'balancedRatio': r['balancedMedianPostPreRatio'],
        'beforeAfterDays': str(r['beforeDayCount']) + ' / ' + str(r['afterDayCount']), 'balancedMemberCount': r['balancedMemberCount'],
        'balancedStockSharePct': r['balancedStockShare'] * 100,
        'beforeAfterObservedCoverage': text_pct(r['beforeObservedCoverage']['median']) + ' / ' + text_pct(r['afterObservedCoverage']['median']),
        'beforeAfterAmountShare': text_pct(r['beforeBalancedShareOfObservedTotal']['median']) + ' / ' + text_pct(r['afterBalancedShareOfObservedTotal']['median'])} for r in comparisons]
    source = {'label': '三板固定来源样本的成交额水平', 'files': [
        {'label': 'artifacts/strategy-search/board-environment-context-v1/' + f} for f in ('amount-level-context.json', 'amount-level-independent-check.json', 'plan.json', 'validation.json')],
        'metricDefinitions': [{'label': '可观察日成交额', 'definition': '每天对固定来源股票中有效的实际成交额求和，比较前后区间每日总额的中位数。'},
            {'label': '完整篮子', 'definition': '同板全部484日金额均有效的股票交集，用相同股票集合报告另一套日成交额中位数。'}],
        'filters': ['按实际观察日A在2024-09-24前后分段', '三板全部保留，未知金额不填零', '金额从人民币元换算为亿元'],
        'caveats': LIMITS, 'evidenceFlow': [{'kind': 'validation', 'title': '成交额单位、覆盖及汇总核对',
            'detail': '独立核对原金额证据、完整篮子身份、1452个板块日、六分段及三前后对比；该审查不改变后验样本选择限制。'}]}
    def query(name, rows, cols, code=False):
        value = {'id': name, 'rows': rows, 'columns': [{'field': k, 'label': label} for k, label in cols], 'source': source,
            'reportingPeriod': '实际成交额观察日A：2024-01-02至2025-12-30，以2024-09-24分界'}
        if code: value['methods'] = [{'language': 'python', 'code': method}]
        return value
    result = {'schemaVersion': 1, 'items': [{'id': 'board-amount-level', 'title': '9·24前后：覆盖样本成交额水平', 'queries': [
        query('amount-epochs', epoch_rows, [('board', '板块'), ('interval', '实际观察日期A'), ('days', '日数'), ('observedYiCny', '可观察日总额中位（亿元）'),
            ('balancedYiCny', '完整篮子日总额中位（亿元）'), ('stockCounts', '固定 / 篮子股票数'), ('balancedStockSharePct', '篮子股票份额（%）'),
            ('observedCoverage', '股票覆盖最低 / 中位（%）'), ('balancedAmountShare', '篮子金额份额最低 / 中位（%）'), ('knownMissing', '已知 / 未知日')], True),
        query('amount-comparisons', comparison_rows, [('board', '板块'), ('observedRatio', '可观察额后 / 前（倍）'), ('balancedRatio', '篮子额后 / 前（倍）'),
            ('beforeAfterDays', '前 / 后日数'), ('balancedMemberCount', '篮子股票数'), ('balancedStockSharePct', '篮子股票份额（%）'),
            ('beforeAfterObservedCoverage', '前 / 后股票覆盖中位（%）'), ('beforeAfterAmountShare', '前 / 后篮子金额份额中位（%）')])]}]}
    require(len(json.dumps(result, ensure_ascii=False).encode()) < 1_000_000, 'Oversized projection')
    return result


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ('amount-level-context.json', 'amount-level-independent-check.json')
    data, proof = (read(directory / name) for name in names)
    pins = {name: sha(directory / name) for name in names}
    require(proof['passed'] and proof['contextSha256'] == pins[names[0]], 'Independent amount proof required')
    for item in proof['sourcePins']:
        target = (ROOT / item['path']).resolve(); require(target.is_relative_to(ROOT) and sha(target) == item['sha256'], 'Audited source changed')
    require(any(item['sha256'] == proof['verifierSha256'] for item in proof['sourcePins']), 'Auditor binding missing')
    extractor = ROOT / data['extractor']['path']; require(sha(extractor) == data['extractor']['sha256'] == data['extractorSha256'], 'Extractor changed')
    result = payload(data, extractor.read_text('utf-8'))
    source, target = directory / 'amount-sources.json', directory / 'inline/board-amount-level-sources.html'
    write(source, result)
    renderer = TOOLS / 'render-inline-sources.mjs'
    ran = subprocess.run([NODE, str(renderer), '--input', str(source), '--output', str(target)], capture_output=True, text=True, timeout=60)
    write(directory / 'amount-sources-render-log.json', {'returncode': ran.returncode, 'stdout': ran.stdout, 'stderr': ran.stderr})
    require(ran.returncode == 0, ran.stderr); require(target.stat().st_size < 1_000_000, 'Oversized fragment')
    require(all(sha(directory / name) == value for name, value in pins.items()), 'Analysis changed')
    write(directory / 'amount-render-receipt.json', {'schema': 'board-amount-level-sources-render-v1', 'inputPins': pins, 'builderSha256': sha(__file__),
        'fragments': {'sources': {'path': target.relative_to(directory).as_posix(), 'sha256': sha(target)}},
        'payloads': {'sources': {'path': source.name, 'sha256': sha(source)}}, 'deliveryOrder': ['sources'], 'tableRows': [[6, 3]],
        'rendererSha256': sha(renderer), 'posthoc': True, 'actualMarketRuns': 0, 'analysisArtifactsModified': False, 'corePresentationModified': False})
    return {'renderReceiptSha256': sha(directory / 'amount-render-receipt.json'), 'fragment': str(target), 'fragmentSha256': sha(target)}


def self_test():
    d = {'posthoc': True, 'newSignals': 0, 'newAccounts': 0, 'parameterChanges': 0, 'newStrategyTrials': 0, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False,
        'method': {'observationDate': 'asOfDate A, not formationDate T', 'start': '2024-01-02', 'end': '2025-12-30', 'splitAsOfDate': '2024-09-24'},
        'unit': {'amount': 'CNY', 'scaleApplied': 1}, 'epochRows': [], 'comparisonRows': []}
    for b in BOARDS:
        dist = {'min': .8, 'median': .9}
        for e, n, factor in [('before20240924', 176, 1), ('from20240924', 308, 2)]:
            d['epochRows'].append({'board': b, 'epoch': e, 'asOfDateFrom': '2024-01-02' if factor == 1 else '2024-09-24', 'asOfDateThrough': '2024-09-23' if factor == 1 else '2025-12-30',
                'dayCount': n, 'observedKnownDays': n, 'observedMissingDays': 0, 'fixedPopulation': 100, 'balancedMemberCount': 80, 'balancedStockShare': .8,
                'medianObservedTotalCny': 1e8*factor, 'medianBalancedTotalCny': .8e8*factor, 'observedCoverage': dist, 'balancedShareOfObservedTotal': dist})
        d['comparisonRows'].append({'board': b, 'beforeDayCount': 176, 'afterDayCount': 308, 'beforeMedianObservedTotalCny': 1e8, 'afterMedianObservedTotalCny': 2e8,
            'beforeMedianBalancedTotalCny': .8e8, 'afterMedianBalancedTotalCny': 1.6e8, 'observedMedianPostPreRatio': 2., 'balancedMedianPostPreRatio': 2., 'balancedMemberCount': 80,
            'balancedStockShare': .8, 'beforeObservedCoverage': dist, 'afterObservedCoverage': dist, 'beforeBalancedShareOfObservedTotal': dist, 'afterBalancedShareOfObservedTotal': dist})
    before = deepcopy(d); out = payload(d, '# Synthetic only')
    require(d == before and [len(q['rows']) for q in out['items'][0]['queries']] == [6, 3], 'Complete nonmutating projection')
    require(out['items'][0]['queries'][0]['rows'][0]['observedYiCny'] == 1., 'CNY conversion differs')
    for key in ('clock', 'basket', 'ratio'):
        bad = deepcopy(d)
        if key == 'clock': bad['method']['observationDate'] = 'formationDate T'
        elif key == 'basket': bad['epochRows'][0]['balancedStockShare'] = 1.
        else: bad['comparisonRows'][0]['observedMedianPostPreRatio'] = 3.
        try: payload(bad, '# sample')
        except ValueError: pass
        else: raise AssertionError('Invalid fixture accepted')
    code = "import fs from 'node:fs';import {normalizeInlineSourcesInput} from %s;normalizeInlineSourcesInput(JSON.parse(fs.readFileSync(0,'utf8')));" % json.dumps((TOOLS/'inline-sources-input.mjs').as_uri())
    ran = subprocess.run([NODE, '--input-type=module', '-e', code], input=json.dumps(out, ensure_ascii=False), capture_output=True, text=True, timeout=30)
    require(ran.returncode == 0, ran.stderr)
    return {'passed': True, 'checks': ['six epochs andthree comparisons', 'amount units', 'posthoc basket andA clock', 'three invalid fixtures rejected', 'shared validator andunchanged inputs'], 'filesWritten': 0}


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--directory', type=Path, default=OUT); parser.add_argument('--self-test', action='store_true')
    a = parser.parse_args(); print(json.dumps(self_test() if a.self_test else build(a.directory), ensure_ascii=False))

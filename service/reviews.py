"""Versioned review templates and transparent, evidence-based process scoring."""
from __future__ import annotations

import copy
import calendar
import hashlib
import json
import re
from datetime import date
from pathlib import PurePath

RULE_VERSION = 'account-review-v1'


def _items(prefix, rows):
    return [{'id': f'{prefix}.{key}', 'label': label, 'prompt': prompt} for key, label, prompt in rows]


DIMENSIONS = [
    {'id': 'position', 'label': '仓位控制', 'description': '评价资金配置和暴露，盈利不抵消超仓。', 'items': _items('position', [
        ('budget', '总仓位预算', '记录计划与实际总仓位；是否符合当前市场环境和账户上限？'),
        ('concentration', '个股与板块集中度', '列出最大个股、行业与相关持仓占比；相关风险是否重复叠加？'),
        ('sizing', '单笔规模与分批', '说明单笔风险预算、下单数量、分批条件和实际执行偏差。'),
        ('liquidity', '流动性与资金余量', '记录可用资金、持仓流动性、退出容量和预留资金安排。'),
    ])},
    {'id': 'risk', 'label': '风险与回撤', 'description': '评价事前风险边界和触发后的动作。', 'items': _items('risk', [
        ('invalidation', '失效条件与止损', '入场前是否写明逻辑失效条件、止损位及最大可承受损失？'),
        ('execution', '风险触发执行', '触发止损或风险预警时，实际动作、延迟与偏差是什么？'),
        ('drawdown', '回撤预算约束', '记录当日/周期回撤与预算；超限后是否降低风险或停止交易？'),
        ('event', '事件与隔夜风险', '检查公告、业绩、停牌、跳空和隔夜暴露，记录应对方案。'),
    ])},
    {'id': 'entry', 'label': '买入质量', 'description': '按决策时已知证据评价，不以事后涨跌倒推。', 'items': _items('entry', [
        ('context', '市场与板块匹配', '买入时的大盘状态、板块强弱、个股相对强度是否支持该策略？'),
        ('trigger', '信号与触发条件', '记录买入信号、价格/量能条件、发生时间及可复核证据。'),
        ('payoff', '盈亏比与失效距离', '写明目标、止损距离、预计费用及盈亏比的计算依据。'),
        ('execution', '成交与追价控制', '对照计划价格与实际成交，解释滑点、追价和分批质量。'),
    ])},
    {'id': 'exit', 'label': '卖出质量', 'description': '评价退出依据、时机和仓位处理。', 'items': _items('exit', [
        ('plan', '退出规则预设', '是否在入场前确定止盈、止损、时间止损与分批退出规则？'),
        ('trigger', '退出信号识别', '退出时的价格、趋势、量能或逻辑变化是否符合规则？'),
        ('execution', '退出纪律与成交', '记录计划与实际退出价格/数量，是否犹豫、抢跑或报复性卖出？'),
        ('remainder', '剩余仓位与回合闭环', '卖出后剩余仓位的风险、继续持有条件、回合结束结论是什么？'),
    ])},
    {'id': 'discipline', 'label': '计划与执行', 'description': '评价可复现的交易流程和偏差修正。', 'items': _items('discipline', [
        ('preparation', '盘前准备', '是否完成市场假设、关注名单、操作条件与备用方案？'),
        ('consistency', '计划一致性', '逐笔核对计划内/外操作；临时变更有无当时证据和理由？'),
        ('record', '记录完整与可核验', '日期、方向、数量、费用、理由和截图/引用是否完整准确？'),
        ('improvement', '改进项落实', '上一轮改进动作是否执行？本轮问题是否形成可检查的下一步？'),
    ])},
    {'id': 'emotion', 'label': '情绪与过程', 'description': '评价决策环境和行为控制，不评价个人价值。', 'items': _items('emotion', [
        ('awareness', '情绪识别', '记录交易前中后的情绪、身体状态、强度和具体触发事件。'),
        ('impulse', '冲动与频率控制', '是否出现追涨、报复性交易、频繁改单或偏离计划的加仓？'),
        ('bias', '偏见与反证', '是否主动检查确认偏误、成本锚定、侥幸和对立证据？'),
        ('recovery', '暂停与恢复机制', '情绪或连续亏损触发时是否暂停、复核并按规则恢复？'),
    ])},
]


def _section(section_id, label, rows):
    return {'id': section_id, 'label': label, 'fields': [{'id': key, 'label': title, 'prompt': prompt, 'type': 'text'} for key, title, prompt in rows]}


MARKET = _section('market', '市场环境与板块', [
    ('marketState', '市场阶段与指数', '记录指数趋势、关键位、量能、涨跌家数及判断依据；未接行情的数据请注明来源。'),
    ('marketExpectation', '预期与实际差异', '盘前假设是什么？哪些证据确认或推翻了它？'),
    ('sectorRotation', '板块轮动与主线', '主线、轮动、领涨/补涨、资金承接与退潮信号分别是什么？'),
    ('eventCalendar', '事件与风险日历', '政策、业绩、公告、解禁、宏观事件及需要核验的时间点。'),
])
HOLDINGS = _section('holdings', '账户与持仓逐项复核', [
    ('accountSnapshot', '账户快照与数据来源', '记录现金、持仓、仓位及估值时间；将市场损益与入出金分开。'),
    ('positionTheses', '逐只持仓逻辑', '每只持仓：原逻辑、当前证据、失效条件、持有/减仓/退出动作。'),
    ('concentration', '相关性与集中风险', '最大个股/板块暴露，相关持仓是否重复承担同类风险？'),
    ('riskBudget', '风险预算与回撤', '风险上限、实际回撤、最差场景、止损/降仓触发和执行人。'),
])
TRADES = _section('trades', '逐笔交易与决策证据', [
    ('tradeRationale', '逐笔决策及证据', '逐笔写明交易ID、时间、当时可知证据、买卖理由、策略标签和截图引用。'),
    ('planComparison', '计划与成交对照', '计划价/数量/条件与实际成交逐笔对照，列出费用、滑点和偏差原因。'),
    ('exitReview', '退出与回合追踪', '每笔退出符合哪条规则？剩余仓位如何处理？完整回合是否已经结束？'),
    ('counterEvidence', '反证与替代动作', '当时有哪些反证？保持不动、等待或更小仓位是否更符合规则？'),
])
PROCESS = _section('process', '情绪、纪律与改进', [
    ('emotionalTimeline', '情绪时间线', '盘前、盘中、盘后情绪强度；具体触发点、行为后果与冷静动作。'),
    ('disciplineDeviations', '纪律偏差清单', '追涨、扛亏、超仓、临时交易等逐项记录；无偏差也说明核对范围。'),
    ('lessons', '做对与做错', '区分好过程/坏结果与坏过程/好结果，找出可重复和必须停止的行为。'),
    ('improvementActions', '可检查的改进动作', '每项改进写动作、触发条件、截止时间和检查标准。'),
])
NEXT = _section('next', '次日/下一周期计划', [
    ('nextScenarios', '多情景推演', '强势、震荡、转弱分别给出证据和应对；避免只有单向预测。'),
    ('nextWatchlist', '观察名单与触发条件', '逐只列出关注原因、买入条件、失效条件和不操作条件。'),
    ('nextPositionPlan', '仓位与风险预案', '下一周期总仓位上限、单笔风险、止损、减仓及暂停交易条件。'),
    ('nextChecklist', '执行检查单', '列出开盘前、盘中、收盘后检查事项与优先级。'),
])
ATTRIBUTION = _section('attribution', '周期归因与系统迭代', [
    ('returnAttribution', '损益与资金流归因', '分离入出金、已实现/未实现损益、费用；标明价格来源和覆盖范围。'),
    ('strategyAttribution', '策略/板块/标的贡献', '按策略、板块、个股和持有周期拆分贡献，避免只看总收益。'),
    ('tradeQuality', '交易样本与胜负分布', '交易数量、盈亏比、胜率、回撤、持仓时长；写明样本量与统计不足。'),
    ('processPatterns', '过程模式与重复错误', '哪些情绪、时段、信号或仓位行为反复导致偏差？'),
    ('ruleChanges', '规则调整与验证计划', '记录准备修改的规则、依据、预期作用和后续回测验证方式。'),
])
TEMPLATES = [
    {'id': 'daily', 'kind': 'daily', 'label': '每日复盘', 'sections': [MARKET, HOLDINGS, TRADES, PROCESS, NEXT]},
    {'id': 'weekly', 'kind': 'weekly', 'label': '每周复盘', 'sections': [MARKET, HOLDINGS, ATTRIBUTION, PROCESS, NEXT]},
    {'id': 'monthly', 'kind': 'monthly', 'label': '每月复盘', 'sections': [MARKET, HOLDINGS, ATTRIBUTION, PROCESS, NEXT]},
    {'id': 'trade', 'kind': 'trade', 'label': '逐笔/回合复盘', 'sections': [
        _section('setup', '交易背景与原始计划', [
            ('setupContext', '市场与策略背景', '记录大盘/板块状态、采用的策略及当时可获得的信息。'),
            ('setupThesis', '交易逻辑与反证', '说明机会、触发条件、反对理由及逻辑失效条件。'),
            ('setupRisk', '计划价量与风险收益', '计划买卖价、数量、分批、止损、止盈、风险预算和预计费用。'),
        ]), TRADES, HOLDINGS, PROCESS,
        _section('outcome', '结果与过程分离', [
            ('actualOutcome', '实际结果与计算口径', '已实现收益、费用、持有天数、最大不利波动；未结束回合注明未完成。'),
            ('decisionQuality', '当时决策质量', '仅依据决策时已知信息，判断过程是否符合规则，不用事后行情打分。'),
            ('nextExperiment', '下一次验证动作', '给出保留/取消/调整的动作及后续观察方法。'),
        ]),
    ]},
    {'id': 'idea', 'kind': 'idea', 'label': '盘前预案/交易想法', 'sections': [MARKET,
        _section('hypothesis', '假设与可证伪条件', [
            ('ideaThesis', '核心假设', '这次机会依赖哪些事实？哪些只是猜测？'),
            ('ideaEvidence', '证据与来源', '列出时间、数据来源、图表/公告引用和反对证据。'),
            ('ideaTrigger', '触发与放弃条件', '只有发生什么才交易？何种变化意味着取消计划？'),
            ('ideaRisk', '风险预算与规模', '计划仓位、止损、止盈、最大损失、流动性与事件风险。'),
            ('ideaValidation', '验证方法与观察期限', '说明何时检查、用什么样本/数据验证，避免把想法直接当策略结论。'),
        ]), NEXT]},
]

REVIEW_SCHEMA = {
    'version': RULE_VERSION, 'rubricVersion': RULE_VERSION,
    'label': '交易过程复盘 · 暂定第一版',
    'description': '六个维度、24 个等权子项；仅评价交易过程。规则将在策略、回测和市场研究完成后迭代。',
    'dimensions': DIMENSIONS, 'templates': TEMPLATES,
    'scoreScale': [
        {'value': 0, 'label': '明显违背规则'}, {'value': 1, 'label': '多数未做到'},
        {'value': 2, 'label': '部分做到'}, {'value': 3, 'label': '基本做到'},
        {'value': 4, 'label': '充分做到且有证据'},
    ],
    'scoringRules': '每项 0–4 分并填写证据；未评分或无证据仍占分母但不得分。不适用必须说明原因后才排除。总分与覆盖率同时展示；盈利不参与过程评分。',
    'resultMetricsSeparate': True,
}
ITEM_IDS = {item['id'] for dimension in DIMENSIONS for item in dimension['items']}
KINDS = {template['id'] for template in TEMPLATES}


def review_schema():
    return copy.deepcopy(REVIEW_SCHEMA)


def _score_entries(scores, notes=None):
    if scores is None:
        scores = {}
    if not isinstance(scores, dict):
        raise ValueError('scores 必须是对象。')
    if notes is not None and not isinstance(notes, dict):
        raise ValueError('scoreNotes 必须是对象。')
    unknown = set(scores) - ITEM_IDS
    if unknown:
        raise ValueError('无法识别的评分项：' + ', '.join(sorted(unknown)))
    normalized = {}
    for key, entry in scores.items():
        if entry is None:
            continue
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            entry = {'score': entry}
        if not isinstance(entry, dict):
            raise ValueError(f'{key} 评分格式无效。')
        score = entry.get('score')
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or score not in range(5)):
            raise ValueError(f'{key} 必须是 0–4 整数或留空。')
        evidence = entry.get('evidence', (notes or {}).get(key, ''))
        if not isinstance(evidence, str) or len(evidence) > 20_000:
            raise ValueError(f'{key} 证据须为不超过 20000 字的文本。')
        na = entry.get('notApplicable', False)
        if not isinstance(na, bool):
            raise ValueError(f'{key} 不适用标记必须为布尔值。')
        if na and score is not None:
            raise ValueError(f'{key} 不能同时评分和标记不适用。')
        normalized[key] = {'score': int(score) if score is not None else None, 'notApplicable': na, 'evidence': evidence.strip()}
    return normalized


def calculate_score(scores, notes=None):
    """Missing evidence cannot yield full marks. N/A without a reason stays unassessed."""
    entries = _score_entries(scores, notes)
    dimensions = []
    total_earned = assessed = applicable = excluded = evidenced = entered = 0
    warnings = []
    missing_ids = []
    for dimension in DIMENSIONS:
        earned = count = rated = evidence_count = na_count = 0
        for item in dimension['items']:
            key = item['id']
            entry = entries.get(key, {})
            has_evidence = bool(entry.get('evidence'))
            if entry.get('notApplicable') and has_evidence:
                na_count += 1
                continue
            count += 1
            if entry.get('notApplicable'):
                warnings.append(f'{item["label"]} 标记不适用但缺少原因，暂按未填写处理。')
            if entry.get('score') is not None:
                entered += 1
            if has_evidence:
                evidence_count += 1
            if entry.get('score') is not None and has_evidence:
                rated += 1
                earned += entry['score']
            else:
                missing_ids.append(key)
                if entry.get('score') is not None:
                    warnings.append(f'{item["label"]} 缺少评分证据，暂不计入已完成评分。')
        dimensions.append({'id': dimension['id'], 'label': dimension['label'], 'score': round(earned / (count * 4) * 100, 1) if count and rated else None,
                           'assessedScore': round(earned / (rated * 4) * 100, 1) if rated else None,
                           'coverage': round(rated / count * 100, 1) if count else None,
                           'ratedCount': rated, 'applicableCount': count, 'notApplicableCount': na_count, 'earnedPoints': earned, 'possiblePoints': count * 4})
        total_earned += earned
        assessed += rated
        applicable += count
        evidenced += evidence_count
        excluded += na_count
    return {
        'version': RULE_VERSION, 'rubricVersion': RULE_VERSION,
        'score': round(total_earned / (applicable * 4) * 100, 1) if applicable and assessed else None,
        'assessedScore': round(total_earned / (assessed * 4) * 100, 1) if assessed else None,
        'coverage': round(assessed / applicable * 100, 1) if applicable else None,
        'evidenceCoverage': round(evidenced / applicable * 100, 1) if applicable else None,
        'totalCount': 24, 'ratedCount': assessed, 'enteredCount': entered, 'applicableCount': applicable,
        'notApplicableCount': excluded, 'missingCount': applicable - assessed,
        'earnedPoints': total_earned, 'possiblePoints': applicable * 4,
        'isComplete': applicable > 0 and assessed == applicable,
        'dimensions': dimensions, 'missingItemIds': missing_ids, 'warnings': warnings,
        'explanation': '过程分 = 有证据的已评项目得分 / 全部适用项目满分 × 100；空白项不计得分，不适用且有原因的项目排除。收益不参与评分。',
    }


def score_review(review):
    return calculate_score(review.get('scores', {}), review.get('scoreNotes'))


def validate_review(review):
    if not isinstance(review, dict):
        raise ValueError('复盘必须是对象。')
    result = copy.deepcopy(review)
    kind = result.get('kind', result.get('type', 'daily'))
    if kind not in KINDS:
        raise ValueError('复盘类型必须是 daily/weekly/monthly/trade/idea。')
    version = result.get('rubricVersion', RULE_VERSION)
    if version != RULE_VERSION:
        raise ValueError('评分规则版本不支持，请先转换为当前版本，原文可保留为附件正文。')
    for key in ('date', 'endDate'):
        raw = result.get(key, result.get('date'))
        try:
            if not isinstance(raw, str) or date.fromisoformat(raw).isoformat() != raw:
                raise ValueError()
        except ValueError as exc:
            raise ValueError(f'{key} 必须是有效的 YYYY-MM-DD 日期。') from exc
        result[key] = raw
    if result['endDate'] < result['date']:
        raise ValueError('复盘结束日期不能早于开始日期。')
    title = result.get('title', '')
    if not isinstance(title, str) or len(title) > 200:
        raise ValueError('标题须为不超过 200 字的文本。')
    fields = result.get('fields', result.get('notes', {}))
    if not isinstance(fields, dict) or len(fields) > 100:
        raise ValueError('复盘字段必须是对象且最多 100 项。')
    if result.get('body'):
        fields.setdefault('importedText', result['body'])
    for key, value in fields.items():
        if not isinstance(key, str) or len(key) > 100 or not isinstance(value, str) or len(value) > 50_000:
            raise ValueError('复盘字段须为文本，每项不超过 50000 字。')
    if sum(len(value) for value in fields.values()) > 500_000:
        raise ValueError('复盘正文总长度超过限制。')
    ids = result.get('relatedTradeIds', result.get('tradeIds', []))
    if not isinstance(ids, list) or len(ids) > 1000 or any(isinstance(i, bool) or not isinstance(i, (str, int)) for i in ids):
        raise ValueError('关联交易须为最多 1000 个交易 ID 的列表。')
    result.update({'kind': kind, 'title': title.strip(), 'fields': fields, 'scores': _score_entries(result.get('scores', {}), result.get('scoreNotes')), 'relatedTradeIds': list(dict.fromkeys(ids)), 'rubricVersion': RULE_VERSION})
    result['score'] = score_review(result)
    return result


LEGACY_FIELDS = {
    'market_observation': 'marketState', 'decision_review': 'tradeRationale', 'mistakes': 'lessons',
    'plan': 'planComparison', 'next_plan': 'nextChecklist', 'next_market_view': 'nextScenarios',
    'next_position_plan': 'nextPositionPlan', 'next_risk_plan': 'riskBudget',
    'did_well': 'lessons', 'did_wrong': 'disciplineDeviations', 'next_strategy': 'nextChecklist',
    'right_things': 'lessons', 'wrong_things': 'disciplineDeviations', 'market_review': 'marketState',
    'system_iteration': 'ruleChanges', 'next_goal': 'nextChecklist',
}


def parse_review_import(filename, raw):
    from .imports import _decode
    text, _ = _decode(raw)
    warnings = []
    if PurePath(filename).suffix.lower() in ('.md', '.markdown', '.txt'):
        found = re.search(r'\b(20\d\d-[01]\d-[0-3]\d)\b', filename + '\n' + text[:500])
        title = next((line.lstrip('# ').strip() for line in text.splitlines() if line.strip()), PurePath(filename).stem)[:200]
        data = [{'kind': 'daily', 'date': found.group(1) if found else '', 'title': title, 'fields': {'importedText': text}, 'scores': {}}]
        warnings.append('Markdown 原文已完整保留为正文；请核对复盘类型、日期和关联交易。不会自动从文字推断评分。')
    elif PurePath(filename).suffix.lower() == '.json':
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError('复盘 JSON 格式无效。') from exc
        if isinstance(data, dict):
            if 'reviews' in data:
                data = data['reviews']
            elif any(key in data for key in ('daily_reviews', 'weekly_reviews', 'monthly_reviews')):
                data = [dict(item, kind=kind) for key, kind in [('daily_reviews', 'daily'), ('weekly_reviews', 'weekly'), ('monthly_reviews', 'monthly')] for item in data.get(key, []) if isinstance(item, dict)]
                warnings.append('已读取参考项目复盘备份；旧六维评分仅保留原文，不强行转换为 24 项评分。')
            else:
                data = [data]
    else:
        raise ValueError('复盘导入支持 JSON、Markdown 或 TXT。')
    if not isinstance(data, list) or len(data) > 1000:
        raise ValueError('复盘导入需为对象/数组，单次最多 1000 篇。')
    rows = []
    for number, item in enumerate(data, 1):
        errors, row_warnings = [], []
        if not isinstance(item, dict):
            item = {}
            errors.append('此行不是复盘对象。')
        item = copy.deepcopy(item)
        if 'record' in item and isinstance(item['record'], dict):
            item = item['record']
        original = copy.deepcopy(item)
        legacy = any(key in item for key in LEGACY_FIELDS) or 'review_date' in item or ('year' in item and ('month' in item or 'week' in item))
        if legacy and 'date' not in item and isinstance(item.get('year'), int):
            try:
                if item.get('kind') == 'weekly' and isinstance(item.get('week'), int):
                    item['date'] = date.fromisocalendar(item['year'], item['week'], 1).isoformat()
                    item['endDate'] = date.fromisocalendar(item['year'], item['week'], 7).isoformat()
                elif item.get('kind') == 'monthly' and isinstance(item.get('month'), int):
                    year, month = item['year'], item['month']
                    item['date'] = date(year, month, 1).isoformat()
                    item['endDate'] = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
            except ValueError:
                errors.append('原复盘年份/周号/月号无效。')
        item.setdefault('date', item.get('review_date', item.get('week_start', item.get('month', ''))))
        if re.fullmatch(r'\d{4}-\d{2}', str(item['date'])):
            item['date'] += '-01'
        fields = item.get('fields', {})
        if not isinstance(fields, dict):
            fields = {}
        if legacy:
            fields['importedOriginalRecord'] = json.dumps(original, ensure_ascii=False, indent=2)
            row_warnings.append('原复盘完整记录已保留在导入原文，便于核对未映射内容。')
        for old, new in LEGACY_FIELDS.items():
            if isinstance(item.get(old), str) and item[old]:
                fields[new] = '\n'.join(filter(None, [fields.get(new, ''), item[old]]))
        item['fields'] = fields
        scores = item.get('scores', {})
        if isinstance(scores, str):
            try:
                scores = json.loads(scores)
            except json.JSONDecodeError:
                scores = {'legacy': scores}
        if isinstance(scores, dict) and set(scores) - ITEM_IDS:
            fields['importedLegacyScores'] = json.dumps(scores, ensure_ascii=False, indent=2)
            item['scores'] = {}
            row_warnings.append('原评分规则不同，原分数保留为正文，当前 24 项尚未评分。')
        else:
            item['scores'] = scores
        try:
            item = validate_review(item)
        except ValueError as exc:
            errors.append(str(exc))
        rows.append({'rowNumber': number, 'index': number - 1, 'record': item, 'entry': item, 'values': item, 'errors': errors, 'warnings': row_warnings, 'selected': not errors})
    return {'kind': 'reviews', 'fileName': filename, 'fileHash': hashlib.sha256(raw).hexdigest(), 'source': 'review-import', 'columns': [], 'rows': rows, 'warnings': warnings, 'summary': {'total': len(rows), 'valid': sum(not row['errors'] for row in rows), 'invalid': sum(bool(row['errors']) for row in rows)}}


def export_reviews(reviews):
    return json.dumps({'format': 'a-trade-reviews', 'version': 1, 'rubricVersion': RULE_VERSION, 'reviews': [validate_review(review) for review in reviews]}, ensure_ascii=False, indent=2)

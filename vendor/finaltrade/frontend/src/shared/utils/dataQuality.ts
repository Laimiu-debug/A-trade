type QualityNotice = { type: 'warning' | 'info'; title: string; description: string }

const FLOAT_SHARES_NOTICE: QualityNotice = {
  type: 'warning', title: '部分股票缺少流通股本',
  description: '这些股票无法计算可靠的换手率，可能影响换手率条件的筛选与排名。日线价格仍来自本地行情。请在通达信补全股本资料后，重新加载输入池并运行筛选。',
}
const NOTICES: Record<string, QualityNotice> = {
  PARTIAL_FLOAT_SHARES_MISSING: FLOAT_SHARES_NOTICE,
  FLOAT_SHARES_NOT_FOUND: FLOAT_SHARES_NOTICE,
  FLOAT_SHARES_DBF_NOT_FOUND: { ...FLOAT_SHARES_NOTICE, title: '未找到流通股本资料', description: '行情目录缺少 base.dbf 股本资料，无法计算可靠的换手率。请在通达信补全股本资料，并确认行情目录包含 T0002/hq_cache/base.dbf，再重新加载输入池。' },
  FLOAT_SHARES_DBF_READ_FAILED: { ...FLOAT_SHARES_NOTICE, title: '流通股本资料读取失败' },
  FLOAT_SHARES_DBF_INVALID: { ...FLOAT_SHARES_NOTICE, title: '流通股本资料格式异常' },
  FLOAT_SHARES_DBF_EMPTY: { ...FLOAT_SHARES_NOTICE, title: '流通股本资料为空' },
  FLOAT_SHARES_FIELDS_MISSING: { ...FLOAT_SHARES_NOTICE, title: '股本文件缺少所需字段' },
  INPUT_POOL_CACHE_HIT: { type: 'info', title: '使用已缓存的股票池', description: '本次复用本地缓存以加快加载，本身不代表数据缺失。请核对所选日期，需要重新读取时可刷新。' },
  INPUT_POOL_LOAD_TIMEOUT_PARTIAL: { type: 'warning', title: '部分股票尚未加载完成', description: '行情读取超时，当前仅展示已加载部分，可能遗漏其他股票。请重新加载输入池，完成后再运行筛选。' },
  INPUT_POOL_LOAD_TIMEOUT: { type: 'warning', title: '股票池加载超时', description: '未能及时读取行情，请检查本地行情目录后重新加载输入池。' },
  FULL_MARKET_SYSTEM_LIMIT_HIT: { type: 'warning', title: '本次扫描未覆盖全部股票', description: '股票数量超过扫描保护上限，当前结果只覆盖已扫描的部分。请缩小市场范围后重新运行。' },
  FULL_MARKET_TDX_UNAVAILABLE_FALLBACK_CANDLES: { type: 'warning', title: '全市场行情不可用，扫描范围已缩小', description: '当前只扫描已有日线记录的股票，并未覆盖全市场。请检查本地行情目录、补全日线后重新运行。' },
  PARTIAL_FLOAT_SHARES_FROM_CACHE: { type: 'warning', title: '部分股票使用缓存股本资料', description: '部分换手率计算使用缓存的股本资料，可能影响相关条件的筛选与排名。请补全最新股本资料后重新加载输入池。' },
  FLOAT_SHARES_CACHE_USED: { type: 'warning', title: '使用缓存股本资料', description: '换手率计算使用缓存股本，请核对资料日期。需要更新时，请补全股本资料后重新加载输入池。' },
  PARTIAL_CACHE_FALLBACK: { type: 'warning', title: '部分数据使用缓存', description: '部分来源暂不可用，当前结果复用了缓存。请核对结果日期，补全数据后重新运行。' },
  TDX_PATH_NOT_FOUND: { type: 'warning', title: '未找到本地行情目录', description: '请在数据设置中选择有效的通达信行情目录，再重新加载股票池。' },
  TDX_VALID_SERIES_NOT_FOUND: { type: 'warning', title: '未找到可用日线', description: '当前行情目录没有符合要求的日线记录，请补全所选市场与日期的行情后重新运行。' },
  FULL_MARKET_SCAN_EMPTY: { type: 'warning', title: '全市场扫描结果为空', description: '请检查行情目录、所选市场与日期，再重新加载股票池。' },
  TREND_POOL_RUN_NOT_FOUND: { type: 'warning', title: '原选股任务已不可用', description: '请重新加载选股池，运行筛选后再绑定新的筛选任务。' },
}

export function describeDataQuality(reason?: string | null): QualityNotice {
  const codes = [...new Set(String(reason || '').split(/[;|]/).map(code => code.trim()).filter(Boolean))]
  const notices = codes.map(code => NOTICES[code] ?? (/^TREND_POOL_.+_EMPTY$/.test(code)
    ? { type: 'warning' as const, title: '所选阶段的股票池为空', description: '请检查选股漏斗对应阶段的筛选条件，重新筛选后再运行策略。' }
    : { type: 'warning' as const, title: '部分数据暂不可用', description: '当前结果存在未确认的数据限制，请展开技术详情查看原因，核对行情来源后重新运行。' }))
  const warnings = notices.filter(notice => notice.type === 'warning')
  const selected = warnings.length ? warnings : notices
  return {
    type: warnings.length || !notices.length ? 'warning' : 'info',
    title: selected[0]?.title ?? '部分数据暂不可用',
    description: [...new Set(selected.map(notice => notice.description))].join(' ') || '系统未提供具体原因，请核对行情来源和结果日期后重新运行。',
  }
}

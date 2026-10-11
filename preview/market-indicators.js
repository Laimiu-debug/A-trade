(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) (root.AT || (root.AT = {})).marketIndicators = api;
})(typeof window === 'object' ? window : null, function () {
  'use strict';

  // Formula provenance and the few stricter data guards are documented in
  // docs/market-indicators.md. Every output uses only this row and earlier rows.
  const VERSION = 'finaltrade-observations-v1';
  const CHART = Object.freeze({
    windows: Object.freeze([40, 30, 20]), rangeMin: 0.06, rangeMax: 0.35,
    breakoutVolume: 1.5, closeStrength: 0.45, wickMax: 0.55,
    hold: 0.97, retestBand: 0.04, shrink: 0.75, upDownRatio: 1.3,
    volumeLookback: 20, holdDays: 2, maxAge: 18, minScore: 50,
    boxShrinkMax: 1.05, retestDays: 5, afterBreakout: 2,
  });
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, low = 0, high = 100) => Math.max(low, Math.min(high, value));
  const mean = values => values.length ? values.reduce((sum, value) => sum + value / values.length, 0) : null;
  const safe = value => number(value) ? value : null;
  const ratio = (value, base) => number(value) && number(base) && base > 0 ? safe(value / base) : null;
  const round = (value, digits = 4) => number(value) ? Number(value.toFixed(digits)) : null;
  const state = (current, previous) => current > previous ? 'rising' : current < previous ? 'falling' : 'flat';
  const maAt = (values, index, window) => mean(values.slice(Math.max(0, index - window + 1), index + 1));
  const volumeRatio = (volumes, index, window) => ratio(volumes[index], maAt(volumes, index - 1, window));
  const blanks = () => ({
    ma5: null, ma10: null, ma20: null, volumeMA5: null, volumeRatio5: null,
    mainForce: null, retailForce: null, prevMainForce: null, prevRetailForce: null,
    prev2MainForce: null, mainForceState: null, prevMainForceState: null,
    forceGap: null, forceDominance: null, goldenCross: false, purpleToYellow: false,
    volumeConversion: false, boxHigh: null, boxLow: null, boxStartIndex: null,
    boxEndIndex: null, breakoutIndex: null, boxBreakout: false,
    boxConfirmed: false, boxSignal: false, boxScore: null, chart: null,
  });

  function evaluateBox(highs, lows, closes, volumes, end, window) {
    const start = end - window + 1;
    if (start < 0) return null;
    const boxHigh = Math.max(...closes.slice(start, end + 1));
    const boxLow = Math.min(...lows.slice(start, end + 1));
    const range = ratio(Math.max(...highs.slice(start, end + 1)) - boxLow, boxLow);
    if (range === null || range < CHART.rangeMin || range > CHART.rangeMax) return null;
    const half = Math.floor(window / 2);
    const shrink = ratio(mean(volumes.slice(start + half, end + 1)), mean(volumes.slice(start, start + half)));
    if (shrink === null || shrink > CHART.boxShrinkMax) return null;
    return { boxHigh, boxLow, boxRange: range, boxWindow: window, boxShrink: shrink, boxStartIndex: start, boxEndIndex: end };
  }

  function findSetup(opens, highs, lows, closes, volumes, today) {
    const floor = Math.max(40, today - CHART.maxAge);
    for (let index = today; index >= floor; index -= 1) {
      for (const window of CHART.windows) {
        const box = evaluateBox(highs, lows, closes, volumes, index - 1, window);
        if (!box || closes[index] <= box.boxHigh * 1.003) continue;
        const vr = volumeRatio(volumes, index, 20);
        const span = highs[index] - lows[index];
        const strength = ratio(closes[index] - lows[index], span);
        const wick = ratio(highs[index] - Math.max(opens[index], closes[index]), span);
        if (vr === null || strength === null || wick === null) continue;
        const dump = wick >= CHART.wickMax && vr >= 2 && closes[index] < opens[index];
        if (vr >= CHART.breakoutVolume && strength >= CHART.closeStrength && !dump) {
          return { ...box, breakoutIndex: index, breakoutVolumeRatio: vr, closeStrength: strength, upperWickRatio: wick };
        }
      }
    }
    return null;
  }

  function scoreChart(setup, health, confirmation, today) {
    const offset = today - setup.breakoutIndex;
    // The original scorer consumes the calculator's four-decimal metadata.
    const vr = round(setup.breakoutVolumeRatio);
    const rangeScore = clamp(100 - Math.abs(round(setup.boxRange) - 0.15) / 0.12 * 100);
    const breakoutScore = vr <= 2.2
      ? clamp((vr - CHART.breakoutVolume) / (2.2 - CHART.breakoutVolume) * 100)
      : clamp(100 - (vr - 2.2) / 1.8 * 80);
    const freshness = offset <= 1 ? 35 : offset <= 10
      ? clamp(100 - Math.abs(offset - 6) / 6 * 35)
      : clamp(55 - (offset - 10) / (CHART.maxAge - 10) * 45);
    const pullback = confirmation.type === 'pullback_retest';
    const parts = {
      range: rangeScore, breakout: breakoutScore,
      boxVolumeShrink: clamp((1.05 - round(setup.boxShrink)) / 0.35 * 100),
      volumePrice: clamp((round(health.upDownVolumeRatio) - CHART.upDownRatio) / 1.5 * 100),
      candleQuality: clamp(round(setup.closeStrength) * 100),
      boxLength: clamp((setup.boxWindow - 20) / 20 * 100),
      freshness,
      retest: pullback ? clamp(100 - round(confirmation.retestDistance) / CHART.retestBand * 100) : 50,
      pullbackShrink: pullback ? clamp((CHART.shrink - round(confirmation.pullbackShrinkRatio)) / CHART.shrink * 100) : 50,
      confirmBonus: pullback ? 8 : 3,
    };
    const score = clamp(parts.range * 0.10 + parts.breakout * 0.16 + parts.boxVolumeShrink * 0.14
      + parts.volumePrice * 0.28 + parts.candleQuality * 0.06 + parts.boxLength * 0.06
      + parts.freshness * 0.10 + parts.retest * 0.06 + parts.pullbackShrink * 0.06 + parts.confirmBonus);
    return { score: round(score, 2), scoreBreakdown: Object.fromEntries(Object.entries(parts).map(([key, value]) => [key, round(value, 2)])) };
  }

  function chartAt(opens, highs, lows, closes, volumes, today) {
    if (today < 79) return null;
    const setup = findSetup(opens, highs, lows, closes, volumes, today);
    if (!setup) return { available: true, signal: false, rejectReason: 'no_chart_setup' };
    let upVolume = 0;
    let downVolume = 0;
    for (let index = Math.max(1, today - CHART.volumeLookback + 1); index <= today; index += 1) {
      if (closes[index] >= closes[index - 1]) upVolume += volumes[index];
      else downVolume += volumes[index];
    }
    const upDownVolumeRatio = ratio(upVolume, downVolume);
    const ret5 = ratio(closes[today] - closes[today - 5], closes[today - 5]);
    const currentVolume5 = maAt(volumes, today, 5);
    const previousVolume5 = maAt(volumes, today - 5, 5);
    const divergence = ret5 > 0.03 && previousVolume5 > 0 && currentVolume5 < previousVolume5 * 0.75;
    const health = { upDownVolumeRatio, ret5, volumeDivergence: divergence };
    const volumePriceOK = upDownVolumeRatio !== null && upDownVolumeRatio >= CHART.upDownRatio && !divergence;
    const ma5 = maAt(closes, today, 5);
    const ma10 = maAt(closes, today, 10);
    const ma20 = maAt(closes, today, 20);
    const maBullish = ma5 > ma10 && ma10 > ma20 && closes[today] > ma20;
    const confirmation = { type: '', retestDistance: null, pullbackShrinkRatio: null, retestIndex: null };
    if (today - setup.breakoutIndex >= CHART.afterBreakout) {
      let retest = null;
      for (let index = Math.max(setup.breakoutIndex + 1, today - CHART.retestDays + 1); index <= today; index += 1) {
        if (lows[index] >= setup.boxHigh * (1 - CHART.retestBand)
          && lows[index] <= setup.boxHigh * (1 + CHART.retestBand)
          && (retest === null || lows[index] < lows[retest])) retest = index;
      }
      if (retest !== null) {
        confirmation.retestIndex = retest;
        confirmation.retestDistance = ratio(Math.abs(lows[retest] - setup.boxHigh), setup.boxHigh);
        confirmation.pullbackShrinkRatio = ratio(volumes[today], currentVolume5);
        if (confirmation.pullbackShrinkRatio !== null && confirmation.pullbackShrinkRatio <= CHART.shrink
          && closes[today] >= setup.boxHigh * CHART.hold) confirmation.type = 'pullback_retest';
      }
    }
    if (!confirmation.type && today - setup.breakoutIndex >= CHART.afterBreakout + CHART.holdDays - 1) {
      let holds = true;
      for (let index = today - CHART.holdDays + 1; index <= today; index += 1) {
        const vr = volumeRatio(volumes, index, 5);
        if (closes[index] < setup.boxHigh * CHART.hold
          || (closes[index] < opens[index] && (vr === null || vr > 1.3))) holds = false;
      }
      if (holds) confirmation.type = 'hold_above';
    }
    const qualified = Boolean(confirmation.type && volumePriceOK && maBullish);
    const scored = qualified ? scoreChart(setup, health, confirmation, today) : { score: null, scoreBreakdown: null };
    const signal = qualified && scored.score >= CHART.minScore;
    return {
      available: true, ...setup, ...health, ...confirmation, ...scored,
      breakoutOffset: today - setup.breakoutIndex, maBullish, volumePriceOK, signal,
      grade: signal ? scored.score >= 75 ? 'A' : scored.score >= 62 ? 'B' : 'C' : null,
      rejectReason: !volumePriceOK ? 'volume_price' : !maBullish ? 'ma_structure'
        : !confirmation.type ? 'no_confirm' : !signal ? 'min_score' : null,
    };
  }

  function calculate(input, options = {}) {
    if (!Array.isArray(input)) throw new TypeError('K 线数据必须是数组');
    const inputUnit = options.volumeUnit || '股';
    const forceDivisor = inputUnit === '股' ? 100 : 1;
    const forceUnit = inputUnit === '股' || inputUnit === '手' ? '手' : inputUnit;
    let opens = [], highs = [], lows = [], closes = [], volumes = [];
    let up = [], down = [], main = [], retail = [];
    let segmentStart = 0;
    let lastDate = '';
    let invalidRows = 0;
    const rows = input.map((source, index) => {
      const row = source && typeof source === 'object' ? source : {};
      const date = typeof row.date === 'string' ? row.date : typeof row.time === 'string' ? row.time : '';
      const values = [row.open, row.high, row.low, row.close];
      const dateOK = Boolean(date && (!lastDate || date > lastDate));
      if (dateOK) lastDate = date;
      const valid = dateOK && values.every(value => number(value) && value > 0)
        && number(row.volume) && row.volume >= 0
        && row.high >= Math.max(row.open, row.close) && row.low <= Math.min(row.open, row.close) && row.high >= row.low;
      const result = { date, index, valid, ...blanks() };
      if (!valid) {
        invalidRows += 1;
        opens = []; highs = []; lows = []; closes = []; volumes = [];
        up = []; down = []; main = []; retail = [];
        segmentStart = index + 1;
        result.issue = !dateOK ? 'invalid_date_order' : 'invalid_ohlcv';
        return result;
      }
      opens.push(row.open); highs.push(row.high); lows.push(row.low); closes.push(row.close); volumes.push(row.volume);
      const offset = closes.length - 1;
      const volume = row.volume / forceDivisor;
      up.push(offset && row.close > closes[offset - 1] ? volume : 0);
      down.push(offset && row.close < closes[offset - 1] ? volume : 0);
      const upMean = maAt(up, offset, 3);
      const downMean = maAt(down, offset, 3);
      // Retain the original zero seed / expanding MA initialization internally.
      main.push(offset ? 0.5 * upMean + 0.5 * main[offset - 1] : upMean);
      retail.push(offset ? (2 / 11) * downMean + (9 / 11) * retail[offset - 1] : downMean);
      result.ma5 = offset >= 4 ? safe(maAt(closes, offset, 5)) : null;
      result.ma10 = offset >= 9 ? safe(maAt(closes, offset, 10)) : null;
      result.ma20 = offset >= 19 ? safe(maAt(closes, offset, 20)) : null;
      result.volumeMA5 = offset >= 4 ? safe(maAt(volumes, offset, 5)) : null;
      result.volumeRatio5 = offset >= 5 ? volumeRatio(volumes, offset, 5) : null;
      if (offset >= 2) {
        result.mainForce = safe(main[offset]);
        result.retailForce = safe(retail[offset]);
        result.forceGap = safe(main[offset] - retail[offset]);
        result.forceDominance = ratio(result.forceGap, main[offset] + retail[offset]);
      }
      if (offset >= 3 && number(main[offset]) && number(retail[offset]) && number(main[offset - 1]) && number(retail[offset - 1])) {
        result.prevMainForce = main[offset - 1];
        result.prevRetailForce = retail[offset - 1];
        result.mainForceState = state(main[offset], main[offset - 1]);
        result.goldenCross = main[offset] > retail[offset] && main[offset - 1] <= retail[offset - 1];
      }
      if (offset >= 4 && number(main[offset - 2]) && result.mainForceState !== null) {
        result.prev2MainForce = main[offset - 2];
        result.prevMainForceState = state(main[offset - 1], main[offset - 2]);
        result.purpleToYellow = main[offset - 1] > 0 && result.prevMainForceState === 'falling'
          && main[offset] > 0 && result.mainForceState === 'rising';
        result.volumeConversion = result.purpleToYellow;
      }
      const chart = chartAt(opens, highs, lows, closes, volumes, offset);
      if (chart) {
        // All graph coordinates refer back to the caller's original rows.
        for (const key of ['boxStartIndex', 'boxEndIndex', 'breakoutIndex', 'retestIndex']) {
          if (number(chart[key])) chart[key] += segmentStart;
        }
        result.chart = chart;
        result.boxHigh = chart.boxHigh ?? null;
        result.boxLow = chart.boxLow ?? null;
        result.boxStartIndex = chart.boxStartIndex ?? null;
        result.boxEndIndex = chart.boxEndIndex ?? null;
        result.breakoutIndex = chart.breakoutIndex ?? null;
        result.boxBreakout = chart.breakoutIndex === index;
        result.boxConfirmed = Boolean(chart.type);
        result.boxSignal = chart.signal;
        result.boxScore = chart.score ?? null;
      }
      return result;
    });
    return {
      rows,
      metadata: {
        version: VERSION, inputVolumeUnit: inputUnit, forceUnit, invalidRows,
        warmup: { ma5: 5, ma10: 10, ma20: 20, volumeMA5: 5, volumeRatio5: 6, force: 3, goldenCross: 4, purpleToYellow: 5, chart: 80 },
        source: 'FINAL-TRADE ths_volume_signal / chart_volume_swing_strategy',
      },
      notes: [
        '主散量能由收盘涨跌和成交量估算，不是逐笔资金流。',
        '紫转黄表示主力线由回落转回升；金叉表示主力线上穿散户线。',
        '图形量价波段沿用原策略默认阈值，信号仅作图形观察。',
        '按现有价格口径计算；未复权数据遇除权可能产生不连续信号。',
        ...(invalidRows ? [`${invalidRows} 根数据不完整或顺序异常，已断开计算并重新暖机。`] : []),
      ],
    };
  }

  return Object.freeze({ calculate, version: VERSION });
});

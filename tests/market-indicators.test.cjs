'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { calculate } = require('../preview/market-indicators.js');

const bar = (close, index, volume = 1000) => ({ date: String(index).padStart(4, '0'), open: close - 0.05, high: close + 0.2, low: close - 0.2, close, volume });
function forceFixture() {
  const closes = [10, 11, 10, 9, 10, 11, 10, 9, 12, 13, 12, 11, 10, 9, 10, 12, 13, 12, 11, 12];
  const volumes = [1000, 1200, 900, 1600, 1900, 1100, 1000, 2100, 3000, 1800, 900, 800, 3000, 900, 1900, 2100, 1200, 1900, 2300, 2900];
  return closes.map((close, index) => bar(close, index, volumes[index]));
}
function boxFixture() {
  const rows = Array.from({ length: 80 }, (_, index) => {
    const close = 10 + (index % 5 - 2) * 0.08;
    return { date: String(index).padStart(4, '0'), open: close - 0.04, high: close + 0.24, low: close - 0.24, close, volume: index < 40 ? 1000 : 900 };
  });
  for (const [close, open, high, low, volume] of [
    [11.2, 10.2, 11.3, 10.1, 3000], [11.15, 11.2, 11.3, 10.95, 800],
    [11.3, 11.1, 11.5, 11, 1000], [11.4, 11.3, 11.6, 11.2, 1000],
    [10.4, 10.6, 10.8, 10.28, 350], [11.5, 10.5, 11.6, 10.4, 1200],
  ]) rows.push({ date: String(rows.length).padStart(4, '0'), open, high, low, close, volume });
  return rows;
}
const near = (actual, expected, tolerance = 0.00000051) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

test('browser and Node expose the same calculator without changing the existing AT namespace', () => {
  const existing = { keep: true };
  const context = { window: { AT: existing } };
  vm.runInNewContext(fs.readFileSync(require.resolve('../preview/market-indicators.js'), 'utf8'), context);
  assert.equal(context.window.AT, existing);
  assert.equal(context.window.AT.keep, true);
  assert.equal(typeof context.window.AT.marketIndicators.calculate, 'function');
  assert.equal(context.window.AT.marketIndicators.calculate([]).rows.length, 0);
  assert.throws(() => calculate({}), TypeError);
});

test('MA and previous-five-volume ratio wait for the full required history', () => {
  const rows = calculate(Array.from({ length: 21 }, (_, index) => bar(index + 1, index, (index + 1) * 100))).rows;
  assert.equal(rows[3].ma5, null);
  assert.equal(rows[4].ma5, 3);
  assert.equal(rows[8].ma10, null);
  assert.equal(rows[9].ma10, 5.5);
  assert.equal(rows[18].ma20, null);
  near(rows[19].ma20, 10.5);
  assert.equal(rows[4].volumeMA5, 300);
  assert.equal(rows[4].volumeRatio5, null);
  assert.equal(rows[5].volumeRatio5, 2);
  assert.equal(rows[0].mainForce, null);
  assert.equal(rows[1].retailForce, null);
  assert.equal(rows[2].mainForceState, null);
  assert.equal(rows[2].goldenCross, false);
  assert.equal(rows[3].purpleToYellow, false);
});

test('main/retail values and distinct turn/cross events match frozen FINAL-TRADE Python reference vectors', () => {
  // Generated from ths_volume_signal.py on 2026-10-11; no old-project runtime dependency.
  const expected = [
    [2, 3.5, 0.545455, false, false], [3, 3.75, 1.961433, false, false],
    [4, 5.041667, 3.11996, false, false], [5, 7.520833, 3.522391, false, false],
    [6, 8.760417, 3.488017, false, false], [7, 6.213542, 4.73262, false, false],
    [8, 8.106771, 5.750932, true, false], [9, 12.053385, 5.978035, false, false],
    [10, 14.026693, 5.436574, false, false], [13, 2.503337, 8.846428, false, false],
    [14, 4.418335, 9.601623, true, false], [15, 8.875834, 8.401328, false, true],
    [19, 8.846406, 9.163794, true, false],
  ];
  const { rows } = calculate(forceFixture());
  for (const [index, main, retail, turn, cross] of expected) {
    near(rows[index].mainForce, main);
    near(rows[index].retailForce, retail);
    assert.equal(rows[index].purpleToYellow, turn, `turn at ${index}`);
    assert.equal(rows[index].volumeConversion, turn);
    assert.equal(rows[index].goldenCross, cross, `cross at ${index}`);
  }
  assert.equal(rows[7].mainForceState, 'falling');
  assert.equal(rows[8].mainForceState, 'rising');
});

test('volume units normalize shares to hands once, without changing event dates or ratios', () => {
  const shares = forceFixture();
  const hands = shares.map(row => ({ ...row, volume: row.volume / 100 }));
  const a = calculate(shares, { volumeUnit: '股' });
  const b = calculate(hands, { volumeUnit: '手' });
  const raw = calculate(hands, { volumeUnit: '原始单位' });
  assert.equal(a.metadata.forceUnit, '手');
  assert.equal(raw.metadata.forceUnit, '原始单位');
  for (let index = 0; index < a.rows.length; index += 1) {
    assert.equal(a.rows[index].mainForce, b.rows[index].mainForce);
    assert.equal(a.rows[index].retailForce, b.rows[index].retailForce);
    assert.equal(a.rows[index].goldenCross, b.rows[index].goldenCross);
    if (a.rows[index].volumeRatio5 !== null) near(a.rows[index].volumeRatio5, b.rows[index].volumeRatio5);
  }
});

test('box breakout, hold and pullback observations match original strategy and score vectors', () => {
  const rows = calculate(boxFixture()).rows;
  assert.equal(rows[78].chart, null);
  assert.equal(rows[79].chart.rejectReason, 'no_chart_setup');
  assert.equal(rows[80].boxBreakout, true);
  assert.equal(rows[80].boxHigh, 10.16);
  assert.equal(rows[80].boxLow, 9.6);
  assert.equal(rows[80].boxStartIndex, 40);
  assert.equal(rows[80].boxEndIndex, 79);
  assert.equal(rows[80].boxConfirmed, false);
  assert.equal(rows[82].boxSignal, false);
  assert.equal(rows[83].chart.type, 'hold_above');
  assert.equal(rows[83].boxSignal, true);
  assert.equal(rows[83].boxScore, 71.13);
  assert.equal(rows[83].chart.grade, 'B');
  assert.equal(rows[84].chart.type, 'pullback_retest');
  assert.equal(rows[84].boxSignal, true);
  assert.equal(rows[84].boxScore, 78.67);
  assert.equal(rows[84].chart.grade, 'A');
  assert.equal(rows[85].boxScore, 72.3);
});

test('appending or changing future bars cannot repaint any earlier value or signal', () => {
  const fixture = boxFixture();
  const before = JSON.stringify(fixture);
  const all = calculate(fixture).rows;
  for (const count of [1, 3, 5, 20, 79, 80, 81, 84, 85]) {
    assert.deepEqual(calculate(fixture.slice(0, count)).rows, all.slice(0, count));
  }
  const changed = fixture.map((row, index) => index >= 84 ? bar(4, index, 1000000) : row);
  assert.deepEqual(calculate(changed).rows.slice(0, 84), all.slice(0, 84));
  assert.equal(JSON.stringify(fixture), before);
});

test('missing or contradictory bars break the series and never become invented zero-price signals', () => {
  for (const bad of [{ close: null }, { volume: NaN }, { volume: -1 }, { high: 1 }, { low: Infinity }, { open: '10' }]) {
    const fixture = forceFixture();
    fixture[10] = { ...fixture[10], ...bad };
    const result = calculate(fixture);
    assert.equal(result.metadata.invalidRows, 1);
    assert.equal(result.rows[10].valid, false);
    assert.equal(result.rows[10].mainForce, null);
    assert.equal(result.rows[10].goldenCross, false);
    assert.equal(result.rows[11].mainForce, null);
    assert.equal(result.rows[12].mainForce, null);
    assert.equal(result.rows[13].ma5, null);
    assert.equal(result.rows[15].ma5, (11 + 10 + 9 + 10 + 12) / 5);
  }
});

test('duplicate or descending dates stay aligned with input and are excluded from signal history', () => {
  const fixture = forceFixture();
  fixture[10] = { ...fixture[10], date: fixture[9].date };
  fixture[11] = { ...fixture[11], date: fixture[8].date };
  const { rows, metadata } = calculate(fixture);
  assert.equal(rows.length, fixture.length);
  assert.equal(metadata.invalidRows, 2);
  assert.equal(rows[10].issue, 'invalid_date_order');
  assert.equal(rows[11].issue, 'invalid_date_order');
  assert.equal(rows[12].mainForce, null);
});

test('zero denominators and flat/no-volume history do not generate ratios or chart signals', () => {
  const rows = calculate(Array.from({ length: 90 }, (_, index) => bar(10, index, 0))).rows;
  for (const row of rows) {
    assert.equal(row.volumeRatio5, null);
    assert.equal(row.forceDominance, null);
    assert.equal(row.goldenCross, false);
    assert.equal(row.purpleToYellow, false);
    assert.equal(row.boxSignal, false);
  }
  const fixture = boxFixture().map(row => ({ ...row, volume: 0 }));
  fixture[80].volume = 3000;
  assert.equal(calculate(fixture).rows[80].boxBreakout, false);
});

test('after a missing row, box coordinates remain indices into the original input', () => {
  const fixture = [null, ...boxFixture()];
  const result = calculate(fixture);
  assert.equal(result.rows[84].boxSignal, true);
  assert.equal(result.rows[84].boxStartIndex, 41);
  assert.equal(result.rows[84].breakoutIndex, 81);
  assert.equal(result.rows[85].chart.retestIndex, 85);
});

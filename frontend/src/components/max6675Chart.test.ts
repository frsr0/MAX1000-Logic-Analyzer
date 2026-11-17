import { expect, it } from 'vitest';
import type { Max6675ChannelConfig, Max6675Config, Max6675Reading, Max6675Row } from '../api/types';
import {
  ALARM_LABEL, CHANNEL_COLOURS, PLOT, VIEW, aggregateRows, alarmSummary, axisLabels, channelPaths, channelStats,
  displayDelta, displayTemp, formatClock, formatDateTime, inSpan, isSteadyZero, nearestIndex, ratePerMinute, spanOf,
  storedDelta, storedTemp, timeAtPointer, timeText, unitSymbol, validateMax6675Config, visibleRows, xFor, yFor, yRange,
} from './max6675Chart';

const ok = (temp_c: number): Max6675Reading => ({ temp_c, status: 'ok' });
const row = (seq: number, t: number, readings: (Max6675Reading | null)[]): Max6675Row =>
  ({ seq, t, elapsed_s: t - 100, readings });
const none = [null, null, null, null];

it('gives four distinct channel colours and fixed plot geometry', () => {
  expect(CHANNEL_COLOURS).toHaveLength(4);
  expect(new Set(CHANNEL_COLOURS).size).toBe(4);
  expect(VIEW.width).toBe(720);
  expect(PLOT.width).toBe(664);
});

it('keeps rows inside the window ending at the newest row', () => {
  const rows = [row(0, 0, [ok(1), null, null, null]), row(1, 30, [ok(2), null, null, null]),
    row(2, 100, [ok(3), null, null, null])];
  expect(visibleRows([], 60)).toEqual([]);
  expect(visibleRows(rows, null)).toBe(rows);
  expect(visibleRows(rows, 60).map((r) => r.seq)).toEqual([2]);
  expect(visibleRows(rows, 100).map((r) => r.seq)).toEqual([0, 1, 2]);
});

it('reports the first and last time of rows, or zero when there are none', () => {
  expect(spanOf([])).toEqual({ t0: 0, t1: 0 });
  expect(spanOf([row(0, 5, none), row(1, 9, none)])).toEqual({ t0: 5, t1: 9 });
});

it('returns one bucket per row when the rows already fit', () => {
  const rows = [row(0, 0, [ok(1), null, null, null]), row(1, 10, [ok(3), null, null, null])];
  const buckets = aggregateRows(rows, 2);
  expect(buckets).toEqual([
    { t: 0, values: [{ count: 1, min: 1, max: 1, mean: 1 }, null, null, null] },
    { t: 10, values: [{ count: 1, min: 3, max: 3, mean: 3 }, null, null, null] },
  ]);
});

it('returns no buckets for no rows', () => {
  expect(aggregateRows([], 10)).toEqual([]);
});

it('splits many rows into at most N buckets with min, max, mean and the mid time', () => {
  const rows = Array.from({ length: 10 }, (_, i) => row(i, i, [ok(i), null, null, null]));
  const buckets = aggregateRows(rows, 2);
  expect(buckets).toHaveLength(2);
  expect(buckets[0]).toEqual({ t: 2, values: [{ count: 5, min: 0, max: 4, mean: 2 }, null, null, null] });
  expect(buckets[1]).toEqual({ t: 7, values: [{ count: 5, min: 5, max: 9, mean: 7 }, null, null, null] });
});

it('keeps single-row buckets when the row count does not divide evenly', () => {
  const rows = [row(0, 0, [ok(1), null, null, null]), row(1, 10, [ok(2), null, null, null]),
    row(2, 20, [ok(3), null, null, null])];
  const buckets = aggregateRows(rows, 2);
  expect(buckets.map((bucket) => bucket.t)).toEqual([0, 15]);
  expect(buckets[0].values[0]).toEqual({ count: 1, min: 1, max: 1, mean: 1 });
});

it('leaves a channel empty in buckets without ok readings', () => {
  const rows = [row(0, 0, [{ temp_c: 50, status: 'open' }, null, null, null]),
    row(1, 1, [ok(1), null, null, null]), row(2, 2, [ok(2), null, null, null]), row(3, 3, [ok(3), null, null, null])];
  const buckets = aggregateRows(rows, 2);
  expect(buckets[0].values[0]).toEqual({ count: 1, min: 1, max: 1, mean: 1 });
  expect(aggregateRows([row(0, 0, [{ temp_c: 1, status: 'bad_frame' }, null, null, null])], 1)[0].values[0]).toBeNull();
});

it('computes statistics only from ok readings with a temperature', () => {
  const rows = [
    row(0, 0, [ok(20), { temp_c: 99, status: 'open' }, null, null]),
    row(1, 1, [ok(30), { temp_c: null, status: 'ok' }, null, null]),
    row(2, 2, [{ temp_c: null, status: 'bad_frame' }, null, null, null]),
  ];
  expect(channelStats(rows, 0)).toEqual({ count: 2, min: 20, max: 30, mean: 25 });
  expect(channelStats(rows, 1)).toBeNull();
  expect(channelStats(rows, 2)).toBeNull();
  expect(channelStats([], 0)).toBeNull();
});

it('pads the temperature range and falls back when there is no data', () => {
  expect(yRange([])).toEqual({ min: 0, max: 50 });
  expect(yRange([row(0, 0, [null, { temp_c: null, status: 'open' }, null, null])])).toEqual({ min: 0, max: 50 });
  expect(yRange([row(0, 0, [ok(20), null, null, null]), row(1, 1, [ok(30), ok(40), null, null])]))
    .toEqual({ min: 18, max: 42 });
  const flat = yRange([row(0, 0, [ok(25), null, null, null])]);
  expect(flat.min).toBeCloseTo(24);
  expect(flat.max).toBeCloseTo(26);
});

it('maps times and temperatures into plot-local coordinates', () => {
  expect(xFor(10, { t0: 0, t1: 20 })).toBeCloseTo(332);
  expect(xFor(5, { t0: 5, t1: 5 })).toBe(0);
  expect(yFor(0, { min: 0, max: 100 })).toBe(PLOT.height);
  expect(yFor(100, { min: 0, max: 100 })).toBe(0);
});

it('builds mean lines broken at gaps and min-max bands over each run', () => {
  const range = { min: 0, max: 100 };
  const buckets = [
    { t: 0, values: [{ count: 1, min: 0, max: 10, mean: 5 }, null, null, null] },
    { t: 10, values: [{ count: 1, min: 20, max: 30, mean: 25 }, null, null, null] },
    { t: 15, values: [null, null, null, null] },
    { t: 20, values: [{ count: 1, min: 40, max: 60, mean: 50 }, null, null, null] },
  ];
  expect(channelPaths(buckets, 0, { t0: 0, t1: 20 }, range)).toEqual({
    line: 'M 0.0 190.0 L 332.0 150.0 M 664.0 100.0',
    band: 'M 0.0 180.0 L 332.0 140.0 L 332.0 160.0 L 0.0 200.0 Z M 664.0 80.0 L 664.0 120.0 Z',
  });
  expect(channelPaths(buckets, 1, { t0: 0, t1: 20 }, range)).toEqual({ line: '', band: '' });
});

it('places a single-instant series at x = 0', () => {
  const buckets = [
    { t: 5, values: [{ count: 1, min: 0, max: 0, mean: 100 }, null, null, null] },
    { t: 5, values: [{ count: 1, min: 0, max: 0, mean: 0 }, null, null, null] },
  ];
  expect(channelPaths(buckets, 0, { t0: 5, t1: 5 }, { min: 0, max: 100 }).line).toBe('M 0.0 0.0 L 0.0 200.0');
});

it('formats local clock time as HH:MM:SS and dates as "17 Nov 10:42"', () => {
  const t = new Date(2026, 0, 2, 9, 5, 7).getTime() / 1000;
  expect(formatClock(t)).toBe('09:05:07');
  expect(formatDateTime(new Date(2026, 10, 17, 10, 42, 5).getTime() / 1000)).toBe('17 Nov 10:42');
});

it('shows only the clock for spans up to 24 h and adds the date beyond that', () => {
  const t = new Date(2026, 10, 17, 10, 42, 5).getTime() / 1000;
  expect(timeText(t, { t0: 0, t1: 86400 })).toBe('10:42:05');
  expect(timeText(t, { t0: 0, t1: 86401 })).toBe('17 Nov 10:42');
});

it('labels five evenly spaced times across the span', () => {
  const labels = axisLabels({ t0: 1000, t1: 1010 });
  expect(labels).toHaveLength(5);
  expect(labels.map((label) => label.x)).toEqual([0, 166, 332, 498, 664]);
  expect(labels[0].text).toBe(formatClock(1000));
  expect(labels[4].text).toBe(formatClock(1010));
  const long = axisLabels({ t0: 0, t1: 2 * 86400 });
  expect(long[2].text).toMatch(/^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$/);
});

it('maps a pointer position inside the SVG to a time, clamped to the span', () => {
  const rect = { left: 100, width: 720 };
  const span = { t0: 0, t1: 100 };
  expect(timeAtPointer(100 + PLOT.x, rect, span)).toBe(0);
  expect(timeAtPointer(100 + 48 + 332, rect, span)).toBeCloseTo(50);
  expect(timeAtPointer(100 + 720, rect, span)).toBe(100);
  expect(timeAtPointer(0, rect, span)).toBe(0);
});

it('finds the nearest bucket and the time test for inclusion', () => {
  const buckets = [0, 10, 20].map((t) => ({ t, values: none }));
  expect(nearestIndex(buckets, 0)).toBe(0);
  expect(nearestIndex(buckets, 12)).toBe(1);
  expect(nearestIndex(buckets, 100)).toBe(2);
  expect(nearestIndex(buckets, Number.NaN)).toBe(0);
  expect(inSpan(5, { t0: 0, t1: 10 })).toBe(true);
  expect(inSpan(11, { t0: 0, t1: 10 })).toBe(false);
});

it('estimates the rate in degrees per minute from the last 60 s of ok readings', () => {
  // 0.1 degC per second from t=0 to t=60 gives 6 degC per minute.
  const rows = Array.from({ length: 7 }, (_, i) => row(i, i * 10, [ok(20 + i), null, null, null]));
  expect(ratePerMinute(rows, 0)).toBeCloseTo(6);
  expect(ratePerMinute([], 0)).toBeNull();
  // Older rows fall outside the window; bad and missing readings are skipped.
  const mixed = [row(0, -1000, [ok(999), null, null, null]), ...rows,
    row(8, 70, [{ temp_c: 99, status: 'open' }, null, null, null]), row(9, 71, [null, null, null, null])];
  expect(ratePerMinute(mixed, 0)).toBeCloseTo(6);
});

it('gives no rate with fewer than two points or a span under 10 s', () => {
  expect(ratePerMinute([row(0, 0, [ok(20), null, null, null])], 0)).toBeNull();
  expect(ratePerMinute([row(0, 0, [ok(20), null, null, null]), row(1, 5, [ok(21), null, null, null])], 0)).toBeNull();
});

it('detects a steady 0 °C over the last five ok readings', () => {
  const zeros = (count: number) => Array.from({ length: count }, (_, i) => row(i, i, [ok(0), null, null, null]));
  expect(isSteadyZero(zeros(5), 0)).toBe(true);
  expect(isSteadyZero(zeros(4), 0)).toBe(false);
  expect(isSteadyZero([...zeros(5), row(9, 9, [ok(0.5), null, null, null])], 0)).toBe(false);
  // Non-ok and missing readings are skipped rather than counted.
  expect(isSteadyZero([...zeros(5), row(9, 9, [{ temp_c: 3, status: 'open' }, null, null, null]),
    row(10, 10, [null, null, null, null])], 0)).toBe(true);
  expect(isSteadyZero(zeros(5), 0, 6)).toBe(false);
});

it('writes alarm summaries with and without a start time', () => {
  expect(ALARM_LABEL.high).toBe('HIGH');
  expect(alarmSummary('Fan', 'high', null)).toBe('Fan: HIGH');
  expect(alarmSummary('Fan', 'low', new Date(2026, 0, 1, 10, 42, 5).getTime() / 1000)).toBe('Fan: LOW since 10:42:05');
});

it('converts temperatures with +32 and offsets without it', () => {
  expect(unitSymbol('C')).toBe('°C');
  expect(unitSymbol('F')).toBe('°F');
  expect(displayTemp(21.25, 'C')).toBe(21.25);
  expect(displayTemp(21.25, 'F')).toBe(70.25);
  expect(displayDelta(5, 'C')).toBe(5);
  expect(displayDelta(5, 'F')).toBe(9);
  expect(storedTemp(20, 'C')).toBe(20);
  expect(storedTemp(70.25, 'F')).toBe(21.25);
  expect(storedDelta(2, 'C')).toBe(2);
  expect(storedDelta(9, 'F')).toBe(5);
});

const channel = (over: Partial<Max6675ChannelConfig> = {}): Max6675ChannelConfig => ({
  name: 'Ch', enabled: false, so_pin: 15, cs_pin: 16, sck_pin: 17,
  offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true, ...over,
});
const config = (channels: Max6675ChannelConfig[], interval_s = 1): Max6675Config => ({
  interval_s, channels, stop_after_s: null, auto_resume: true, auto_reconnect: true,
});
const one = channel({ enabled: true, name: 'Ch1' });

it('accepts a valid single-channel configuration', () => {
  expect(validateMax6675Config(config([one, channel(), channel(), channel()]))).toEqual([]);
});

const two = [one, channel({ enabled: true, cs_pin: 18 })];
const four = [one, channel({ enabled: true, cs_pin: 18 }), channel({ enabled: true, cs_pin: 19 }),
  channel({ enabled: true, cs_pin: 20 })];

it('scales the minimum interval with the number of enabled channels', () => {
  expect(validateMax6675Config(config([one], 0.4)))
    .toEqual(['Interval must be at least 0.5 s for 1 enabled channel (each read takes about 0.4 s)']);
  expect(validateMax6675Config(config(two, 0.9)))
    .toEqual(['Interval must be at least 1 s for 2 enabled channels (each read takes about 0.4 s)']);
  expect(validateMax6675Config(config(four, 1.9)))
    .toEqual(['Interval must be at least 2 s for 4 enabled channels (each read takes about 0.4 s)']);
});

it('accepts exactly the minimum interval for each enabled count', () => {
  expect(validateMax6675Config(config([one], 0.5))).toEqual([]);
  expect(validateMax6675Config(config(two, 1))).toEqual([]);
  expect(validateMax6675Config(config(four, 2))).toEqual([]);
});

it('caps the interval at 3600 s', () => {
  expect(validateMax6675Config(config([one], 3600))).toEqual([]);
  expect(validateMax6675Config(config([one], 3601))).toEqual(['Interval must be at most 3600 s']);
});

it('rejects a non-numeric interval', () => {
  expect(validateMax6675Config(config([one], Number.NaN))).toEqual(['Interval must be a number']);
});

it('requires at least one enabled channel', () => {
  expect(validateMax6675Config(config([channel(), channel(), channel(), channel()])))
    .toEqual(['Enable at least one channel']);
});

it('requires SO, CS and SCK to be different within an enabled channel', () => {
  expect(validateMax6675Config(config([channel({ enabled: true, cs_pin: 15 }), channel(), channel(), channel()])))
    .toEqual(['Channel 1: SO, CS and SCK must be different pins']);
  expect(validateMax6675Config(config([channel({ enabled: true, sck_pin: 15 }), channel(), channel(), channel()])))
    .toEqual(['Channel 1: SO, CS and SCK must be different pins']);
  expect(validateMax6675Config(config([channel({ enabled: true, sck_pin: 16 }), channel(), channel(), channel()])))
    .toEqual(['Channel 1: SO, CS and SCK must be different pins']);
});

it('ignores pin clashes involving disabled channels', () => {
  expect(validateMax6675Config(config([one, channel({ cs_pin: 16 }), channel(), channel()]))).toEqual([]);
});

it('requires every enabled CS pin to be unique', () => {
  const problems = validateMax6675Config(config([
    one,
    channel({ enabled: true, name: 'Ch2', so_pin: 10, cs_pin: 16, sck_pin: 11 }),
    channel(),
    channel(),
  ]));
  expect(problems).toContain('CS pin 16 is used by more than one enabled channel');
});

it('keeps CS away from other channels SO and SCK pins', () => {
  const problems = validateMax6675Config(config([
    one,
    channel({ enabled: true, name: 'Ch2', so_pin: 16, cs_pin: 18, sck_pin: 19 }),
    channel(), channel(),
  ]));
  expect(problems).toContain('CS pin 16 on channel 1 is also used as SO or SCK');
  expect(problems).toContain('SO pin 16 on channel 2 is also used as CS or SCK');
});

it('keeps SCK away from other channels SO and CS pins', () => {
  const problems = validateMax6675Config(config([
    one,
    channel({ enabled: true, name: 'Ch2', so_pin: 11, cs_pin: 17, sck_pin: 19 }),
    channel(), channel(),
  ]));
  expect(problems).toContain('SCK pin 17 on channel 1 is also used as SO or CS');
});

it('allows sharing SO and SCK pins between channels', () => {
  expect(validateMax6675Config(config([
    one,
    channel({ enabled: true, name: 'Ch2', so_pin: 15, cs_pin: 18, sck_pin: 17 }),
    channel(), channel(),
  ]))).toEqual([]);
});

it('limits the offset to -20..20 and the alarms to -50..1100', () => {
  expect(validateMax6675Config(config([one, channel({ offset_c: 20 }), channel({ offset_c: -20 }), channel()]))).toEqual([]);
  expect(validateMax6675Config(config([one, channel({ offset_c: 20.5 }), channel(), channel()])))
    .toEqual(['Channel 2: offset must be between -20 and 20 °C']);
  expect(validateMax6675Config(config([one, channel({ alarm_high_c: 1101 }), channel(), channel()])))
    .toEqual(['Channel 2: high alarm must be between -50 and 1100 °C']);
  expect(validateMax6675Config(config([one, channel({ alarm_low_c: -51 }), channel(), channel()])))
    .toEqual(['Channel 2: low alarm must be between -50 and 1100 °C']);
  expect(validateMax6675Config(config([one, channel({ alarm_high_c: 1100, alarm_low_c: -50 }), channel(), channel()])))
    .toEqual([]);
  expect(validateMax6675Config(config([one, channel({ offset_c: Number.NaN }), channel(), channel()])))
    .toEqual(['Channel 2: offset must be between -20 and 20 °C']);
});

it('requires a low alarm below the high alarm when both are set', () => {
  expect(validateMax6675Config(config([channel({ enabled: true, alarm_low_c: 30, alarm_high_c: 30 }), channel(), channel(), channel()])))
    .toEqual(['Channel 1: low alarm must be below the high alarm']);
  expect(validateMax6675Config(config([channel({ enabled: true, alarm_low_c: 10, alarm_high_c: 30 }), channel(), channel(), channel()])))
    .toEqual([]);
  expect(validateMax6675Config(config([channel({ enabled: true, alarm_low_c: 10 }), channel(), channel(), channel()])))
    .toEqual([]);
});

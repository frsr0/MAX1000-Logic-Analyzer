import { expect, it } from 'vitest';
import type { Max6675ChannelConfig, Max6675Config, Max6675Reading, Max6675Row } from '../api/types';
import {
  CHANNEL_COLOURS, channelPath, channelStats, formatClock, reduceRows, validateMax6675Config, visibleRows, yRange,
} from './max6675Chart';

const ok = (temp_c: number): Max6675Reading => ({ temp_c, status: 'ok' });
const row = (seq: number, t: number, readings: (Max6675Reading | null)[]): Max6675Row =>
  ({ seq, t, elapsed_s: t - 100, readings });
const range = { min: 0, max: 100 };

it('gives four distinct channel colours', () => {
  expect(CHANNEL_COLOURS).toHaveLength(4);
  expect(new Set(CHANNEL_COLOURS).size).toBe(4);
});

it('keeps rows inside the window ending at the newest row', () => {
  const rows = [row(0, 0, [ok(1), null, null, null]), row(1, 30, [ok(2), null, null, null]),
    row(2, 100, [ok(3), null, null, null])];
  expect(visibleRows([], 60)).toEqual([]);
  expect(visibleRows(rows, null)).toBe(rows);
  expect(visibleRows(rows, 60).map((r) => r.seq)).toEqual([2]);
  expect(visibleRows(rows, 100).map((r) => r.seq)).toEqual([0, 1, 2]);
});

it('returns rows unchanged when they fit and otherwise keeps first and last rows per bucket', () => {
  const rows = Array.from({ length: 10 }, (_, i) => row(i, i, [null, null, null, null]));
  expect(reduceRows(rows, 10)).toBe(rows);
  expect(reduceRows(rows, 4).map((r) => r.seq)).toEqual([0, 4, 5, 9]);
  expect(reduceRows(rows, 3).map((r) => r.seq)).toEqual([0, 9]);
  expect(reduceRows(rows, 1).map((r) => r.seq)).toEqual([0]);
  expect(reduceRows(rows, 0)).toEqual([]);
  expect(reduceRows(rows, 4).length).toBeLessThanOrEqual(4);
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

it('builds SVG paths with breaks at non-ok readings', () => {
  expect(channelPath([], 0, 100, 50, range)).toBe('');
  const rows = [
    row(0, 10, [ok(0), null, null, null]),
    row(1, 15, [ok(50), null, null, null]),
    row(2, 20, [{ temp_c: 100, status: 'open' }, null, null, null]),
    row(3, 30, [ok(100), null, null, null]),
  ];
  expect(channelPath(rows, 0, 100, 50, range)).toBe('M 0.0 50.0 L 25.0 25.0 M 100.0 0.0');
  expect(channelPath(rows, 1, 100, 50, range)).toBe('');
});

it('places a single-instant series at x = 0', () => {
  const rows = [row(0, 5, [ok(100), null, null, null]), row(1, 5, [ok(0), null, null, null])];
  expect(channelPath(rows, 0, 100, 50, range)).toBe('M 0.0 0.0 L 0.0 50.0');
});

it('formats local clock time as HH:MM:SS', () => {
  const t = new Date(2026, 0, 2, 9, 5, 7).getTime() / 1000;
  expect(formatClock(t)).toBe('09:05:07');
});

const channel = (over: Partial<Max6675ChannelConfig> = {}): Max6675ChannelConfig =>
  ({ name: 'Ch', enabled: false, so_pin: 15, cs_pin: 16, sck_pin: 17, ...over });
const config = (channels: Max6675ChannelConfig[], interval_s = 1): Max6675Config => ({ interval_s, channels });
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

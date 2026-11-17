import type { Max6675AlarmKind, Max6675Config, Max6675Reading, Max6675Row } from '../api/types';

export const CHANNEL_COLOURS: string[] = ['#4fc3f7', '#ffb74d', '#81c784', '#e57373'];
export const VIEW = { width: 720, height: 240 };
export const PLOT = { x: 48, y: 8, width: 664, height: 200 };
const DAY_S = 86400;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export type TempUnit = 'C' | 'F';
export interface ChannelStats { count: number; min: number; max: number; mean: number }
export interface Span { t0: number; t1: number }
export interface Bucket { t: number; values: (ChannelStats | null)[] }
export interface ChannelPaths { line: string; band: string }

export const ALARM_LABEL: Record<Max6675AlarmKind, string> = { high: 'HIGH', low: 'LOW', fault: 'FAULT', clear: 'CLEARED' };

/** Rows inside the graph window ending at the newest row; null means all rows. */
export function visibleRows(rows: Max6675Row[], windowS: number | null): Max6675Row[] {
  if (rows.length === 0) return [];
  if (windowS === null) return rows;
  const cutoff = rows[rows.length - 1].t - windowS;
  return rows.filter((row) => row.t >= cutoff);
}

/** First and last time of a set of rows (0 when empty). */
export function spanOf(rows: Max6675Row[]): Span {
  return { t0: rows[0]?.t ?? 0, t1: rows[rows.length - 1]?.t ?? 0 };
}

/** Temperature of a reading that is usable for plotting and statistics. */
function okTemp(reading: Max6675Reading | null | undefined): number | null {
  return reading && reading.status === 'ok' ? reading.temp_c : null;
}

export function channelStats(rows: Max6675Row[], channel: number): ChannelStats | null {
  let count = 0;
  let min = Infinity;
  let max = -Infinity;
  let sum = 0;
  for (const row of rows) {
    const temp = okTemp(row.readings[channel]);
    if (temp === null) continue;
    count += 1;
    min = Math.min(min, temp);
    max = Math.max(max, temp);
    sum += temp;
  }
  if (count === 0) return null;
  return { count, min, max, mean: sum / count };
}

/**
 * Splits rows into at most maxBuckets consecutive groups. Each group keeps its mid time and the
 * min, max and mean of its ok readings per channel. When the rows already fit, each row is a bucket.
 */
export function aggregateRows(rows: Max6675Row[], maxBuckets: number): Bucket[] {
  if (rows.length === 0) return [];
  const width = rows[0].readings.length;
  let groups: Max6675Row[][];
  if (rows.length <= maxBuckets) {
    groups = rows.map((row) => [row]);
  } else {
    groups = [];
    for (let bucket = 0; bucket < maxBuckets; bucket++) {
      const start = Math.floor(bucket * rows.length / maxBuckets);
      const end = Math.floor((bucket + 1) * rows.length / maxBuckets);
      groups.push(rows.slice(start, end));
    }
  }
  return groups.map((group) => ({
    t: (group[0].t + group[group.length - 1].t) / 2,
    values: Array.from({ length: width }, (_, channel) => channelStats(group, channel)),
  }));
}

export function yRange(rows: Max6675Row[]): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    for (const reading of row.readings) {
      const temp = okTemp(reading);
      if (temp === null) continue;
      min = Math.min(min, temp);
      max = Math.max(max, temp);
    }
  }
  if (min === Infinity) return { min: 0, max: 50 };
  const span = max - min;
  const pad = span === 0 ? 1 : span * 0.1;
  return { min: min - pad, max: max + pad };
}

/** Plot-local x of a time inside the span. */
export function xFor(t: number, span: Span): number {
  return span.t1 === span.t0 ? 0 : (t - span.t0) / (span.t1 - span.t0) * PLOT.width;
}

/** Plot-local y of a temperature in °C. */
export function yFor(temp: number, range: { min: number; max: number }): number {
  return PLOT.height - (temp - range.min) / (range.max - range.min) * PLOT.height;
}

/**
 * SVG paths for one channel: the mean line (broken where a bucket has no ok readings) and a
 * min-max band over each unbroken run of buckets. Coordinates are plot-local.
 */
export function channelPaths(buckets: Bucket[], channel: number, span: Span,
  range: { min: number; max: number }): ChannelPaths {
  const runs: { x: number; stats: ChannelStats }[][] = [];
  let current: { x: number; stats: ChannelStats }[] | null = null;
  for (const bucket of buckets) {
    const stats = bucket.values[channel];
    if (!stats) {
      current = null;
      continue;
    }
    if (!current) {
      current = [];
      runs.push(current);
    }
    current.push({ x: xFor(bucket.t, span), stats });
  }
  const fmt = (value: number) => value.toFixed(1);
  const line = runs.map((run) => run.map((point, i) =>
    `${i === 0 ? 'M' : 'L'} ${fmt(point.x)} ${fmt(yFor(point.stats.mean, range))}`).join(' ')).join(' ');
  const band = runs.map((run) => {
    const top = run.map((point) => `${fmt(point.x)} ${fmt(yFor(point.stats.max, range))}`);
    const bottom = run.slice().reverse().map((point) => `${fmt(point.x)} ${fmt(yFor(point.stats.min, range))}`);
    return `M ${top.join(' L ')} L ${bottom.join(' L ')} Z`;
  }).join(' ');
  return { line, band };
}

/** Local wall-clock time as HH:MM:SS for a unix time in seconds. */
export function formatClock(t: number): string {
  const date = new Date(t * 1000);
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, '0'))
    .join(':');
}

/** Local date and HH:MM, for example "17 Nov 10:42". */
export function formatDateTime(t: number): string {
  const date = new Date(t * 1000);
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${formatClock(t).slice(0, 5)}`;
}

/** Axis and tooltip time: a date is added once the span is longer than 24 h. */
export function timeText(t: number, span: Span): string {
  return span.t1 - span.t0 > DAY_S ? formatDateTime(t) : formatClock(t);
}

/** Five evenly spaced labels across the span, with plot-local x positions. */
export function axisLabels(span: Span, count = 5): { x: number; text: string }[] {
  return Array.from({ length: count }, (_, i) => {
    const t = span.t0 + (span.t1 - span.t0) * i / (count - 1);
    return { x: PLOT.width * i / (count - 1), text: timeText(t, span) };
  });
}

/** Maps a pointer x (client pixels) inside the SVG to a time inside the span. */
export function timeAtPointer(clientX: number, rect: { left: number; width: number }, span: Span): number {
  const viewX = (clientX - rect.left) / rect.width * VIEW.width;
  const fraction = Math.min(1, Math.max(0, (viewX - PLOT.x) / PLOT.width));
  return span.t0 + fraction * (span.t1 - span.t0);
}

/** Index of the bucket whose time is closest to t. A NaN time falls back to the first bucket. */
export function nearestIndex(buckets: Bucket[], t: number): number {
  let best = 0;
  let bestDistance = Infinity;
  buckets.forEach((bucket, index) => {
    const distance = Math.abs(bucket.t - t);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

export function inSpan(t: number, span: Span): boolean {
  return t >= span.t0 && t <= span.t1;
}

/** Least-squares slope of the ok readings in the last windowS seconds, in °C per minute. Null when too little data. */
export function ratePerMinute(rows: Max6675Row[], channel: number, windowS = 60): number | null {
  if (rows.length === 0) return null;
  const cutoff = rows[rows.length - 1].t - windowS;
  const points: { t: number; temp: number }[] = [];
  for (const row of rows) {
    if (row.t < cutoff) continue;
    const temp = okTemp(row.readings[channel]);
    if (temp !== null) points.push({ t: row.t, temp });
  }
  if (points.length < 2) return null;
  const span = points[points.length - 1].t - points[0].t;
  if (span < 10) return null;
  const meanT = points.reduce((sum, p) => sum + p.t, 0) / points.length;
  const meanTemp = points.reduce((sum, p) => sum + p.temp, 0) / points.length;
  let numerator = 0;
  let denominator = 0;
  for (const point of points) {
    numerator += (point.t - meanT) * (point.temp - meanTemp);
    denominator += (point.t - meanT) ** 2;
  }
  return numerator / denominator * 60;
}

/** True when the last `count` ok readings of a channel are all exactly 0 °C. */
export function isSteadyZero(rows: Max6675Row[], channel: number, count = 5): boolean {
  let seen = 0;
  for (let i = rows.length - 1; i >= 0 && seen < count; i--) {
    const temp = okTemp(rows[i].readings[channel]);
    if (temp === null) continue;
    if (temp !== 0) return false;
    seen += 1;
  }
  return seen === count;
}

/** "Fan: HIGH since 10:42:05", or without the time when the start is unknown. */
export function alarmSummary(name: string, kind: Max6675AlarmKind, since: number | null): string {
  return since === null
    ? `${name}: ${ALARM_LABEL[kind]}`
    : `${name}: ${ALARM_LABEL[kind]} since ${formatClock(since)}`;
}

const round2 = (value: number) => Math.round(value * 100) / 100;

export function unitSymbol(unit: TempUnit): string {
  return unit === 'F' ? '°F' : '°C';
}

/** A stored °C temperature shown in the chosen unit. */
export function displayTemp(celsius: number, unit: TempUnit): number {
  return round2(unit === 'F' ? celsius * 9 / 5 + 32 : celsius);
}

/** A stored °C difference (offset or rate) shown in the chosen unit; no +32 offset. */
export function displayDelta(celsius: number, unit: TempUnit): number {
  return round2(unit === 'F' ? celsius * 9 / 5 : celsius);
}

/** An entered temperature in the chosen unit, converted to the stored °C value. */
export function storedTemp(value: number, unit: TempUnit): number {
  return round2(unit === 'F' ? (value - 32) * 5 / 9 : value);
}

/** An entered difference in the chosen unit, converted to the stored °C value. */
export function storedDelta(value: number, unit: TempUnit): number {
  return round2(unit === 'F' ? value * 5 / 9 : value);
}

/** Client-side mirror of the backend pin, interval and alarm rules. Returns human-readable problems. */
export function validateMax6675Config(config: Max6675Config): string[] {
  const problems: string[] = [];
  const enabled = config.channels
    .map((channel, index) => ({ ...channel, number: index + 1 }))
    .filter((channel) => channel.enabled);
  // Each sensor read holds chip-select high for about 0.4 s, so the floor scales with channels.
  const enabledCount = enabled.length;
  const minInterval = 0.5 * Math.max(1, enabledCount);
  if (!Number.isFinite(config.interval_s)) {
    problems.push('Interval must be a number');
  } else if (config.interval_s < minInterval) {
    problems.push(`Interval must be at least ${minInterval} s for ${enabledCount} enabled channel${enabledCount === 1 ? '' : 's'} (each read takes about 0.4 s)`);
  } else if (config.interval_s > 3600) {
    problems.push('Interval must be at most 3600 s');
  }
  if (enabled.length === 0) problems.push('Enable at least one channel');

  for (const channel of enabled) {
    if (channel.so_pin === channel.cs_pin || channel.so_pin === channel.sck_pin || channel.cs_pin === channel.sck_pin) {
      problems.push(`Channel ${channel.number}: SO, CS and SCK must be different pins`);
    }
  }
  const csPins = enabled.map((channel) => channel.cs_pin);
  for (const duplicate of new Set(csPins.filter((pin, index) => csPins.indexOf(pin) !== index))) {
    problems.push(`CS pin ${duplicate} is used by more than one enabled channel`);
  }
  for (const channel of enabled) {
    const others = enabled.filter((other) => other !== channel);
    if (others.some((other) => other.so_pin === channel.cs_pin || other.sck_pin === channel.cs_pin)) {
      problems.push(`CS pin ${channel.cs_pin} on channel ${channel.number} is also used as SO or SCK`);
    }
    if (others.some((other) => other.so_pin === channel.sck_pin || other.cs_pin === channel.sck_pin)) {
      problems.push(`SCK pin ${channel.sck_pin} on channel ${channel.number} is also used as SO or CS`);
    }
    if (others.some((other) => other.cs_pin === channel.so_pin || other.sck_pin === channel.so_pin)) {
      problems.push(`SO pin ${channel.so_pin} on channel ${channel.number} is also used as CS or SCK`);
    }
  }

  config.channels.forEach((channel, index) => {
    const number = index + 1;
    if (!(channel.offset_c >= -20 && channel.offset_c <= 20)) {
      problems.push(`Channel ${number}: offset must be between -20 and 20 °C`);
    }
    for (const [label, value] of [['low', channel.alarm_low_c], ['high', channel.alarm_high_c]] as const) {
      if (value !== null && !(value >= -50 && value <= 1100)) {
        problems.push(`Channel ${number}: ${label} alarm must be between -50 and 1100 °C`);
      }
    }
    if (channel.alarm_low_c !== null && channel.alarm_high_c !== null && channel.alarm_low_c >= channel.alarm_high_c) {
      problems.push(`Channel ${number}: low alarm must be below the high alarm`);
    }
  });
  return problems;
}

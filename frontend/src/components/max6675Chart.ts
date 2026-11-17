import type { Max6675Config, Max6675Reading, Max6675Row } from '../api/types';

export const CHANNEL_COLOURS: string[] = ['#4fc3f7', '#ffb74d', '#81c784', '#e57373'];

export interface ChannelStats { count: number; min: number; max: number; mean: number }

/** Rows inside the graph window ending at the newest row; null means all rows. */
export function visibleRows(rows: Max6675Row[], windowS: number | null): Max6675Row[] {
  if (rows.length === 0) return [];
  if (windowS === null) return rows;
  const cutoff = rows[rows.length - 1].t - windowS;
  return rows.filter((row) => row.t >= cutoff);
}

/** Keeps the first and last row of each bucket so peaks survive the reduction. */
export function reduceRows(rows: Max6675Row[], maxPoints: number): Max6675Row[] {
  if (rows.length <= maxPoints) return rows;
  if (maxPoints < 2) return rows.slice(0, maxPoints);
  const buckets = Math.floor(maxPoints / 2);
  const out: Max6675Row[] = [];
  for (let bucket = 0; bucket < buckets; bucket++) {
    const start = Math.floor(bucket * rows.length / buckets);
    const end = Math.floor((bucket + 1) * rows.length / buckets);
    out.push(rows[start], rows[end - 1]);
  }
  return out;
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

/** SVG path for one channel. Non-ok readings break the line so gaps stay visible. */
export function channelPath(rows: Max6675Row[], channel: number, width: number, height: number,
  range: { min: number; max: number }): string {
  if (rows.length === 0) return '';
  const t0 = rows[0].t;
  const t1 = rows[rows.length - 1].t;
  const parts: string[] = [];
  let needMove = true;
  for (const row of rows) {
    const temp = okTemp(row.readings[channel]);
    if (temp === null) {
      needMove = true;
      continue;
    }
    const x = t1 === t0 ? 0 : (row.t - t0) / (t1 - t0) * width;
    const y = height - (temp - range.min) / (range.max - range.min) * height;
    parts.push(`${needMove ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`);
    needMove = false;
  }
  return parts.join(' ');
}

/** Local wall-clock time as HH:MM:SS for a unix time in seconds. */
export function formatClock(t: number): string {
  const date = new Date(t * 1000);
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, '0'))
    .join(':');
}

/** Client-side mirror of the backend pin and interval rules. Returns human-readable problems. */
export function validateMax6675Config(config: Max6675Config): string[] {
  const problems: string[] = [];
  if (!(config.interval_s >= 0.5 && config.interval_s <= 3600)) {
    problems.push('Interval must be between 0.5 and 3600 s');
  }
  const enabled = config.channels
    .map((channel, index) => ({ ...channel, number: index + 1 }))
    .filter((channel) => channel.enabled);
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
  return problems;
}

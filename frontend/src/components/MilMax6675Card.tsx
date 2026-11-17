import { useEffect, useMemo, useRef, useState } from 'react';
import { api, max6675ExportUrl } from '../api/client';
import type {
  Max6675ChannelConfig, Max6675Config, Max6675Data, Max6675Reading, Max6675ReadingStatus, Max6675Row, Max6675Status,
} from '../api/types';
import { useApp } from '../state/appStore';
import { PIN_OPTIONS } from './milPins';
import {
  CHANNEL_COLOURS, channelPath, channelStats, formatClock, reduceRows, validateMax6675Config, visibleRows, yRange,
} from './max6675Chart';

const DATA_PAGE = 5000;
const GRAPH_POINTS = 2000;
const POLL_MS = 1000;
const IDLE_REFRESH_MS = 5000;
const PLOT = { x: 48, y: 8, width: 664, height: 200 };
const WINDOW_OPTIONS = [
  { value: '60', label: 'Last 1 min' },
  { value: '600', label: 'Last 10 min' },
  { value: '3600', label: 'Last 1 h' },
  { value: 'all', label: 'All' },
];
const READING_LABELS: Record<Max6675ReadingStatus, string> = {
  ok: 'OK',
  open: 'Open thermocouple',
  bad_frame: 'No sensor / bad frame',
  skipped: 'Skipped',
  error: 'Error',
};
const DEFAULT_CHANNELS: Max6675ChannelConfig[] = [
  { name: 'Channel 1', enabled: true, so_pin: 15, cs_pin: 16, sck_pin: 17 },
  { name: 'Channel 2', enabled: false, so_pin: 15, cs_pin: 18, sck_pin: 17 },
  { name: 'Channel 3', enabled: false, so_pin: 15, cs_pin: 19, sck_pin: 17 },
  { name: 'Channel 4', enabled: false, so_pin: 15, cs_pin: 20, sck_pin: 17 },
];

function readingText(reading: Max6675Reading | null): string {
  return reading && reading.temp_c !== null ? `${reading.temp_c.toFixed(2)} °C` : '—';
}

export function MilMax6675Card() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<Max6675Status | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [intervalS, setIntervalS] = useState(1);
  const [channels, setChannels] = useState<Max6675ChannelConfig[]>(DEFAULT_CHANNELS);
  const [latest, setLatest] = useState<(Max6675Reading | null)[]>([null, null, null, null]);
  const [rows, setRows] = useState<Max6675Row[]>([]);
  const [windowValue, setWindowValue] = useState('600');
  const [busy, setBusy] = useState(false);
  const seqRef = useRef(0);
  const droppedRef = useRef(0);
  const pollingRef = useRef(false);

  const running = Boolean(status?.running);
  const windowS = windowValue === 'all' ? null : Number(windowValue);
  const problems = validateMax6675Config({ interval_s: intervalS, channels });
  const valid = problems.length === 0;
  const enabledChannels = channels.flatMap((channel, index) => (channel.enabled ? [index] : []));
  const visible = useMemo(() => visibleRows(rows, windowS), [rows, windowS]);
  const shown = useMemo(() => reduceRows(visible, GRAPH_POINTS), [visible]);
  const range = useMemo(() => yRange(shown), [shown]);

  // Drop the oldest local rows when the backend reports it evicted more.
  const absorbDropped = (dropped: number) => {
    const grew = dropped - droppedRef.current;
    droppedRef.current = dropped;
    if (grew > 0) setRows((prev) => prev.slice(grew));
  };

  const syncStatus = (next: Max6675Status) => {
    setStatus(next);
    setLatest(next.channels.map((channel) => channel.last));
    absorbDropped(next.dropped_rows);
  };

  const fetchRows = async (since: number): Promise<Max6675Data> => {
    const page = await api.milMax6675Data(since);
    seqRef.current = page.next_seq;
    absorbDropped(page.dropped_rows);
    setRows((prev) => prev.concat(page.rows));
    return page;
  };

  // Mount-only load. Strict-mode double mounts are guarded by `alive`, so the
  // first, abandoned run cannot append its pages a second time.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const next = await api.milMax6675Status();
        if (!alive) return;
        syncStatus(next);
        setIntervalS(next.interval_s);
        setChannels(next.channels.map(({ name, enabled, so_pin, cs_pin, sck_pin }) =>
          ({ name, enabled, so_pin, cs_pin, sck_pin })));
        let page: Max6675Data;
        do {
          page = await fetchRows(seqRef.current);
        } while (alive && page.rows.length >= DATA_PAGE);
      } catch (error: any) {
        if (alive) setLoadError(error.message);
      }
    })();
    return () => { alive = false; };
  }, []);

  // Reads only refs and state setters, so the closure from the current render is safe.
  const poll = async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      const [, next] = await Promise.all([fetchRows(seqRef.current), api.milMax6675Status()]);
      syncStatus(next);
      setPollError(null);
    } catch (error: any) {
      setPollError(error.message);
    } finally {
      pollingRef.current = false;
    }
  };

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => { void poll(); }, POLL_MS);
    return () => clearInterval(timer);
  }, [running]);

  // While idle, notice logging started or stopped elsewhere. Failures are ignored.
  const idleRefresh = async () => {
    try {
      const next = await api.milMax6675Status();
      if (next.running) await fetchRows(seqRef.current);
      syncStatus(next);
    } catch {
      // Best effort: the next tick retries.
    }
  };

  useEffect(() => {
    if (running) return undefined;
    const timer = setInterval(() => { void idleRefresh(); }, IDLE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [running]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (error: any) {
      toast('error', error.message);
    } finally {
      setBusy(false);
    }
  };

  const config = (): Max6675Config => ({ interval_s: intervalS, channels });

  const updateChannel = (index: number, patch: Partial<Max6675ChannelConfig>) =>
    setChannels((prev) => prev.map((channel, i) => (i === index ? { ...channel, ...patch } : channel)));

  const readOnce = () => run(async () => {
    syncStatus(await api.milMax6675Config(config()));
    const result = await api.milMax6675Read();
    setLatest(result.readings);
  });

  const start = () => run(async () => {
    syncStatus(await api.milMax6675Config(config()));
    syncStatus(await api.milMax6675Start());
  });

  const stop = () => run(async () => {
    syncStatus(await api.milMax6675Stop());
    await fetchRows(seqRef.current);
  });

  const clear = () => run(async () => {
    const next = await api.milMax6675Clear();
    setRows([]);
    seqRef.current = next.next_seq;
    droppedRef.current = next.dropped_rows;
    syncStatus(next);
  });

  return (
    <section className="card" aria-labelledby="max6675-title">
      <h3 id="max6675-title">MAX6675 thermocouple datalogger</h3>
      <p className="hint">
        Each enabled channel reads one MAX6675 on its own CS pin; SO and SCK may be shared. A read takes about
        0.4 s per channel because chip-select is held high for the sensor's conversion time, so the shortest
        interval is 0.5 s per enabled channel. Logging pauses while a capture, the accelerometer or the signal
        generator is using the hardware.
      </p>
      <p className="hint">
        Sharing one SO line between sensors needs a pull-up on each CS line (D0–D14 have one; the PMOD pins do not).
      </p>
      {loadError && <div className="finding error" role="alert">{loadError}</div>}
      {pollError && (
        <div className="finding error" role="alert">{`Lost contact with the logger: ${pollError}`}</div>
      )}
      {status && status.paused_reason !== null && (
        <div className="finding warning" role="status">Logging paused: {status.paused_reason}</div>
      )}
      {status && status.last_error !== null && <div className="finding error">{status.last_error}</div>}
      <div className="max6675-controls">
        <label className="field"><span>Interval (s)</span>
          <input type="number" min={0.5} max={3600} step={0.5} value={intervalS} disabled={running}
            onChange={(event) => setIntervalS(Number(event.target.value))} />
        </label>
        <label className="field"><span>Graph window</span>
          <select value={windowValue} onChange={(event) => setWindowValue(event.target.value)}>
            {WINDOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <div className="button-row wrap">
          <button disabled={busy || !controlMode || running || !valid} onClick={readOnce}>Read once</button>
          <button className="primary" disabled={busy || !controlMode || running || !valid} onClick={start}>Start logging</button>
          <button disabled={busy || !controlMode || !running} onClick={stop}>Stop</button>
          <button disabled={busy || !controlMode} onClick={clear}>Clear</button>
          <a href={max6675ExportUrl()} download>Export CSV</a>
        </div>
      </div>
      <div className="max6675-channels">
        {channels.map((channel, index) => {
          const number = index + 1;
          const reading = latest[index];
          return (
            <div className="max6675-channel" key={number}>
              <label className="field checkbox">
                <input type="checkbox" checked={channel.enabled} disabled={running}
                  onChange={(event) => updateChannel(index, { enabled: event.target.checked })} />
                <span>{`Channel ${number} enabled`}</span>
              </label>
              <label className="field"><span>{`Channel ${number} name`}</span>
                <input type="text" value={channel.name} disabled={running}
                  onChange={(event) => updateChannel(index, { name: event.target.value })} />
              </label>
              <label className="field"><span>{`Channel ${number} SO`}</span>
                <select value={channel.so_pin} disabled={running}
                  onChange={(event) => updateChannel(index, { so_pin: Number(event.target.value) })}>
                  {PIN_OPTIONS.map((option) => (
                    <option key={option.pin} value={option.pin}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="field"><span>{`Channel ${number} CS`}</span>
                <select value={channel.cs_pin} disabled={running}
                  onChange={(event) => updateChannel(index, { cs_pin: Number(event.target.value) })}>
                  {PIN_OPTIONS.map((option) => (
                    <option key={option.pin} value={option.pin}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="field"><span>{`Channel ${number} SCK`}</span>
                <select value={channel.sck_pin} disabled={running}
                  onChange={(event) => updateChannel(index, { sck_pin: Number(event.target.value) })}>
                  {PIN_OPTIONS.map((option) => (
                    <option key={option.pin} value={option.pin}>{option.label}</option>
                  ))}
                </select>
              </label>
              <div className="field">
                <span>Latest reading</span>
                <span>
                  <span className="max6675-swatch" aria-hidden="true" style={{ background: CHANNEL_COLOURS[index] }} />{' '}
                  <strong>{readingText(reading)}</strong>{' '}
                  <span className="badge">{reading ? READING_LABELS[reading.status] : 'No reading yet'}</span>
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {problems.length > 0 && (
        <div className="finding warning">
          {problems.map((problem, index) => <div key={index}>{problem}</div>)}
        </div>
      )}
      <div className="max6675-legend">
        {enabledChannels.map((index) => (
          <span className="max6675-legend-item" key={index} data-testid={`max6675-legend-${index + 1}`}>
            <span className="max6675-swatch" aria-hidden="true" style={{ background: CHANNEL_COLOURS[index] }} />
            {channels[index].name}
          </span>
        ))}
      </div>
      <svg viewBox="0 0 720 240" role="img" aria-label="Temperature graph"
        style={{ display: 'block', width: '100%', height: 260 }}>
        {rows.length === 0 ? (
          <text x={360} y={120} textAnchor="middle" fill="currentColor">No readings yet</text>
        ) : (
          <>
            <text x={2} y={10} fontSize={11} fill="currentColor">°C</text>
            {[0, 1, 2, 3, 4].map((step) => {
              const temp = range.min + (range.max - range.min) * step / 4;
              const y = PLOT.y + PLOT.height - PLOT.height * step / 4;
              return (
                <g key={step}>
                  <line x1={PLOT.x} x2={PLOT.x + PLOT.width} y1={y} y2={y} stroke="currentColor" opacity={0.2} />
                  <text x={PLOT.x - 6} y={y + 4} textAnchor="end" fontSize={11} fill="currentColor">{temp.toFixed(1)}</text>
                </g>
              );
            })}
            {enabledChannels.map((index) => (
              <path key={index} data-testid={`max6675-line-${index + 1}`} fill="none"
                stroke={CHANNEL_COLOURS[index]} strokeWidth={2}
                transform={`translate(${PLOT.x} ${PLOT.y})`}
                d={channelPath(shown, index, PLOT.width, PLOT.height, range)} />
            ))}
            <text x={PLOT.x} y={232} fontSize={11} fill="currentColor">{formatClock(shown[0].t)}</text>
            <text x={PLOT.x + PLOT.width} y={232} textAnchor="end" fontSize={11} fill="currentColor">
              {formatClock(shown[shown.length - 1].t)}
            </text>
          </>
        )}
      </svg>
      <div className="table-scroll">
        <table className="data-table">
          <thead><tr><th>Channel</th><th>Samples</th><th>Min (°C)</th><th>Max (°C)</th><th>Mean (°C)</th></tr></thead>
          <tbody>
            {enabledChannels.map((index) => {
              const stats = channelStats(visible, index);
              return (
                <tr key={index}>
                  <td>{channels[index].name}</td>
                  <td>{stats ? stats.count : '—'}</td>
                  <td>{stats ? stats.min.toFixed(2) : '—'}</td>
                  <td>{stats ? stats.max.toFixed(2) : '—'}</td>
                  <td>{stats ? stats.mean.toFixed(2) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">{`${status ? status.row_count : rows.length} readings logged`}</p>
      {status && status.dropped_rows > 0 && (
        <div className="finding warning">{`Oldest ${status.dropped_rows} readings were discarded (log is full)`}</div>
      )}
    </section>
  );
}

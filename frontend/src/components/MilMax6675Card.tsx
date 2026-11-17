import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { api, max6675ExportUrl } from '../api/client';
import type {
  Max6675AlarmEvent, Max6675ChannelConfig, Max6675ChannelStatus, Max6675Config, Max6675Data, Max6675Note,
  Max6675Reading, Max6675ReadingStatus, Max6675Row, Max6675Status,
} from '../api/types';
import { useApp } from '../state/appStore';
import { PIN_OPTIONS } from './milPins';
import {
  ALARM_LABEL, CHANNEL_COLOURS, PLOT, VIEW, aggregateRows, alarmSummary, axisLabels, channelPaths, channelStats,
  displayDelta, displayTemp, formatClock, inSpan, isSteadyZero, nearestIndex, ratePerMinute, spanOf, storedDelta,
  storedTemp, timeAtPointer, timeText, unitSymbol, validateMax6675Config, visibleRows, xFor, yFor, yRange,
} from './max6675Chart';
import type { TempUnit } from './max6675Chart';

const DATA_PAGE = 5000;
const GRAPH_BUCKETS = 2000;
const POLL_MS = 1000;
const IDLE_REFRESH_MS = 5000;
const ALARM_EVENTS_SHOWN = 10;
const UNIT_KEY = 'msa_max6675_unit';
const ALERTS_KEY = 'msa_max6675_desktop_alerts';
const WINDOW_OPTIONS = [
  { value: '60', label: 'Last 1 min' },
  { value: '600', label: 'Last 10 min' },
  { value: '3600', label: 'Last 1 h' },
  { value: '21600', label: 'Last 6 h' },
  { value: '86400', label: 'Last 24 h' },
  { value: 'all', label: 'All' },
];
const STOP_OPTIONS = [
  { value: 'none', label: 'No limit' },
  { value: '600', label: '10 min' },
  { value: '3600', label: '1 h' },
  { value: '28800', label: '8 h' },
  { value: '86400', label: '24 h' },
  { value: '604800', label: '7 days' },
];
const READING_LABELS: Record<Max6675ReadingStatus, string> = {
  ok: 'OK',
  open: 'Open thermocouple',
  bad_frame: 'No sensor / bad frame',
  skipped: 'Skipped',
  error: 'Error',
};

function makeChannel(name: string, enabled: boolean, cs_pin: number): Max6675ChannelConfig {
  return {
    name, enabled, so_pin: 15, cs_pin, sck_pin: 17,
    offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true,
  };
}
const DEFAULT_CHANNELS: Max6675ChannelConfig[] = [
  makeChannel('Channel 1', true, 16),
  makeChannel('Channel 2', false, 18),
  makeChannel('Channel 3', false, 19),
  makeChannel('Channel 4', false, 20),
];

/** localStorage can be blocked or full; the preference then lasts for this page only. */
function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage is unavailable: the choice is kept only until the page reloads.
  }
}

function notificationsAvailable(): boolean {
  return typeof Notification !== 'undefined';
}

/** Config fields from a status row; older backends without the new fields get the defaults. */
function channelConfig(channel: Max6675ChannelStatus): Max6675ChannelConfig {
  return {
    name: channel.name, enabled: channel.enabled, so_pin: channel.so_pin, cs_pin: channel.cs_pin, sck_pin: channel.sck_pin,
    offset_c: channel.offset_c ?? 0,
    alarm_high_c: channel.alarm_high_c ?? null,
    alarm_low_c: channel.alarm_low_c ?? null,
    alarm_on_fault: channel.alarm_on_fault ?? true,
  };
}

/** Latest temperature in the chosen unit, or null when the reading has no temperature. */
function latestTemp(reading: Max6675Reading | null, unit: TempUnit): number | null {
  return reading && reading.temp_c !== null ? displayTemp(reading.temp_c, unit) : null;
}

function readingText(reading: Max6675Reading | null, unit: TempUnit): string {
  const value = latestTemp(reading, unit);
  return value === null ? '—' : `${value.toFixed(2)} ${unitSymbol(unit)}`;
}

interface NumberFieldProps {
  label: string;
  value: number | null;
  disabled: boolean;
  step: number;
  show: (stored: number) => number;
  store: (shown: number) => number;
  onChange: (stored: number | null) => void;
}

/** Number input that keeps the text being typed (such as a lone minus sign) until it loses focus. */
function NumberField({ label, value, disabled, step, show, store, onChange }: NumberFieldProps) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? (value === null ? '' : String(show(value)));
  return (
    <label className="field"><span>{label}</span>
      <input type="number" step={step} value={text} disabled={disabled}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          onChange(next === '' ? null : store(Number(next)));
        }}
        onBlur={() => setDraft(null)} />
    </label>
  );
}

export function MilMax6675Card() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<Max6675Status | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [intervalS, setIntervalS] = useState(1);
  const [channels, setChannels] = useState<Max6675ChannelConfig[]>(DEFAULT_CHANNELS);
  const [stopAfter, setStopAfter] = useState<number | null>(null);
  const [autoResume, setAutoResume] = useState(true);
  const [autoReconnect, setAutoReconnect] = useState(true);
  const [latest, setLatest] = useState<(Max6675Reading | null)[]>([null, null, null, null]);
  const [rows, setRows] = useState<Max6675Row[]>([]);
  const [notes, setNotes] = useState<Max6675Note[]>([]);
  const [noteText, setNoteText] = useState('');
  const [windowValue, setWindowValue] = useState('600');
  const [unit, setUnit] = useState<TempUnit>(() => (readStored(UNIT_KEY) === 'F' ? 'F' : 'C'));
  const [desktopAlerts, setDesktopAlerts] = useState(() => readStored(ALERTS_KEY) === '1');
  const [hoverT, setHoverT] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const seqRef = useRef(0);
  const droppedRef = useRef(0);
  const pollingRef = useRef(false);
  const alertsRef = useRef(desktopAlerts);
  const alarmSeenRef = useRef<number | null>(null);

  const running = Boolean(status?.running);
  const windowS = windowValue === 'all' ? null : Number(windowValue);
  const symbol = unitSymbol(unit);
  const config = (): Max6675Config => ({
    interval_s: intervalS, channels, stop_after_s: stopAfter, auto_resume: autoResume, auto_reconnect: autoReconnect,
  });
  const problems = validateMax6675Config(config());
  const valid = problems.length === 0;
  const enabledChannels = channels.flatMap((channel, index) => (channel.enabled ? [index] : []));
  const visible = useMemo(() => visibleRows(rows, windowS), [rows, windowS]);
  const buckets = useMemo(() => aggregateRows(visible, GRAPH_BUCKETS), [visible]);
  const range = useMemo(() => yRange(visible), [visible]);
  const span = spanOf(visible);
  const statusChannels = status ? status.channels : [];
  const alarming = statusChannels.flatMap((channelStatus) => (channelStatus.alarm === null
    ? [] : [{ channelStatus, kind: channelStatus.alarm }]));
  const events = status ? (status.alarm_events ?? []) : [];
  const hover = hoverT !== null && buckets.length > 0 ? buckets[nearestIndex(buckets, hoverT)] : null;

  // Drop the oldest local rows when the backend reports it evicted more.
  const absorbDropped = (dropped: number) => {
    const grew = dropped - droppedRef.current;
    droppedRef.current = dropped;
    if (grew > 0) setRows((prev) => prev.slice(grew));
  };

  const refreshNotes = async () => {
    try {
      const { notes: next } = await api.milMax6675Notes();
      setNotes(next);
    } catch {
      // Best effort: the next poll or idle refresh tries again.
    }
  };

  // The first status sets the baseline, so only alarms raised after page load become desktop alerts.
  const announceAlarms = (alarmEvents: Max6675AlarmEvent[]) => {
    const newest = alarmEvents.reduce((latestT, event) => Math.max(latestT, event.t), 0);
    const seen = alarmSeenRef.current;
    alarmSeenRef.current = seen === null ? newest : Math.max(seen, newest);
    if (seen === null || !alertsRef.current || !notificationsAvailable() || Notification.permission !== 'granted') return;
    for (const event of alarmEvents) {
      if (event.t > seen && event.kind !== 'clear') {
        try {
          new Notification('MAX6675 alarm', { body: alarmSummary(event.name, event.kind, event.t) });
        } catch {
          // Some mobile browsers only allow notifications from a service worker; the banner still shows.
        }
      }
    }
  };

  const syncStatus = (next: Max6675Status) => {
    setStatus(next);
    setLatest(next.channels.map((channel) => channel.last));
    absorbDropped(next.dropped_rows);
    announceAlarms(next.alarm_events ?? []);
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
        setChannels(next.channels.map(channelConfig));
        setStopAfter(next.stop_after_s ?? null);
        setAutoResume(next.auto_resume ?? true);
        setAutoReconnect(next.auto_reconnect ?? true);
        void refreshNotes();
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
      void refreshNotes();
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
      void refreshNotes();
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

  const updateChannel = (index: number, patch: Partial<Max6675ChannelConfig>) =>
    setChannels((prev) => prev.map((channel, i) => (i === index ? { ...channel, ...patch } : channel)));

  const chooseUnit = (value: TempUnit) => {
    setUnit(value);
    writeStored(UNIT_KEY, value);
  };

  const toggleAlerts = (checked: boolean) => {
    setDesktopAlerts(checked);
    alertsRef.current = checked;
    writeStored(ALERTS_KEY, checked ? '1' : '0');
    if (checked && notificationsAvailable()) void Notification.requestPermission();
  };

  const pointerMoved = (event: ReactPointerEvent<SVGSVGElement>) => {
    setHoverT(timeAtPointer(event.clientX, event.currentTarget.getBoundingClientRect(), span));
  };

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

  const clear = () => {
    if (!window.confirm('Clear the whole log? This also deletes the saved log file.')) return;
    void run(async () => {
      const next = await api.milMax6675Clear();
      setRows([]);
      setNotes([]);
      seqRef.current = next.next_seq;
      droppedRef.current = next.dropped_rows;
      syncStatus(next);
    });
  };

  const addNote = () => run(async () => {
    await api.milMax6675AddNote(noteText.trim());
    setNoteText('');
    await refreshNotes();
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
      {alarming.length > 0 && (
        <div className="finding error" role="alert">
          {alarming.map(({ channelStatus, kind }) => (
            <div key={channelStatus.name}>{alarmSummary(channelStatus.name, kind, channelStatus.alarm_since)}</div>
          ))}
        </div>
      )}
      {status && status.paused_reason !== null && (
        <div className="finding warning" role="status">Logging paused: {status.paused_reason}</div>
      )}
      {status && status.stopped_reason && (
        <div className="finding warning" role="status">Logging stopped: {status.stopped_reason}</div>
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
      <div className="max6675-options">
        <label className="field"><span>Stop after</span>
          <select value={stopAfter === null ? 'none' : String(stopAfter)} disabled={running}
            onChange={(event) => setStopAfter(event.target.value === 'none' ? null : Number(event.target.value))}>
            {STOP_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="field checkbox">
          <input type="checkbox" checked={autoResume} disabled={running}
            onChange={(event) => setAutoResume(event.target.checked)} />
          <span>Resume after a backend restart</span>
        </label>
        <label className="field checkbox">
          <input type="checkbox" checked={autoReconnect} disabled={running}
            onChange={(event) => setAutoReconnect(event.target.checked)} />
          <span>Reconnect the board automatically</span>
        </label>
        <label className="field"><span>Units</span>
          <select data-testid="max6675-unit" value={unit}
            onChange={(event) => chooseUnit(event.target.value === 'F' ? 'F' : 'C')}>
            <option value="C">°C</option>
            <option value="F">°F</option>
          </select>
        </label>
        <label className="field checkbox">
          <input type="checkbox" checked={desktopAlerts} onChange={(event) => toggleAlerts(event.target.checked)} />
          <span>Desktop alerts</span>
        </label>
      </div>
      <div className="max6675-channels">
        {channels.map((channel, index) => {
          const number = index + 1;
          const reading = latest[index];
          const alarm = statusChannels[index]?.alarm ?? null;
          return (
            <div className={`max6675-channel${alarm ? ' alarming' : ''}`} key={number}>
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
                  <strong>{readingText(reading, unit)}</strong>{' '}
                  <span className="badge">{reading ? READING_LABELS[reading.status] : 'No reading yet'}</span>{' '}
                  {alarm && <span className="badge alarm-badge">{ALARM_LABEL[alarm]}</span>}
                </span>
              </div>
              {isSteadyZero(rows, index) && (
                <p className="hint max6675-steady">A steady 0.00 °C usually means the SCK pin is wrong.</p>
              )}
            </div>
          );
        })}
      </div>
      {problems.length > 0 && (
        <div className="finding warning">
          {problems.map((problem, index) => <div key={index}>{problem}</div>)}
        </div>
      )}
      <details className="max6675-calibration-section">
        <summary>Calibration and alarms</summary>
        <div className="max6675-calibration">
          {channels.map((channel, index) => {
            const number = index + 1;
            return (
              <div className="max6675-calibration-channel" key={number}>
                {/* Keyed by unit so a half-typed value is dropped when the unit changes. */}
                <NumberField key={`offset-${unit}`} label={`Channel ${number} offset (${symbol})`} value={channel.offset_c}
                  disabled={running} step={0.01} show={(stored) => displayDelta(stored, unit)}
                  store={(shown) => storedDelta(shown, unit)}
                  onChange={(stored) => updateChannel(index, { offset_c: stored ?? 0 })} />
                <NumberField key={`low-${unit}`} label={`Channel ${number} low alarm (${symbol})`} value={channel.alarm_low_c}
                  disabled={running} step={0.1} show={(stored) => displayTemp(stored, unit)}
                  store={(shown) => storedTemp(shown, unit)}
                  onChange={(stored) => updateChannel(index, { alarm_low_c: stored })} />
                <NumberField key={`high-${unit}`} label={`Channel ${number} high alarm (${symbol})`} value={channel.alarm_high_c}
                  disabled={running} step={0.1} show={(stored) => displayTemp(stored, unit)}
                  store={(shown) => storedTemp(shown, unit)}
                  onChange={(stored) => updateChannel(index, { alarm_high_c: stored })} />
                <label className="field checkbox">
                  <input type="checkbox" checked={channel.alarm_on_fault} disabled={running}
                    onChange={(event) => updateChannel(index, { alarm_on_fault: event.target.checked })} />
                  <span>{`Channel ${number} alert on sensor fault`}</span>
                </label>
              </div>
            );
          })}
        </div>
      </details>
      <div className="max6675-legend">
        {enabledChannels.map((index) => (
          <span className="max6675-legend-item" key={index} data-testid={`max6675-legend-${index + 1}`}>
            <span className="max6675-swatch" aria-hidden="true" style={{ background: CHANNEL_COLOURS[index] }} />
            {channels[index].name}
          </span>
        ))}
      </div>
      <div className="max6675-graph">
        <svg viewBox={`0 0 ${VIEW.width} ${VIEW.height}`} role="img" aria-label="Temperature graph"
          className="max6675-plot" onPointerMove={pointerMoved} onPointerDown={pointerMoved}
          onPointerLeave={() => setHoverT(null)}>
          {rows.length === 0 ? (
            <text x={360} y={120} textAnchor="middle" fill="currentColor">No readings yet</text>
          ) : (
            <>
              <text x={2} y={10} fontSize={11} fill="currentColor">{symbol}</text>
              {[0, 1, 2, 3, 4].map((step) => {
                const temp = range.min + (range.max - range.min) * step / 4;
                const y = PLOT.y + PLOT.height - PLOT.height * step / 4;
                return (
                  <g key={step}>
                    <line x1={PLOT.x} x2={PLOT.x + PLOT.width} y1={y} y2={y} stroke="currentColor" opacity={0.2} />
                    <text x={PLOT.x - 6} y={y + 4} textAnchor="end" fontSize={11} fill="currentColor">
                      {displayTemp(temp, unit).toFixed(1)}
                    </text>
                  </g>
                );
              })}
              {enabledChannels.map((index) => {
                const paths = channelPaths(buckets, index, span, range);
                return (
                  <g key={index}>
                    <path data-testid={`max6675-band-${index + 1}`} fill={CHANNEL_COLOURS[index]} opacity={0.2}
                      transform={`translate(${PLOT.x} ${PLOT.y})`} d={paths.band} />
                    <path data-testid={`max6675-line-${index + 1}`} fill="none"
                      stroke={CHANNEL_COLOURS[index]} strokeWidth={2}
                      transform={`translate(${PLOT.x} ${PLOT.y})`} d={paths.line} />
                  </g>
                );
              })}
              {enabledChannels.flatMap((index) => (['alarm_high_c', 'alarm_low_c'] as const).map((field) => {
                const value = channels[index][field];
                if (value === null || value < range.min || value > range.max) return null;
                const y = PLOT.y + yFor(value, range);
                return (
                  <line key={`${index}-${field}`} data-testid={`max6675-threshold-${index + 1}-${field}`}
                    x1={PLOT.x} x2={PLOT.x + PLOT.width} y1={y} y2={y}
                    stroke={CHANNEL_COLOURS[index]} strokeWidth={1.5} strokeDasharray="6 4" />
                );
              }))}
              {notes.filter((note) => inSpan(note.t, span)).map((note, i) => {
                const x = PLOT.x + xFor(note.t, span);
                return (
                  <line key={i} data-testid="max6675-note-marker" x1={x} x2={x} y1={PLOT.y}
                    y2={PLOT.y + PLOT.height} stroke="currentColor" opacity={0.6} strokeDasharray="4 3">
                    <title>{note.text}</title>
                  </line>
                );
              })}
              {hover && (
                <line data-testid="max6675-cursor" x1={PLOT.x + xFor(hover.t, span)} x2={PLOT.x + xFor(hover.t, span)}
                  y1={PLOT.y} y2={PLOT.y + PLOT.height} stroke="currentColor" opacity={0.5} />
              )}
              {axisLabels(span).map((label, i, all) => (
                <text key={i} x={PLOT.x + label.x} y={232} fontSize={11} fill="currentColor"
                  textAnchor={i === 0 ? 'start' : i === all.length - 1 ? 'end' : 'middle'}>
                  {label.text}
                </text>
              ))}
            </>
          )}
        </svg>
        {hover && (
          <div className="max6675-tooltip" data-testid="max6675-tooltip"
            style={{ left: `${(PLOT.x + xFor(hover.t, span)) / VIEW.width * 100}%` }}>
            <div>{timeText(hover.t, span)}</div>
            {enabledChannels.map((index) => {
              const stats = hover.values[index];
              return (
                <div key={index}>
                  <span className="max6675-swatch" aria-hidden="true" style={{ background: CHANNEL_COLOURS[index] }} />{' '}
                  {`${channels[index].name}: ${stats ? `${displayTemp(stats.mean, unit).toFixed(2)} ${symbol}` : '—'}`}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="max6675-notes-input">
        <label className="field"><span>Note text</span>
          <input type="text" maxLength={200} value={noteText} onChange={(event) => setNoteText(event.target.value)} />
        </label>
        <button disabled={busy || !controlMode || noteText.trim() === ''} onClick={addNote}>Add note</button>
      </div>
      {notes.length > 0 && (
        <ul className="max6675-notes" aria-label="Notes">
          {[...notes].reverse().map((note, i) => (
            <li key={i}><span>{formatClock(note.t)}</span> {note.text}</li>
          ))}
        </ul>
      )}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Channel</th><th>Samples</th><th>{`Min (${symbol})`}</th><th>{`Max (${symbol})`}</th>
              <th>{`Mean (${symbol})`}</th><th>{`Latest (${symbol})`}</th><th>Rate (°/min)</th>
            </tr>
          </thead>
          <tbody>
            {enabledChannels.map((index) => {
              const stats = channelStats(visible, index);
              const rate = ratePerMinute(visible, index);
              const latestValue = latestTemp(latest[index], unit);
              return (
                <tr key={index}>
                  <td>{channels[index].name}</td>
                  <td>{stats ? stats.count : '—'}</td>
                  <td>{stats ? displayTemp(stats.min, unit).toFixed(2) : '—'}</td>
                  <td>{stats ? displayTemp(stats.max, unit).toFixed(2) : '—'}</td>
                  <td>{stats ? displayTemp(stats.mean, unit).toFixed(2) : '—'}</td>
                  <td>{latestValue === null ? '—' : latestValue.toFixed(2)}</td>
                  <td>{rate === null ? '—' : displayDelta(rate, unit).toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <details className="max6675-events">
        <summary>{`Alarm events (${Math.min(events.length, ALARM_EVENTS_SHOWN)} newest)`}</summary>
        <ul>
          {events.slice(-ALARM_EVENTS_SHOWN).reverse().map((event, i) => (
            <li key={i}>
              {`${formatClock(event.t)} ${event.name}: ${ALARM_LABEL[event.kind]}`}
              {event.temp_c === null ? '' : ` at ${displayTemp(event.temp_c, unit).toFixed(2)} ${symbol}`}
            </li>
          ))}
        </ul>
      </details>
      <p className="hint">{`${status ? status.row_count : rows.length} readings logged`}</p>
      {status && status.dropped_rows > 0 && (
        <div className="finding warning">{`Oldest ${status.dropped_rows} readings were discarded (log is full)`}</div>
      )}
    </section>
  );
}

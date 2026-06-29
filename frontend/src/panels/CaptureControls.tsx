import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

const DIGITAL_DEEP_MAX_RATE = 14e6;
const DIGITAL_FAST_DEPTH = 1024;
// Full 64 Mbit x16 SDRAM = 4,194,304 words (hardware-validated deep capture).
const DIGITAL_SDRAM_DEPTH = 4_194_304;
const DIGITAL_NARROW_MAX_SAMPLES = DIGITAL_SDRAM_DEPTH * 16;
const DIGITAL_RATES = [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 12.5e6, 14e6, 20e6, 50e6, 100e6, 200e6];
const DIGITAL_DEEP_RATES = DIGITAL_RATES.filter((rate) => rate <= DIGITAL_DEEP_MAX_RATE);
const DIGITAL_DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000, 500_000, 1_048_576, 2_097_152, DIGITAL_SDRAM_DEPTH];
const DIGITAL_FAST_DEPTHS = [DIGITAL_FAST_DEPTH];
const MIXED_RATES = [125e3];
const ANALOG_FAST_RATES = [100e3, 200e3, 500e3, 1e6];
const ANALOG_ALL_RATES = [125e3];
const ANALOG_DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000];
const ROLLING_WINDOW_SECONDS = [
  100e-6, 500e-6, 1e-3, 5e-3, 10e-3, 50e-3,
  100e-3, 500e-3, 1, 5,
];
const ALL_DIGITAL = Array.from({ length: 16 }, (_, i) => i);
const NO_DIGITAL: number[] = [];

type CaptureMode = 'single' | 'continuous' | 'rolling' | 'digital_narrow' | 'triggered' | 'analog'
  | 'analog_fast' | 'analog_all' | 'mixed' | 'analog_continuous'
  | 'analog_all_continuous' | 'mixed_continuous';

const ANALOG_MODES: CaptureMode[] = [
  'analog', 'analog_fast', 'analog_all', 'mixed',
  'analog_continuous', 'analog_all_continuous', 'mixed_continuous',
];
const ANALOG_ONLY_MODES: CaptureMode[] = [
  'analog', 'analog_fast', 'analog_all',
  'analog_continuous', 'analog_all_continuous',
];
const ROLLING_MODES: CaptureMode[] = [
  'continuous', 'rolling', 'digital_narrow', 'analog_continuous',
  'analog_all_continuous', 'mixed_continuous',
];

const MODE_OPTIONS: {
  mode: CaptureMode;
  label: string;
  detail: string;
  channels: string;
}[] = [
  {
    mode: 'single',
    label: 'Full-speed digital',
    detail: '200 MHz at 1k depth, 14 MHz full-width deep',
    channels: 'd0-d15',
  },
  {
    mode: 'mixed',
    label: 'Mixed',
    detail: '16 digital + 8 ADC scan channels',
    channels: 'd0-d15 + ADC0-ADC7',
  },
  {
    mode: 'digital_narrow',
    label: '200 MHz narrow rolling',
    detail: '1 digital channel packed for long gapless rolling',
    channels: 'one digital channel',
  },
  {
    mode: 'analog_fast',
    label: 'High-speed analog',
    detail: '1 physical analog input at best ADC rate',
    channels: 'a1',
  },
  {
    mode: 'analog_all',
    label: 'Maximum analog',
    detail: 'All physical analog inputs at best detail',
    channels: 'a1, a2, a3, a4, a5, a7, a8, a16',
  },
];

function formatRate(rate: number) {
  return rate >= 1e6 ? `${rate / 1e6} MHz` : `${rate / 1e3} kHz`;
}

function formatWindow(seconds: number) {
  if (seconds >= 1) return `${seconds.toFixed(seconds >= 5 ? 0 : 1)} s`;
  if (seconds >= 1e-3) {
    const ms = seconds * 1e3;
    return `${ms.toFixed(ms >= 10 ? 0 : 1)} ms`;
  }
  return `${(seconds * 1e6).toFixed(0)} us`;
}

function samplesForWindow(sampleRate: number, seconds: number) {
  return Math.max(1, Math.round(sampleRate * seconds));
}

function maxWindowSamplesForMode(mode: CaptureMode) {
  if (mode === 'digital_narrow') return DIGITAL_NARROW_MAX_SAMPLES;
  return DIGITAL_SDRAM_DEPTH;
}

function windowOptionsForMode(mode: CaptureMode, sampleRate: number) {
  const maxSamples = maxWindowSamplesForMode(mode);
  return ROLLING_WINDOW_SECONDS.filter(
    (seconds) => samplesForWindow(sampleRate, seconds) <= maxSamples,
  );
}

function nearestWindowSeconds(samples: number, sampleRate: number, mode: CaptureMode) {
  return nearestWindowSecondsForDuration(samples / Math.max(1, sampleRate), sampleRate, mode);
}

function nearestWindowSecondsForDuration(durationSeconds: number, sampleRate: number, mode: CaptureMode) {
  const options = windowOptionsForMode(mode, sampleRate);
  return options.reduce((best, value) => (
    Math.abs(value - durationSeconds) < Math.abs(best - durationSeconds) ? value : best
  ), options[0] ?? ROLLING_WINDOW_SECONDS[0]);
}

function rateOptionsForMode(mode: CaptureMode, numSamples = DIGITAL_FAST_DEPTH) {
  if (mode === 'analog_fast' || mode === 'analog' || mode === 'analog_continuous') {
    return ANALOG_FAST_RATES;
  }
  if (mode === 'analog_all' || mode === 'analog_all_continuous') {
    return ANALOG_ALL_RATES;
  }
  if (mode === 'mixed' || mode === 'mixed_continuous') {
    return MIXED_RATES;
  }
  if (mode === 'digital_narrow') {
    return [200e6];
  }
  if (ROLLING_MODES.includes(mode)) {
    return DIGITAL_DEEP_RATES;
  }
  if (numSamples > DIGITAL_FAST_DEPTH) {
    return DIGITAL_DEEP_RATES;
  }
  return DIGITAL_RATES;
}

function depthOptionsForMode(mode: CaptureMode, sampleRate: number) {
  if (ANALOG_MODES.includes(mode)) {
    return ANALOG_DEPTHS;
  }
  if (sampleRate > DIGITAL_DEEP_MAX_RATE) {
    return DIGITAL_FAST_DEPTHS;
  }
  return DIGITAL_DEPTHS;
}

function labelForMode(mode: CaptureMode) {
  const opt = MODE_OPTIONS.find((o) => o.mode === mode);
  if (opt) return opt.label;
  if (mode === 'continuous') return 'Digital continuous';
  if (mode === 'rolling') return 'Digital rolling';
  if (mode === 'digital_narrow') return '200 MHz narrow rolling';
  if (mode === 'mixed_continuous') return 'Mixed continuous';
  if (mode === 'analog_continuous') return 'High-speed analog continuous';
  if (mode === 'analog_all_continuous') return 'Maximum analog continuous';
  return mode;
}

export function CaptureControls() {
  const { status, captureSettings, setCaptureSettings, toast, controlMode } = useApp();
  const [scenarios, setScenarios] = useState<{ id: string; name: string }[]>([]);
  const [findings, setFindings] = useState<{ level: string; message: string }[]>([]);
  const [name, setName] = useState('');

  const connected = status?.device_connected ?? false;
  const capturing = status?.capture_state === 'capturing' || status?.capture_state === 'armed';
  const isMock = status?.device_kind === 'mock';
  const hasErrors = findings.some((f) => f.level === 'error');
  const analogMode = ANALOG_MODES.includes(captureSettings.mode as CaptureMode);
  const analogOnlyMode = ANALOG_ONLY_MODES.includes(captureSettings.mode as CaptureMode);
  const rollingMode = ROLLING_MODES.includes(captureSettings.mode as CaptureMode);
  const rateOptions = rateOptionsForMode(
    captureSettings.mode as CaptureMode,
    captureSettings.num_samples,
  );
  const depthOptions = depthOptionsForMode(
    captureSettings.mode as CaptureMode,
    captureSettings.sample_rate,
  );
  const windowOptions = useMemo(
    () => windowOptionsForMode(
      captureSettings.mode as CaptureMode,
      captureSettings.sample_rate,
    ),
    [captureSettings.mode, captureSettings.sample_rate],
  );
  const activeModeLabel = labelForMode(captureSettings.mode as CaptureMode);
  const activeWindowSeconds = captureSettings.num_samples / Math.max(1, captureSettings.sample_rate);

  useEffect(() => {
    if (isMock) api.mockScenarios().then((r) => setScenarios(r.scenarios)).catch(() => {});
    else setScenarios([]);
  }, [isMock, connected]);

  useEffect(() => {
    if (!connected) return;
    const t = setTimeout(() => {
      api.validateSettings(captureSettings)
        .then((r) => setFindings(r.findings))
        .catch(() => setFindings([]));
    }, 300);
    return () => clearTimeout(t);
  }, [captureSettings, connected]);

  useEffect(() => {
    const digitalSelectionOk = !analogOnlyMode
      || captureSettings.enabled_digital.length === 0;
    const sampleWindowOk = rollingMode
      ? captureSettings.num_samples <= maxWindowSamplesForMode(captureSettings.mode as CaptureMode)
      : depthOptions.includes(captureSettings.num_samples);
    if (rateOptions.includes(captureSettings.sample_rate)
        && sampleWindowOk
        && captureSettings.analog_enabled === analogMode
        && digitalSelectionOk) {
      return;
    }
    setCaptureSettings({
      analog_enabled: analogMode,
      sample_rate: rateOptions.includes(captureSettings.sample_rate)
        ? captureSettings.sample_rate
        : rateOptions[rateOptions.length - 1],
      num_samples: sampleWindowOk
        ? captureSettings.num_samples
        : rollingMode
          ? samplesForWindow(
            captureSettings.sample_rate,
            nearestWindowSeconds(
              captureSettings.num_samples,
              captureSettings.sample_rate,
              captureSettings.mode as CaptureMode,
            ),
          )
          : depthOptions[Math.min(depthOptions.length - 1, 1)],
      enabled_digital: analogOnlyMode
        ? NO_DIGITAL
        : captureSettings.enabled_digital?.length
          ? captureSettings.enabled_digital
          : ALL_DIGITAL,
    });
  }, [
    analogMode,
    analogOnlyMode,
    captureSettings.analog_enabled,
    captureSettings.enabled_digital,
    captureSettings.mode,
    captureSettings.num_samples,
    captureSettings.sample_rate,
    depthOptions,
    rateOptions,
    rollingMode,
    setCaptureSettings,
    windowOptions,
  ]);

  const setMode = (mode: CaptureMode) => {
    const isAnalog = ANALOG_MODES.includes(mode);
    const isAnalogOnly = ANALOG_ONLY_MODES.includes(mode);
    const isRolling = ROLLING_MODES.includes(mode);
    const isNarrow = mode === 'digital_narrow';
    const nextRates = rateOptionsForMode(mode, captureSettings.num_samples);
    const maxRate = nextRates[nextRates.length - 1];
    const nextDepths = depthOptionsForMode(mode, maxRate);
    const nearestWindow = nearestWindowSecondsForDuration(
      activeWindowSeconds,
      maxRate,
      mode,
    );
    const numSamples = isRolling
      ? samplesForWindow(maxRate, nearestWindow)
      : nextDepths.includes(captureSettings.num_samples)
        ? captureSettings.num_samples
        : nextDepths[nextDepths.length - 1];
    setCaptureSettings({
      mode,
      analog_enabled: isAnalog,
      enabled_digital: isAnalogOnly ? NO_DIGITAL : isNarrow ? [0] : ALL_DIGITAL,
      sample_rate: maxRate,
      num_samples: numSamples,
    });
  };

  const start = async () => {
    try {
      await api.startCapture(captureSettings, name);
    } catch (e: any) {
      toast('error', e.message);
    }
  };
  const stop = async () => {
    try { await api.stopCapture(); } catch (e: any) { toast('error', e.message); }
  };

  const duration = captureSettings.num_samples / captureSettings.sample_rate;

  return (
    <div className="panel-body">
      <label className="field">
        <span>Capture name</span>
        <input value={name} placeholder="(auto)" onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="field">
        <span>Mode</span>
        <div className="mode-grid">
          {MODE_OPTIONS.map((opt) => (
            <button
              key={opt.mode}
              type="button"
              className={`mode-tile ${captureSettings.mode === opt.mode ? 'active' : ''}`}
              onClick={() => setMode(opt.mode)}
              title={opt.detail}
            >
              <span className="mode-title">{opt.label}</span>
              <span className="mode-detail">{opt.detail}</span>
              <span className="mode-channels mono">{opt.channels}</span>
            </button>
          ))}
        </div>
        <select value={captureSettings.mode}
          title={`Current mode: ${activeModeLabel}`}
          onChange={(e) => setMode(e.target.value as CaptureMode)}>
          <option value="single">Full-speed digital</option>
          <option value="continuous">Digital continuous</option>
          <option value="rolling">Digital rolling</option>
          <option value="digital_narrow">200 MHz narrow rolling</option>
          <option value="mixed">Mixed digital + analog</option>
          <option value="analog_fast">High-speed analog</option>
          <option value="analog_all">Maximum analog</option>
          <option value="analog_continuous">High-speed analog continuous</option>
          <option value="analog_all_continuous">Maximum analog continuous</option>
          <option value="mixed_continuous">Mixed continuous</option>
        </select>
      </div>
      <label className="field">
        <span>Sample rate</span>
        <select value={captureSettings.sample_rate}
          onChange={(e) => {
            const sample_rate = Number(e.target.value);
            const patch: Partial<typeof captureSettings> = { sample_rate };
            if (rollingMode) {
              const seconds = nearestWindowSecondsForDuration(
                activeWindowSeconds,
                sample_rate,
                captureSettings.mode as CaptureMode,
              );
              patch.num_samples = samplesForWindow(sample_rate, seconds);
            }
            setCaptureSettings(patch);
          }}>
          {rateOptions.map((rate) => (
            <option key={rate} value={rate}>{formatRate(rate)}</option>
          ))}
          {!rateOptions.includes(captureSettings.sample_rate) && (
            <option value={captureSettings.sample_rate}>{formatRate(captureSettings.sample_rate)}</option>
          )}
        </select>
      </label>
      {rollingMode ? (
        <label className="field">
          <span>Screen window</span>
          <select value={nearestWindowSeconds(
            captureSettings.num_samples,
            captureSettings.sample_rate,
            captureSettings.mode as CaptureMode,
          )}
            onChange={(e) => setCaptureSettings({
              num_samples: samplesForWindow(captureSettings.sample_rate, Number(e.target.value)),
            })}>
            {windowOptions.map((seconds) => (
              <option key={seconds} value={seconds}>
                {formatWindow(seconds)} ({samplesForWindow(captureSettings.sample_rate, seconds).toLocaleString()} samples)
              </option>
            ))}
            {!windowOptions.includes(activeWindowSeconds) && (
              <option value={activeWindowSeconds}>
                {formatWindow(activeWindowSeconds)} ({captureSettings.num_samples.toLocaleString()} samples)
              </option>
            )}
          </select>
        </label>
      ) : (
        <label className="field">
          <span>Samples</span>
          <select value={captureSettings.num_samples}
            onChange={(e) => setCaptureSettings({ num_samples: Number(e.target.value) })}>
            {depthOptions.map((depth) => <option key={depth} value={depth}>{depth.toLocaleString()}</option>)}
          </select>
        </label>
      )}
      <div className="capture-summary">
        <span>{activeModeLabel}</span>
        <span>{formatRate(captureSettings.sample_rate)}</span>
        <span>{duration >= 1 ? `${duration.toFixed(2)} s` : `${(duration * 1e3).toFixed(2)} ms`}</span>
        <span>{analogOnlyMode ? 'analog only' : analogMode ? 'digital + analog' : 'digital only'}</span>
      </div>
      {(captureSettings.mode === 'single' || captureSettings.mode === 'analog'
        || captureSettings.mode === 'analog_fast' || captureSettings.mode === 'analog_all'
        || captureSettings.mode === 'mixed') && (
        <label className="field">
          <span>Repeat N</span>
          <input type="number" min={1} max={100} value={captureSettings.repeat_count}
            onChange={(e) => setCaptureSettings({ repeat_count: Math.max(1, Number(e.target.value)) })} />
        </label>
      )}
      {isMock && (
        <label className="field">
          <span>Mock scenario</span>
          <select value={captureSettings.mock_scenario ?? 'demo_mixed'}
            onChange={(e) => setCaptureSettings({ mock_scenario: e.target.value })}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      )}
      {findings.map((f, i) => (
        <div key={i} className={`finding ${f.level}`}>{f.message}</div>
      ))}
      <div className="button-row">
        {!capturing ? (
          <button className="primary big" disabled={!connected || !controlMode || hasErrors} onClick={start}>
            Capture
          </button>
        ) : (
          <button className="danger big" onClick={stop} disabled={!controlMode}>Stop</button>
        )}
      </div>
      {capturing && status && (
        <div className="progress">
          <div className="progress-bar" style={{
            width: `${(status.capture_progress.samples_read /
              Math.max(1, status.capture_progress.samples_total)) * 100}%`,
          }} />
          <span>{status.capture_progress.samples_read.toLocaleString()} / {status.capture_progress.samples_total.toLocaleString()} ({status.capture_progress.message})</span>
        </div>
      )}
      {!connected && <div className="hint">Connect a device on the Device page first.</div>}
      {!controlMode && <div className="finding warning">Read-only viewer mode: controls disabled.</div>}
    </div>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

const DIGITAL_FAST_DEPTH = 1024;
const DIGITAL_SDRAM_DEPTH = 4_194_304;
const DIGITAL_NARROW_MAX_SAMPLES = DIGITAL_SDRAM_DEPTH * 16;
const DIGITAL_RATES = [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 12.5e6, 14e6, 20e6, 50e6, 100e6, 200e6];
// Live rolling uses repeated finite captures, so keep the selector below the
// hardware-backed ceiling that still behaves cleanly on the real board.
const DIGITAL_ROLLING_MAX_RATE = 50e6;
const DIGITAL_ROLLING_RATES = DIGITAL_RATES.filter((rate) => rate <= DIGITAL_ROLLING_MAX_RATE);
const DIGITAL_DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000, 500_000, 1_048_576, 2_097_152, DIGITAL_SDRAM_DEPTH];
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
type ReadbackCompression = 'raw' | 'delta_rle' | 'delta' | 'rle';

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

type CaptureSource = 'digital' | 'mixed' | 'digital_narrow' | 'analog_fast' | 'analog_all';
type Acquisition = 'single' | 'live';

const SOURCES: {
  source: CaptureSource;
  label: string;
  detail: string;
  channels: string;
  liveOnly?: boolean;
}[] = [
  {
    source: 'digital',
    label: 'Digital deep',
    detail: '16 inputs, up to 200 MHz, deep SDRAM reads up to 4,194,304 samples',
    channels: 'D0-D15',
  },
  {
    source: 'mixed',
    label: 'Mixed scan',
    detail: '16 digital + ADC0-ADC7 at the scan frame rate',
    channels: 'D0-D15 + ADC0-ADC7',
  },
  {
    source: 'digital_narrow',
    label: 'Packed narrow',
    detail: 'One digital line packed into a 16x deeper logical stream',
    channels: 'one digital line',
    liveOnly: true,
  },
  {
    source: 'analog_fast',
    label: 'Analog fast',
    detail: 'One physical analog input at the highest ADC rate',
    channels: 'one analog input',
  },
  {
    source: 'analog_all',
    label: 'Analog wide',
    detail: 'All exposed analog inputs, including the dedicated AIN pin',
    channels: 'ADC1,2,3,4,5,7,8,16',
  },
];

function modeForSource(source: CaptureSource, acq: Acquisition): CaptureMode {
  switch (source) {
    case 'digital':
      return acq === 'live' ? 'rolling' : 'single';
    case 'digital_narrow':
      return 'digital_narrow';
    case 'mixed':
      return acq === 'live' ? 'mixed_continuous' : 'mixed';
    case 'analog_fast':
      return acq === 'live' ? 'analog_continuous' : 'analog_fast';
    case 'analog_all':
      return acq === 'live' ? 'analog_all_continuous' : 'analog_all';
  }
}

function sourceForMode(mode: CaptureMode): CaptureSource {
  if (mode === 'digital_narrow') return 'digital_narrow';
  if (mode === 'mixed' || mode === 'mixed_continuous') return 'mixed';
  if (mode === 'analog_fast' || mode === 'analog' || mode === 'analog_continuous') return 'analog_fast';
  if (mode === 'analog_all' || mode === 'analog_all_continuous') return 'analog_all';
  return 'digital';
}

function acquisitionForMode(mode: CaptureMode): Acquisition {
  return ROLLING_MODES.includes(mode) ? 'live' : 'single';
}

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
  return ROLLING_WINDOW_SECONDS.filter((seconds) => samplesForWindow(sampleRate, seconds) <= maxSamples);
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

function rateOptionsForMode(mode: CaptureMode) {
  if (mode === 'analog_fast' || mode === 'analog' || mode === 'analog_continuous') return ANALOG_FAST_RATES;
  if (mode === 'analog_all' || mode === 'analog_all_continuous') return ANALOG_ALL_RATES;
  if (mode === 'mixed' || mode === 'mixed_continuous') return MIXED_RATES;
  if (mode === 'digital_narrow') return [200e6];
  if (ROLLING_MODES.includes(mode)) return DIGITAL_ROLLING_RATES;
  return DIGITAL_RATES;
}

function depthOptionsForMode(mode: CaptureMode) {
  return ANALOG_MODES.includes(mode) ? ANALOG_DEPTHS : DIGITAL_DEPTHS;
}

function labelForMode(mode: CaptureMode) {
  const src = SOURCES.find((s) => s.source === sourceForMode(mode));
  if (!src) return mode;
  if (src.liveOnly) return src.label;
  return acquisitionForMode(mode) === 'live' ? `${src.label} (live)` : src.label;
}

function hardwareSummary(mode: CaptureMode) {
  switch (mode) {
    case 'single':
      return 'Deep digital capture uses the full 16-channel probe pool and SDRAM readback.';
    case 'rolling':
      return 'Live digital capture uses the SDRAM ring. Above the readback ceiling, newest samples are retained.';
    case 'digital_narrow':
      return 'Packed narrow mode keeps one line at 200 MHz and stretches it to a much longer logical stream.';
    case 'mixed':
    case 'mixed_continuous':
      return 'Mixed mode captures 16 digital bits plus ADC0-ADC7 at a shared scan frame rate.';
    case 'analog_fast':
    case 'analog_continuous':
      return 'High-speed analog uses one physical analog input at the best ADC rate.';
    case 'analog_all':
    case 'analog_all_continuous':
      return 'Maximum analog exposes the full board analog map, including the dedicated AIN pin.';
    default:
      return 'This mode is selected by the MAX1000 hardware profile.';
  }
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
  const rateOptions = rateOptionsForMode(captureSettings.mode as CaptureMode);
  const depthOptions = depthOptionsForMode(captureSettings.mode as CaptureMode);
  const windowOptions = useMemo(
    () => windowOptionsForMode(captureSettings.mode as CaptureMode, captureSettings.sample_rate),
    [captureSettings.mode, captureSettings.sample_rate],
  );
  const activeModeLabel = labelForMode(captureSettings.mode as CaptureMode);
  const activeWindowSeconds = captureSettings.num_samples / Math.max(1, captureSettings.sample_rate);
  const digitalCompressionMode = !analogMode
    && captureSettings.mode !== 'mixed'
    && captureSettings.mode !== 'mixed_continuous';

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
      readback_compression: (!analogMode
        && captureSettings.mode !== 'mixed'
        && captureSettings.mode !== 'mixed_continuous')
        ? captureSettings.readback_compression
        : 'raw',
    });
  }, [
    analogMode,
    analogOnlyMode,
    captureSettings.analog_enabled,
    captureSettings.enabled_digital,
    captureSettings.mode,
    captureSettings.num_samples,
    captureSettings.readback_compression,
    captureSettings.sample_rate,
    depthOptions,
    rateOptions,
    rollingMode,
    setCaptureSettings,
  ]);

  const setMode = (mode: CaptureMode) => {
    const isAnalog = ANALOG_MODES.includes(mode);
    const isAnalogOnly = ANALOG_ONLY_MODES.includes(mode);
    const isRolling = ROLLING_MODES.includes(mode);
    const isNarrow = mode === 'digital_narrow';
    const nextRates = rateOptionsForMode(mode);
    const maxRate = nextRates[nextRates.length - 1];
    const nextDepths = depthOptionsForMode(mode);
    const nearestWindow = nearestWindowSecondsForDuration(activeWindowSeconds, maxRate, mode);
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
      readback_compression: sourceForMode(mode) === 'digital' || mode === 'digital_narrow'
        ? captureSettings.readback_compression
        : 'raw',
    });
  };

  const currentSource = sourceForMode(captureSettings.mode as CaptureMode);
  const currentAcq = acquisitionForMode(captureSettings.mode as CaptureMode);
  const currentSourceLiveOnly = !!SOURCES.find((s) => s.source === currentSource)?.liveOnly;

  const selectSource = (source: CaptureSource) => {
    const liveOnly = !!SOURCES.find((s) => s.source === source)?.liveOnly;
    setMode(modeForSource(source, liveOnly ? 'live' : currentAcq));
  };
  const selectAcquisition = (acq: Acquisition) => {
    if (currentSourceLiveOnly) return;
    setMode(modeForSource(currentSource, acq));
  };

  const start = async () => {
    try {
      await api.startCapture(captureSettings, name);
    } catch (e: any) {
      toast('error', e.message);
    }
  };
  const stop = async () => {
    try {
      await api.stopCapture();
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const duration = captureSettings.num_samples / captureSettings.sample_rate;

  return (
    <div className="panel-body">
      <div className="hardware-note">
        <strong>{activeModeLabel}</strong>
        <span>{hardwareSummary(captureSettings.mode as CaptureMode)}</span>
      </div>

      <label className="field">
        <span>Capture name</span>
        <input value={name} placeholder="(auto)" onChange={(e) => setName(e.target.value)} />
      </label>

      <div className="field">
        <span>Hardware mode</span>
        <div className="mode-grid">
          {SOURCES.map((opt) => (
            <button
              key={opt.source}
              type="button"
              className={`mode-tile ${currentSource === opt.source ? 'active' : ''}`}
              onClick={() => selectSource(opt.source)}
              title={opt.detail}
            >
              <span className="mode-title">{opt.label}</span>
              <span className="mode-detail">{opt.detail}</span>
              <span className="mode-channels mono">{opt.channels}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span>Acquisition</span>
        <div className="seg-toggle" role="group" aria-label="Acquisition">
          <button
            type="button"
            className={`seg ${currentAcq === 'single' ? 'active' : ''}`}
            onClick={() => selectAcquisition('single')}
            disabled={currentSourceLiveOnly}
            title="Capture a fixed number of samples once, then read back"
          >
            Single-shot
          </button>
          <button
            type="button"
            className={`seg ${currentAcq === 'live' ? 'active' : ''}`}
            onClick={() => selectAcquisition('live')}
            title="Continuously capture into the SDRAM ring for a live rolling view"
          >
            Live ring
          </button>
        </div>
        {currentSourceLiveOnly && (
          <span className="mode-detail">Packed narrow is live-only.</span>
        )}
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
          <span>Live window</span>
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

      <div className="field">
        <span>Readback codec</span>
        {digitalCompressionMode ? (
          <div className="seg-toggle" role="group" aria-label="Digital readback compression">
            {(['raw', 'delta_rle'] as ReadbackCompression[]).map((codec) => (
              <button
                key={codec}
                type="button"
                className={`seg ${captureSettings.readback_compression === codec ? 'active' : ''}`}
                onClick={() => setCaptureSettings({ readback_compression: codec })}
                disabled={!controlMode}
                title={
                  codec === 'raw'
                    ? 'No compression'
                    : 'Merged delta packing followed by RLE'
                }
              >
                {codec === 'raw' ? 'RAW' : 'DELTA RLE'}
              </button>
            ))}
          </div>
        ) : (
          <span className="mode-detail">Analog and mixed captures use raw readback.</span>
        )}
      </div>

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
          <button className="danger big" onClick={stop}>Stop</button>
        )}
      </div>
    </div>
  );
}

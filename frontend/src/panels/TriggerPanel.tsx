// Trigger configuration with explicit hardware / post-capture / unavailable
// labelling driven by the device capability matrix.
import { useApp } from '../state/appStore';

const EXEC_BADGE: Record<string, { label: string; cls: string }> = {
  hardware: { label: 'HW', cls: 'badge-hw' },
  post_capture: { label: 'post-capture', cls: 'badge-soft' },
  unavailable: { label: 'unavailable', cls: 'badge-na' },
};

export function TriggerPanel() {
  const { capabilities, captureSettings, setCaptureSettings } = useApp();
  const trig = captureSettings.trigger;
  const matrix = capabilities?.trigger_matrix ?? [];

  const setTrig = (t: Partial<typeof trig>) =>
    setCaptureSettings({ trigger: { ...trig, ...t } });

  const exec = matrix.find((m) => m.type === trig.type)?.execution ?? 'unavailable';

  const needsChannels = !['none', 'timeout'].includes(trig.type);
  const needsValue = ['bus_value', 'uart_byte', 'spi_byte', 'i2c_address'].includes(trig.type);
  const needsWidth = ['pulse_wider', 'pulse_narrower', 'timeout', 'glitch'].includes(trig.type);
  const needsPattern = trig.type === 'pattern';
  const needsBaud = trig.type === 'uart_byte';

  return (
    <div className="panel-body">
      <label className="field">
        <span>Trigger type</span>
        <select value={trig.type} onChange={(e) => {
          const t = e.target.value;
          const ex = matrix.find((m) => m.type === t)?.execution ?? 'unavailable';
          setTrig({ type: t, execution: ex as any });
        }}>
          {matrix.map((m) => (
            <option key={m.type} value={m.type} disabled={m.execution === 'unavailable'}>
              {m.type.replace(/_/g, ' ')} {m.execution === 'hardware' ? '· HW'
                : m.execution === 'post_capture' ? '· post' : '· n/a'}
            </option>
          ))}
        </select>
      </label>
      {trig.type !== 'none' && (
        <div className={`badge ${EXEC_BADGE[exec].cls}`}>
          {exec === 'hardware' ? 'Supported in hardware'
            : exec === 'post_capture' ? 'Post-capture only (software search)'
            : 'Unavailable on this device'}
        </div>
      )}
      {needsChannels && (
        <div className="field">
          <span>Channels</span>
          <div className="bus-members">
            {Array.from({ length: capabilities?.digital_channels ?? 16 }, (_, i) => (
              <label key={i} className="chip">
                <input type="checkbox" checked={trig.channels.includes(i)}
                  onChange={(e) => setTrig({
                    channels: e.target.checked
                      ? [...trig.channels, i].sort((a, b) => a - b)
                      : trig.channels.filter((c) => c !== i),
                  })} />
                {i}
              </label>
            ))}
          </div>
        </div>
      )}
      {needsPattern && (
        <label className="field">
          <span>Pattern (1/0/x per channel)</span>
          <input value={trig.pattern ?? ''} placeholder="1x0x"
            onChange={(e) => setTrig({ pattern: e.target.value })} />
        </label>
      )}
      {needsValue && (
        <label className="field">
          <span>Match value (hex)</span>
          <input value={trig.value != null ? trig.value.toString(16) : ''}
            placeholder="3c"
            onChange={(e) => setTrig({ value: parseInt(e.target.value, 16) || 0 })} />
        </label>
      )}
      {needsWidth && (
        <label className="field">
          <span>Width (µs)</span>
          <input type="number" step="0.1"
            value={trig.width_s != null ? trig.width_s * 1e6 : 1}
            onChange={(e) => setTrig({ width_s: Number(e.target.value) / 1e6 })} />
        </label>
      )}
      {needsBaud && (
        <label className="field">
          <span>Baud</span>
          <input type="number" value={trig.baud ?? 115200}
            onChange={(e) => setTrig({ baud: Number(e.target.value) })} />
        </label>
      )}
      {capabilities?.supports_pre_trigger && trig.type !== 'none' && (
        <>
          <label className="field">
            <span>Trigger position: {trig.position_pct.toFixed(0)} %</span>
            <input type="range" min={0} max={90} value={trig.position_pct}
              onChange={(e) => {
                const pct = Number(e.target.value);
                setTrig({
                  position_pct: pct,
                  pre_trigger_samples: Math.floor(captureSettings.num_samples * pct / 100),
                });
              }} />
          </label>
          <div className="hint">
            pre-trigger {trig.pre_trigger_samples.toLocaleString()} samples /
            post-trigger {(captureSettings.num_samples - trig.pre_trigger_samples).toLocaleString()}
          </div>
        </>
      )}
    </div>
  );
}

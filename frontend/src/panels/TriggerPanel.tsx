// Trigger configuration with explicit hardware / post-capture / unavailable
// labeling driven by the device capability matrix.
import { useApp } from '../state/appStore';
import { api } from '../api/client';
import { waveformView } from '../state/waveformStore';

const EXEC_BADGE: Record<string, { label: string; cls: string }> = {
  hardware: { label: 'HW', cls: 'badge-hw' },
  post_capture: { label: 'post-capture', cls: 'badge-soft' },
  unavailable: { label: 'unavailable', cls: 'badge-na' },
};

export function TriggerPanel() {
  const { capabilities, captureSettings, setCaptureSettings, activeSession, toast } = useApp();
  const trig = captureSettings.trigger;
  const matrix = capabilities?.trigger_matrix ?? [];

  const setTrig = (t: Partial<typeof trig>) =>
    setCaptureSettings({ trigger: { ...trig, ...t } });

  const exec = matrix.find((m) => m.type === trig.type)?.execution ?? 'unavailable';

  const needsChannels = !['none', 'timeout'].includes(trig.type);
  const needsValue = ['bus_value', 'uart_byte', 'spi_byte', 'i2c_address', 'generic_pattern'].includes(trig.type);
  const needsWidth = ['pulse_wider', 'pulse_narrower', 'timeout', 'glitch'].includes(trig.type);
  const needsPattern = trig.type === 'pattern';
  const needsBaud = trig.type === 'uart_byte';
  const needsOccurrence = ['uart_byte', 'i2c_address', 'i2c_nack', 'spi_byte', 'decoder_error'].includes(trig.type);
  const needsSequence = trig.type === 'sequence';
  const needsTimingQualifier = !['none', 'sequence', 'timeout'].includes(trig.type);
  const previewSteps = trig.type === 'sequence'
    ? (trig.sequence_steps ?? []).map((step: any) => step.type ?? 'event')
    : trig.type === 'pattern'
      ? String(trig.pattern ?? '').split('').map((bit) => bit === 'x' ? "don't care" : bit === '1' ? 'high' : 'low')
      : [trig.type.replace(/_/g, ' ')];
  const searchExisting = async (occurrence: number) => {
    try {
      const query = { ...trig, occurrence };
      const r = await api.triggerSearch(activeSession!.id, query, undefined, true);
      if (r.sample == null) toast('warning', `No match for occurrence ${occurrence}`);
      else {
        setTrig({ occurrence });
        waveformView.jumpTo(r.sample);
        const scope = r.scopes?.[0];
        if (scope) waveformView.setView(scope.start_sample, scope.end_sample);
        toast('success', scope
          ? `Match ${occurrence}; scoped decoder to ${scope.event_count} event(s)`
          : `Match ${occurrence} at sample ${r.sample}`);
      }
    } catch (e: any) { toast('error', e.message); }
  };

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
      {trig.type !== 'none' && (
        <div className="trigger-preview" aria-label="Trigger preview">
          <span className="trigger-preview-title">Preview</span>
          <div className="trigger-preview-track">
            {previewSteps.slice(0, 16).map((step: string, index: number) => (
              <span key={`${step}-${index}`} className={`trigger-preview-step ${step === 'high' ? 'high' : step === 'low' ? 'low' : ''}`}>
                {step === 'high' ? '1' : step === 'low' ? '0' : step === "don't care" ? 'x' : '•'}
              </span>
            ))}
          </div>
          <span className="hint">{previewSteps.length > 16 ? `${previewSteps.length} steps; first 16 shown` : previewSteps.join(' → ')}</span>
        </div>
      )}
      {needsChannels && (
        <div className="field">
          <span>{trig.type === 'generic_pattern' ? 'Channels (max 4, 0-15)' : 'Channels'}</span>
          <div className="bus-members">
            {Array.from({ length: capabilities?.digital_channels ?? 16 }, (_, i) => (
              <label key={i} className="chip">
                <input type="checkbox" checked={trig.channels.includes(i)}
                  disabled={trig.type === 'generic_pattern' && !trig.channels.includes(i) && trig.channels.length >= 4}
                  onChange={(e) => setTrig({
                    channels: e.target.checked
                      ? [...trig.channels, i].sort((a, b) => a - b)
                      : trig.channels.filter((c) => c !== i),
                  })} />
                {i}{trig.type === 'generic_pattern' && i === 0 ? ' (max 4)' : ''}
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
          <span>Width (us)</span>
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
      {trig.type === 'generic_pattern' && (
        <>
          <label className="field">
            <span>Protocol preset</span>
            <select value={trig.clock_source === 'internal_baud' ? 'uart' : trig.start_mode === 'none' ? 'parallel' : 'spi'}
              onChange={(e) => {
                const preset = e.target.value;
                if (preset === 'uart') setTrig({ clock_source: 'internal_baud', start_mode: 'edge_on_channel', clock_edge: 'rising', frame_width: 8, bit_order: 'lsb_first' });
                else if (preset === 'spi') setTrig({ clock_source: 'external_edge', start_mode: 'edge_on_channel', clock_edge: 'rising', frame_width: 8, bit_order: 'msb_first' });
                else setTrig({ clock_source: 'external_edge', start_mode: 'none', frame_width: 16, bit_order: 'msb_first' });
              }}>
              <option value="uart">UART</option><option value="spi">SPI</option><option value="parallel">Parallel bus</option>
            </select>
          </label>
          <label className="field"><span>Clock channel</span><input type="number" min={0} max={15} value={trig.clock_channel ?? 0} onChange={(e) => setTrig({ clock_channel: Number(e.target.value) })} /></label>
          <label className="field"><span>Clock source</span><select value={trig.clock_source ?? 'external_edge'} onChange={(e) => setTrig({ clock_source: e.target.value as any })}><option value="internal_baud">Internal baud</option><option value="external_edge">External edge</option></select></label>
          <label className="field"><span>Clock edge</span><select value={trig.clock_edge ?? 'rising'} onChange={(e) => setTrig({ clock_edge: e.target.value as any })}><option value="rising">Rising</option><option value="falling">Falling</option></select></label>
          <label className="field"><span>Baud</span><input type="number" min={1} value={trig.baud ?? 115200} onChange={(e) => setTrig({ baud: Number(e.target.value) })} /></label>
          <label className="field"><span>Frame width (bits)</span><input type="number" min={1} max={32} value={trig.frame_width ?? 8} onChange={(e) => setTrig({ frame_width: Math.max(1, Math.min(32, Number(e.target.value))) })} /></label>
          <label className="field"><span>Match mask (hex)</span><input value={(trig.match_mask ?? 0xFFFFFFFF).toString(16)} onChange={(e) => setTrig({ match_mask: parseInt(e.target.value, 16) || 0 })} /></label>
          <label className="field"><span>Bit order</span><select value={trig.bit_order ?? 'lsb_first'} onChange={(e) => setTrig({ bit_order: e.target.value as any })}><option value="lsb_first">LSB first</option><option value="msb_first">MSB first</option></select></label>
          <label className="field"><span>Start channel</span><input type="number" min={0} max={15} value={trig.start_channel ?? 0} onChange={(e) => setTrig({ start_channel: Number(e.target.value) })} /></label>
          <label className="field"><span>Start condition</span><select value={trig.start_mode ?? 'edge_on_channel'} onChange={(e) => setTrig({ start_mode: e.target.value as any })}><option value="edge_on_channel">Edge on channel</option><option value="none">None</option></select></label>
          <label className="field"><span>Start polarity</span><select value={trig.start_polarity ?? 0} onChange={(e) => setTrig({ start_polarity: Number(e.target.value) })}><option value={0}>Falling / low</option><option value={1}>Rising / high</option></select></label>
        </>
      )}
      {needsOccurrence && (
        <label className="field">
          <span>Match occurrence</span>
          <input type="number" min={1} step={1} value={trig.occurrence ?? 1}
            onChange={(e) => setTrig({ occurrence: Math.max(1, Number(e.target.value)) })} />
        </label>
      )}
      {needsTimingQualifier && <>
        <label className="field">
          <span>Minimum duration (µs)</span>
          <input type="number" min={0} step={0.1}
            value={trig.min_duration_s != null ? trig.min_duration_s * 1e6 : ''}
            onChange={(e) => setTrig({ min_duration_s: e.target.value ? Number(e.target.value) / 1e6 : null })} />
        </label>
        <label className="field">
          <span>Maximum duration (µs)</span>
          <input type="number" min={0} step={0.1}
            value={trig.max_duration_s != null ? trig.max_duration_s * 1e6 : ''}
            onChange={(e) => setTrig({ max_duration_s: e.target.value ? Number(e.target.value) / 1e6 : null })} />
        </label>
        <label className="field">
          <span>Consecutive matching samples</span>
          <input type="number" min={1} step={1} value={trig.consecutive ?? 1}
            onChange={(e) => setTrig({ consecutive: Math.max(1, Number(e.target.value)) })} />
        </label>
        <label className="field">
          <span>Holdoff after match (µs)</span>
          <input type="number" min={0} step={0.1}
            value={trig.holdoff_s != null ? trig.holdoff_s * 1e6 : ''}
            onChange={(e) => setTrig({ holdoff_s: e.target.value ? Number(e.target.value) / 1e6 : null })} />
        </label>
        <label className="field checkbox">
          <input type="checkbox" checked={Boolean(trig.rearm)}
            onChange={(e) => setTrig({ rearm: e.target.checked })} />
          <span>Re-arm for repeated captures</span>
        </label>
      </>}
      {needsSequence && (
        <>
          <label className="field">
            <span>Sequence steps (JSON)</span>
            <input value={JSON.stringify(trig.sequence_steps ?? [])}
              placeholder='[{"type":"uart_byte","value":85}]'
              onChange={(e) => {
                try { setTrig({ sequence_steps: JSON.parse(e.target.value) }); } catch { /* edit in progress */ }
              }} />
          </label>
          <label className="field">
            <span>Sequence window (us)</span>
            <input type="number" min={0} step={0.1}
              value={(trig.window_s ?? 0) * 1e6}
              onChange={(e) => setTrig({ window_s: Number(e.target.value) / 1e6 })} />
          </label>
        </>
      )}
      {activeSession && exec === 'post_capture' && (
        <div className="button-row">
          <button onClick={() => searchExisting(Math.max(1, (trig.occurrence ?? 1) - 1))}
            disabled={(trig.occurrence ?? 1) <= 1}>Previous match</button>
          <button onClick={() => searchExisting(trig.occurrence ?? 1)}>Search existing capture</button>
          <button onClick={() => searchExisting((trig.occurrence ?? 1) + 1)}>Next match</button>
        </div>
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

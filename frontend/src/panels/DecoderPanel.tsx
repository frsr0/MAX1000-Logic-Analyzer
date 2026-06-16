// Decoder management: add instance (type → channel roles → settings),
// run / cancel / region decode, status, presets.
import { useState } from 'react';
import { api } from '../api/client';
import type { DecoderDescription } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

export function DecoderPanel() {
  const { activeSession, refreshActiveSession, decoderTypes, toast } = useApp();
  const [adding, setAdding] = useState(false);
  const [typeId, setTypeId] = useState('uart');
  const [channels, setChannels] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<Record<string, unknown>>({});

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  const desc: DecoderDescription | undefined = decoderTypes.find((d) => d.id === typeId);
  const channelOptions = activeSession.channels
    .filter((c) => c.type === 'digital' || c.type === 'derived')
    .map((c) => ({ id: c.id, label: `${c.id} (${c.name})` }));

  const selection = waveformView.selectionStart !== null && waveformView.selectionEnd !== null
    ? [Math.floor(Math.min(waveformView.selectionStart, waveformView.selectionEnd)),
       Math.ceil(Math.max(waveformView.selectionStart, waveformView.selectionEnd))]
    : null;

  const add = async (region?: number[]) => {
    if (!desc) return;
    const missing = desc.channels.filter((c) => c.required && !channels[c.role]);
    if (missing.length && !desc.consumes) {
      toast('warning', `Assign channels: ${missing.map((m) => m.name).join(', ')}`);
      return;
    }
    try {
      await api.addDecoder(activeSession.id, {
        decoder_id: typeId, channels, settings, region,
      });
      await refreshActiveSession();
      setAdding(false);
      setChannels({});
      setSettings({});
      pollUntilDone();
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const pollUntilDone = () => {
    // decoder_complete also arrives via WS; poll as a fallback
    let n = 0;
    const t = setInterval(async () => {
      await refreshActiveSession();
      waveformView.decodersVersion++;
      waveformView.requestAnnotations(0);
      const s = useApp.getState().activeSession;
      if (!s || !s.decoders.some((d) => d.status === 'running') || ++n > 60) {
        clearInterval(t);
      }
    }, 700);
  };

  const run = async (decId: string, region?: number[]) => {
    try {
      await api.runDecoder(activeSession.id, decId, region);
      pollUntilDone();
    } catch (e: any) { toast('error', e.message); }
  };

  const savePreset = (decId: string) => {
    const inst = activeSession.decoders.find((d) => d.id === decId);
    if (!inst) return;
    const presets = JSON.parse(localStorage.getItem('msa_decoder_presets') ?? '[]');
    presets.push({ name: `${inst.decoder_id} ${new Date().toLocaleString()}`,
      decoder_id: inst.decoder_id, channels: inst.channels, settings: inst.settings });
    localStorage.setItem('msa_decoder_presets', JSON.stringify(presets));
    toast('success', 'Preset saved (Settings page)');
  };

  return (
    <div className="panel-body">
      {activeSession.decoders.map((d) => (
        <div key={d.id} className="decoder-card">
          <div className="decoder-head">
            <input type="checkbox" checked={d.enabled} title="enable/disable"
              onChange={async (e) => {
                await api.patchDecoder(activeSession.id, d.id, { enabled: e.target.checked });
                await refreshActiveSession();
                waveformView.requestAnnotations(0);
              }} />
            <strong>{d.name || d.decoder_id}</strong>
            <span className={`status status-${d.status}`}>{d.status}</span>
            <span className="hint">{d.event_count} events</span>
          </div>
          <div className="hint">
            {Object.entries(d.channels).map(([r, c]) => `${r}=${c}`).join('  ')}
            {d.region ? `  region ${d.region[0]}–${d.region[1]}` : ''}
          </div>
          {d.error && <div className="finding error">{d.error}</div>}
          <div className="button-row">
            <button onClick={() => run(d.id)}>Run</button>
            {selection && <button onClick={() => run(d.id, selection)}>Run on selection</button>}
            {d.status === 'running' && (
              <button onClick={() => api.cancelDecoder(activeSession.id, d.id)}>Cancel</button>
            )}
            <button onClick={() => savePreset(d.id)}>Preset</button>
            <button className="danger" onClick={async () => {
              await api.deleteDecoder(activeSession.id, d.id);
              await refreshActiveSession();
              waveformView.requestAnnotations(0);
            }}>✕</button>
          </div>
        </div>
      ))}

      {!adding ? (
        <button className="primary" onClick={() => setAdding(true)}>+ Add decoder</button>
      ) : (
        <div className="decoder-card">
          <label className="field">
            <span>Decoder</span>
            <select value={typeId} onChange={(e) => { setTypeId(e.target.value); setChannels({}); setSettings({}); }}>
              {decoderTypes.map((d) => (
                <option key={d.id} value={d.id}>{d.name}{d.consumes ? ` (on ${d.consumes})` : ''}</option>
              ))}
            </select>
          </label>
          {desc?.consumes && (
            <div className="hint">Stacked decoder — needs a completed '{desc.consumes}' run on this session.</div>
          )}
          {desc?.channels.filter((c) => !c.role.startsWith('bit') || parseInt(c.role.slice(3)) < 8).map((c) => (
            <label key={c.role} className="field">
              <span>{c.name}{c.required ? ' *' : ''}</span>
              <select value={channels[c.role] ?? ''}
                onChange={(e) => setChannels({ ...channels, [c.role]: e.target.value })}>
                <option value="">—</option>
                {channelOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          ))}
          {desc?.settings.map((s) => (
            <label key={s.key} className="field">
              <span>{s.name}</span>
              {s.type === 'enum' ? (
                <select value={String(settings[s.key] ?? s.default)}
                  onChange={(e) => setSettings({ ...settings, [s.key]: parseEnum(e.target.value, s.options) })}>
                  {s.options?.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
                </select>
              ) : s.type === 'bool' ? (
                <input type="checkbox" checked={Boolean(settings[s.key] ?? s.default)}
                  onChange={(e) => setSettings({ ...settings, [s.key]: e.target.checked })} />
              ) : (
                <input type={s.type === 'str' ? 'text' : 'number'}
                  value={String(settings[s.key] ?? s.default ?? '')}
                  onChange={(e) => setSettings({
                    ...settings,
                    [s.key]: s.type === 'str' ? e.target.value : Number(e.target.value),
                  })} />
              )}
            </label>
          ))}
          <div className="button-row">
            <button className="primary" onClick={() => add()}>Add & run</button>
            {selection && <button onClick={() => add(selection)}>Add & run on selection</button>}
            <button onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function parseEnum(value: string, options?: any[] | null) {
  const match = options?.find((o) => String(o) === value);
  return match !== undefined ? match : value;
}

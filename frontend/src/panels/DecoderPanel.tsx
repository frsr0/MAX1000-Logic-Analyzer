// Decoder management: add instance (type → channel roles → settings),
// run / cancel / region decode, status, presets.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { WaveformPayload } from '../api/binary';
import type { DecoderDescription } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

export function DecoderPanel() {
  const { activeSession, refreshActiveSession, decoderTypes, toast } = useApp();
  const [adding, setAdding] = useState(false);
  const [typeId, setTypeId] = useState('uart');
  const [channels, setChannels] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [, setWaveformVersion] = useState(0);

  // WaveformView is deliberately outside React because it owns large typed
  // arrays. Subscribe here so a rolling/live chunk causes the suggestion to
  // be recomputed from the same payload the user is looking at.
  useEffect(() => waveformView.subscribe(() => setWaveformVersion((v) => v + 1)), []);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  const suggestions = suggestProtocols(activeSession, waveformView.payload);

  const desc: DecoderDescription | undefined = decoderTypes.find((d) => d.id === typeId);
  const optionsForRole = (types: string[]) => activeSession.channels
    .filter((c) => types.includes(c.type))
    .map((c) => ({
      id: c.id,
      label: `${c.id} (${c.name}${c.type === 'analog' ? ', threshold' : ''})`,
    }));
  const firstRequired = desc?.channels.find((c) => c.required);
  const fallback = firstRequired ? optionsForRole(firstRequired.types)[0]?.id : undefined;
  const effectiveChannels = defaultSingleInputChannel(desc, channels, fallback);

  const selection = waveformView.selectionStart !== null && waveformView.selectionEnd !== null
    ? [Math.floor(Math.min(waveformView.selectionStart, waveformView.selectionEnd)),
       Math.ceil(Math.max(waveformView.selectionStart, waveformView.selectionEnd))]
    : null;

  const add = async (region?: number[]) => {
    if (!desc) return;
    const missing = desc.channels.filter((c) => c.required && !effectiveChannels[c.role]);
    if (missing.length && !desc.consumes) {
      toast('warning', `Assign channels: ${missing.map((m) => m.name).join(', ')}`);
      return;
    }
    try {
      await api.addDecoder(activeSession.id, {
        decoder_id: typeId, channels: effectiveChannels, settings, region,
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
      {suggestions.map((suggestion) => (
        <div key={suggestion.id} className="finding info" role="status">
          <strong>Suggested protocol: {suggestion.name}</strong>
          <span> {suggestion.reason}</span>
          {!activeSession.decoders.some((d) => d.decoder_id === suggestion.id) && (
            <button className="link-button" onClick={() => {
              setTypeId(suggestion.id);
              setChannels(suggestion.channels ?? {});
              setSettings(suggestion.settings ?? {});
              setAdding(true);
            }}>Configure {suggestion.name}</button>
          )}
        </div>
      ))}
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
            <span className="hint">{d.event_count} events{d.quality_score != null
              ? ` · quality ${(d.quality_score * 100).toFixed(0)}%` : ''}</span>
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
              <select value={effectiveChannels[c.role] ?? ''}
                onChange={(e) => setChannels({ ...channels, [c.role]: e.target.value })}>
                <option value="">—</option>
                {optionsForRole(c.types).map((o) => (
                  <option key={o.id} value={o.id}>{o.label}</option>
                ))}
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

export type ProtocolSuggestion = {
  id: string;
  name: string;
  reason: string;
  channels: Record<string, string>;
  settings?: Record<string, unknown>;
};

/** Infer a useful first decoder from the channels currently shown in Capture.
 * This deliberately stays advisory: the user still chooses the decoder and
 * its settings before a live run starts.
 */
export function suggestProtocol(
  session: { channels: { id: string; name: string; type: string }[]; tags?: string[] },
  payload?: WaveformPayload | null,
): ProtocolSuggestion | null {
  return suggestProtocols(session, payload)[0] ?? null;
}

/** Return all useful candidates, ordered from strongest evidence to weakest. */
export function suggestProtocols(
  session: { channels: { id: string; name: string; type: string }[]; tags?: string[] },
  payload?: WaveformPayload | null,
): ProtocolSuggestion[] {
  // Suggestions must describe what is visible in the current waveform.  In
  // particular, do not use session tags or friendly channel labels as a
  // shortcut: an accelerometer session can be idle, disconnected, or wired
  // differently, and a named channel is not protocol evidence.
  const channels = session.channels.filter((c) => c.type === 'digital');
  const evidence = waveformEvidence(payload);
  if (!evidence || evidence.samples.length < 2 || channels.length === 0) return [];

  const suggestions: ProtocolSuggestion[] = [];
  const i2c = findI2cPair(channels, evidence.samples, evidence.lod);
  if (i2c) {
    suggestions.push({
      id: 'i2c', name: 'I²C', channels: { sda: i2c.sda.id, scl: i2c.scl.id },
      reason: `Observed open-drain-like bus activity on ${i2c.sda.id}/${i2c.scl.id}: ${i2c.evidence}.`,
    });
  }

  // Only offer weaker protocol candidates when the stronger two-wire I²C
  // shape was not observed.  This keeps a live sensor bus from also looking
  // like a generic serial/clocked bus in the decoder panel.
  if (!i2c) {
    const spi = findSpiPair(channels, evidence.samples);
    if (spi) {
      suggestions.push({ id: 'spi', name: 'SPI', channels: { sclk: spi.clock.id, mosi: spi.data.id },
        reason: `Observed a regular clock with data transitions on ${spi.clock.id}/${spi.data.id} (${spi.evidence}).` });
    } else {
      const uart = findUartLine(channels, evidence.samples);
      if (uart) {
        suggestions.push({ id: 'uart', name: 'UART', channels: { rx: uart.channel.id },
          reason: `Observed idle-high asynchronous transitions on ${uart.channel.id} (${uart.evidence}).` });
      }
    }
  }
  return suggestions;
}

type LineStats = {
  bit: number;
  transitions: number;
  rising: number;
  falling: number;
  highRatio: number;
  startsHigh: boolean;
  endsHigh: boolean;
};

type WaveformEvidence = {
  samples: Uint8Array | Uint16Array | Uint32Array;
  lod: boolean;
};

function waveformEvidence(payload: WaveformPayload | null | undefined): WaveformEvidence | null {
  const digital = payload?.arrays.get('digital');
  if (digital instanceof Uint8Array || digital instanceof Uint16Array || digital instanceof Uint32Array) {
    return { samples: digital, lod: false };
  }

  // The canvas intentionally fetches LOD data for a wide live window.  LOD
  // payloads do not contain a `digital` array, but their and/or masks still
  // describe the observed waveform.  Expand each bin into its low/high
  // bounds so inference can use the same line-shape checks without ever
  // treating channel names or session tags as protocol evidence.
  const and = payload?.arrays.get('digital_and');
  const or = payload?.arrays.get('digital_or');
  if (!(and instanceof Uint16Array) || !(or instanceof Uint16Array)
      || and.length !== or.length || and.length === 0) return null;
  const samples = new Uint16Array(and.length * 2 + 1);
  samples[0] = or[0];
  for (let i = 0; i < and.length; i++) {
    samples[1 + i * 2] = and[i];
    samples[2 + i * 2] = or[i];
  }
  return { samples, lod: true };
}

function lineStats(samples: ArrayLike<number>, bit: number): LineStats {
  let high = 0;
  let transitions = 0;
  let rising = 0;
  let falling = 0;
  let previous = (samples[0] >> bit) & 1;
  high += previous;
  for (let i = 1; i < samples.length; i++) {
    const current = (samples[i] >> bit) & 1;
    high += current;
    if (current !== previous) {
      transitions++;
      if (current) rising++;
      else falling++;
    }
    previous = current;
  }
  return { bit, transitions, rising, falling, highRatio: high / samples.length,
    startsHigh: Boolean((samples[0] >> bit) & 1), endsHigh: Boolean((samples[samples.length - 1] >> bit) & 1) };
}

function bitNumber(id: string): number | null {
  const value = Number(id.match(/^d(\d+)$/i)?.[1]);
  return Number.isInteger(value) && value >= 0 && value < 32 ? value : null;
}

function findI2cPair(channels: { id: string; name: string }[], samples: ArrayLike<number>, lod = false) {
  const candidates = channels.map((channel) => {
    const bit = bitNumber(channel.id);
    return bit === null ? null : { channel, stats: lineStats(samples, bit), bit };
  }).filter((value): value is { channel: { id: string; name: string }; stats: LineStats; bit: number } => value !== null);

  for (let i = 0; i < candidates.length; i++) {
    for (let j = i + 1; j < candidates.length; j++) {
      // The clock normally has at least as many edges as SDA.  Test both
      // orientations so channel order does not influence the prediction.
      for (const [a, b] of [[candidates[i], candidates[j]], [candidates[j], candidates[i]]] as const) {
        const sda = a.stats;
        const scl = b.stats;
        if (scl.transitions < 4 || sda.transitions < 1 || scl.transitions < sda.transitions) continue;
        if (sda.highRatio < 0.5 || scl.highRatio < 0.5 || !sda.startsHigh || !scl.startsHigh || !sda.endsHigh || !scl.endsHigh) continue;

        let sdaChangesWhileClockLow = 0;
        let clockRises = 0;
        let stableAtClockRise = 0;
        let previousSda = (samples[0] >> sda.bit) & 1;
        let previousScl = (samples[0] >> scl.bit) & 1;
        for (let n = 1; n < samples.length; n++) {
          const currentSda = (samples[n] >> sda.bit) & 1;
          const currentScl = (samples[n] >> scl.bit) & 1;
          if (currentSda !== previousSda && currentScl === 0) sdaChangesWhileClockLow++;
          if (!previousScl && currentScl) {
            clockRises++;
            if (currentSda === previousSda) stableAtClockRise++;
          }
          previousSda = currentSda;
          previousScl = currentScl;
        }
        if (clockRises < 2 || stableAtClockRise < Math.ceil(clockRises * 0.6)) continue;
        // A bin only preserves its bounds, not the exact edge ordering.  It
        // therefore under-counts SDA changes while SCL is low; retain the
        // stricter threshold for raw samples and use a conservative lower
        // bound for LOD evidence.
        const dataChangeRatio = lod ? 0.25 : 0.4;
        if (sdaChangesWhileClockLow < Math.max(1, Math.ceil(sda.transitions * dataChangeRatio))) continue;
        return { sda: a.channel, scl: b.channel,
          evidence: `${scl.transitions} clock edges, ${sda.transitions} data edges, idle-high lines` };
      }
    }
  }
  return null;
}

function findSpiPair(channels: { id: string; name: string }[], samples: ArrayLike<number>) {
  const candidates = channels.map((channel) => {
    const bit = bitNumber(channel.id);
    return bit === null ? null : { channel, stats: lineStats(samples, bit), bit };
  }).filter((value): value is { channel: { id: string; name: string }; stats: LineStats; bit: number } => value !== null);
  for (const clock of candidates) {
    if (clock.stats.transitions < 6) continue;
    for (const data of candidates) {
      if (clock === data || data.stats.transitions < 1 || data.stats.transitions > clock.stats.transitions * 1.5) continue;
      return { clock: clock.channel, data: data.channel,
        evidence: `${clock.stats.transitions} clock edges and ${data.stats.transitions} data edges` };
    }
  }
  return null;
}

function findUartLine(channels: { id: string; name: string }[], samples: ArrayLike<number>) {
  for (const channel of channels) {
    const bit = bitNumber(channel.id);
    if (bit === null) continue;
    const stats = lineStats(samples, bit);
    if (stats.transitions >= 4 && stats.startsHigh && stats.endsHigh && stats.highRatio >= 0.5 && stats.highRatio < 0.95) {
      return { channel, evidence: `${stats.transitions} edges with idle-high framing` };
    }
  }
  return null;
}

function parseEnum(value: string, options?: any[] | null) {
  const match = options?.find((o) => String(o) === value);
  return match !== undefined ? match : value;
}

function defaultSingleInputChannel(
  desc: DecoderDescription | undefined,
  channels: Record<string, string>,
  fallback?: string,
) {
  if (!desc || desc.consumes || !fallback) return channels;
  const required = desc.channels.filter((c) => c.required);
  if (required.length !== 1 || channels[required[0].role]) return channels;
  return { ...channels, [required[0].role]: fallback };
}

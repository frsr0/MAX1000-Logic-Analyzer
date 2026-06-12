// Channel management: show/hide, rename, colour, reorder, solo, bus grouping,
// derived (filtered/threshold) channels.
import { useState } from 'react';
import { api } from '../api/client';
import type { ChannelInfo } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

const PALETTE = ['#4fc3f7', '#81c784', '#ffb74d', '#e57373', '#ba68c8',
  '#4db6ac', '#fff176', '#90a4ae', '#f06292', '#aed581'];

export function ChannelPanel() {
  const { activeSession, refreshActiveSession, toast } = useApp();
  const [busName, setBusName] = useState('BUS0');
  const [busMembers, setBusMembers] = useState<string[]>([]);
  const [deriveSource, setDeriveSource] = useState('d0');
  const [deriveKind, setDeriveKind] = useState('min_pulse');
  const [deriveParam, setDeriveParam] = useState(3);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;
  const channels = activeSession.channels;

  const patchChannels = async (updates: Partial<ChannelInfo>[]) => {
    try {
      await api.patchSession(activeSession.id, { channels: updates });
      await refreshActiveSession();
      waveformView.requestFetch(0);
      waveformView.notify();
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const move = (idx: number, dir: -1 | 1) => {
    const order = [...channels];
    const j = idx + dir;
    if (j < 0 || j >= order.length) return;
    [order[idx], order[j]] = [order[j], order[idx]];
    patchChannels(order.map((c) => ({ id: c.id })));
  };

  const solo = (id: string) => {
    patchChannels(channels.map((c) => ({ id: c.id, enabled: c.id === id })));
  };

  const allOn = () => patchChannels(channels.map((c) => ({ id: c.id, enabled: true })));

  const addDerived = async () => {
    const derive: Record<string, unknown> = { kind: deriveKind };
    if (deriveKind === 'debounce') derive.hold = deriveParam;
    if (deriveKind === 'min_pulse') derive.min_width = deriveParam;
    if (deriveKind === 'glitch_suppress') derive.max_glitch = deriveParam;
    if (deriveKind === 'threshold') derive.level = deriveParam;
    try {
      await api.addDerivedChannel(activeSession.id, deriveSource, derive);
      await refreshActiveSession();
      waveformView.requestFetch(0);
      toast('success', 'Derived channel added (raw data unchanged)');
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const digitalIds = channels.filter((c) => c.type === 'digital').map((c) => c.id);
  const analogIds = channels.filter((c) => c.type === 'analog').map((c) => c.id);

  return (
    <div className="panel-body">
      <div className="button-row">
        <button onClick={allOn}>Show all</button>
      </div>
      <div className="channel-list">
        {channels.map((ch, idx) => (
          <div key={ch.id} className={`channel-row ${ch.enabled ? '' : 'disabled'}`}>
            <input type="checkbox" checked={ch.enabled} title="show/hide"
              onChange={(e) => patchChannels([{ id: ch.id, enabled: e.target.checked }])} />
            <input type="color" value={ch.color ?? PALETTE[idx % PALETTE.length]}
              onChange={(e) => patchChannels([{ id: ch.id, color: e.target.value }])} />
            <input className="ch-name" defaultValue={ch.name} key={`${ch.id}:${ch.name}`}
              onBlur={(e) => e.target.value !== ch.name
                && patchChannels([{ id: ch.id, name: e.target.value }])} />
            <span className="ch-type">{ch.type === 'analog' ? '∿' : ch.type === 'derived' ? 'ƒ' : '⎍'}</span>
            <button title="solo" onClick={() => solo(ch.id)}>S</button>
            <button title="move up" onClick={() => move(idx, -1)}>↑</button>
            <button title="move down" onClick={() => move(idx, 1)}>↓</button>
            {ch.type === 'analog' && (
              <select value={ch.volts_per_div} title="volts/div"
                onChange={(e) => patchChannels([{ id: ch.id, volts_per_div: Number(e.target.value) }])}>
                {[0.1, 0.2, 0.5, 1, 2].map((v) => <option key={v} value={v}>{v} V/div</option>)}
              </select>
            )}
          </div>
        ))}
      </div>

      <h4>Derived channel (software filter / threshold)</h4>
      <div className="hint">Filters never modify raw data — they create new channels.</div>
      <label className="field">
        <span>Source</span>
        <select value={deriveSource} onChange={(e) => setDeriveSource(e.target.value)}>
          {digitalIds.map((id) => <option key={id} value={id}>{id}</option>)}
          {analogIds.map((id) => <option key={id} value={id}>{id} (analog)</option>)}
        </select>
      </label>
      <label className="field">
        <span>Filter</span>
        <select value={deriveKind} onChange={(e) => setDeriveKind(e.target.value)}>
          <option value="majority3">3-sample majority</option>
          <option value="debounce">Debounce (hold N)</option>
          <option value="min_pulse">Min pulse width</option>
          <option value="glitch_suppress">Glitch suppression</option>
          {analogIds.includes(deriveSource) && <option value="threshold">Analog threshold</option>}
        </select>
      </label>
      {deriveKind !== 'majority3' && (
        <label className="field">
          <span>{deriveKind === 'threshold' ? 'Level (V)' : 'Samples'}</span>
          <input type="number" step={deriveKind === 'threshold' ? 0.05 : 1}
            value={deriveParam} onChange={(e) => setDeriveParam(Number(e.target.value))} />
        </label>
      )}
      <button className="primary" onClick={addDerived}>Add derived channel</button>

      <h4>Bus grouping</h4>
      <label className="field">
        <span>Name</span>
        <input value={busName} onChange={(e) => setBusName(e.target.value)} />
      </label>
      <div className="bus-members">
        {digitalIds.map((id) => (
          <label key={id} className="chip">
            <input type="checkbox" checked={busMembers.includes(id)}
              onChange={(e) => setBusMembers(e.target.checked
                ? [...busMembers, id] : busMembers.filter((m) => m !== id))} />
            {id}
          </label>
        ))}
      </div>
      <button onClick={async () => {
        if (busMembers.length < 2) { toast('warning', 'Pick ≥ 2 members (bit0 first)'); return; }
        try {
          await api.addBus(activeSession.id, busName, busMembers);
          await refreshActiveSession();
          waveformView.notify();
          toast('success', `Bus ${busName} added`);
        } catch (e: any) { toast('error', e.message); }
      }}>Create bus</button>
    </div>
  );
}

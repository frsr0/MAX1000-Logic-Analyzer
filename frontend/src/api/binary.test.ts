import { describe, expect, it } from 'vitest';
import { buildWaveformPayload } from '../test/waveformPayload';
import { parseWaveformPayload } from './binary';

describe('parseWaveformPayload', () => {
  it('parses every wire dtype and preserves four-byte array alignment', () => {
    const payload = buildWaveformPayload('session-1', [
      { name: 'byte', dtype: 'u1', values: [1, 2, 255] },
      { name: 'word', dtype: 'u2', values: [256, 65_535] },
      { name: 'dword', dtype: 'u4', values: [1, 0xfeedbeef] },
      { name: 'float', dtype: 'f4', values: [1.5, -2.25] },
    ]);

    const parsed = parseWaveformPayload(payload);

    expect(parsed.header.session_id).toBe('session-1');
    expect(Array.from(parsed.arrays.get('byte')!)).toEqual([1, 2, 255]);
    expect(Array.from(parsed.arrays.get('word')!)).toEqual([256, 65_535]);
    expect(Array.from(parsed.arrays.get('dword')!)).toEqual([1, 0xfeedbeef]);
    expect(Array.from(parsed.arrays.get('float')!)).toEqual([1.5, -2.25]);
  });

  it('rejects payloads without the MSAW magic', () => {
    const payload = buildWaveformPayload('bad');
    new Uint8Array(payload)[0] = 0;
    expect(() => parseWaveformPayload(payload)).toThrow('Bad waveform payload magic');
  });
});

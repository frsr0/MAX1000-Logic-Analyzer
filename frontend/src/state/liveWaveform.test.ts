import { describe, expect, it } from 'vitest';
import { decodeLiveWaveformBase64, parseLiveWaveformBinary } from './liveWaveform';

function binaryPacket(samples: number[], sampleRate = 2_000_000): ArrayBuffer {
  const header = new TextEncoder().encode(JSON.stringify({
    sample_count: samples.length, sample_rate: sampleRate,
  }));
  const buffer = new ArrayBuffer(4 + header.length + samples.length * 2);
  const view = new DataView(buffer);
  view.setUint32(0, header.length, true);
  new Uint8Array(buffer, 4, header.length).set(header);
  const values = new Uint16Array(buffer, 4 + header.length, samples.length);
  values.set(samples);
  return buffer;
}

describe('live waveform packet parsing', () => {
  it('parses the ordered binary packet without changing sample values', () => {
    const chunk = parseLiveWaveformBinary(binaryPacket([0, 0x1234, 0xffff]));
    expect(chunk?.sampleRate).toBe(2_000_000);
    expect(Array.from(chunk?.samples ?? [])).toEqual([0, 0x1234, 0xffff]);
  });

  it('uses the payload length when sample_count is omitted', () => {
    const header = new TextEncoder().encode(JSON.stringify({ sample_rate: 1_000 }));
    const buffer = new ArrayBuffer(4 + header.length + 2);
    new DataView(buffer).setUint32(0, header.length, true);
    new Uint8Array(buffer, 4, header.length).set(header);
    new Uint16Array(buffer, 4 + header.length)[0] = 7;
    expect(Array.from(parseLiveWaveformBinary(buffer)?.samples ?? [])).toEqual([7]);
  });

  it('decodes the legacy base64 packet representation', () => {
    const bytes = Uint8Array.from([0x34, 0x12, 0xcd, 0xab]);
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    const chunk = decodeLiveWaveformBase64(btoa(binary), 2_000_000);
    expect(Array.from(chunk?.samples ?? [])).toEqual([0x1234, 0xabcd]);
  });

  it('rejects malformed or empty packets', () => {
    expect(parseLiveWaveformBinary(new ArrayBuffer(3))).toBeNull();
    expect(parseLiveWaveformBinary(binaryPacket([], 2_000_000))).toBeNull();
    expect(parseLiveWaveformBinary(binaryPacket([1], 0))).toBeNull();
    const malformedHeader = new ArrayBuffer(6);
    new DataView(malformedHeader).setUint32(0, 1, true);
    expect(parseLiveWaveformBinary(malformedHeader)).toBeNull();
    const invalidJson = new ArrayBuffer(8);
    new DataView(invalidJson).setUint32(0, 2, true);
    new Uint8Array(invalidJson, 4, 2).set([0x7b, 0x2d]);
    expect(parseLiveWaveformBinary(invalidJson)).toBeNull();
    expect(decodeLiveWaveformBase64('not base64', 2_000_000)).toBeNull();
    expect(decodeLiveWaveformBase64('', 2_000_000)).toBeNull();
    expect(decodeLiveWaveformBase64(btoa('\u0001'), 2_000_000)).toBeNull();
    expect(decodeLiveWaveformBase64(btoa('\u0001\u0002'), 0)).toBeNull();
  });
});

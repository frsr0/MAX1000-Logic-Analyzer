export interface LiveWaveformChunk {
  sampleRate: number;
  samples: Uint16Array;
}

function parseHeader(buffer: ArrayBuffer): {
  header: Record<string, any>;
  payloadOffset: number;
} | null {
  if (buffer.byteLength < 4) return null;
  const view = new DataView(buffer);
  const headerLength = view.getUint32(0, true);
  const payloadOffset = 4 + headerLength;
  if (headerLength <= 0 || payloadOffset > buffer.byteLength
      || payloadOffset % 2 !== 0
      || (buffer.byteLength - payloadOffset) % 2 !== 0) return null;
  try {
    const header = JSON.parse(new TextDecoder().decode(
      new Uint8Array(buffer, 4, headerLength))) as Record<string, any>;
    return { header, payloadOffset };
  } catch {
    return null;
  }
}

export function parseLiveWaveformBinary(buffer: ArrayBuffer): LiveWaveformChunk | null {
  const parsed = parseHeader(buffer);
  if (!parsed) return null;
  const { header, payloadOffset } = parsed;
  const available = (buffer.byteLength - payloadOffset) / 2;
  const sampleCount = Math.min(
    available,
    Number.isFinite(Number(header.sample_count))
      ? Math.max(0, Number(header.sample_count)) : available,
  );
  const sampleRate = Number(header.sample_rate);
  if (!sampleCount || !Number.isFinite(sampleRate) || sampleRate <= 0) return null;
  return {
    sampleRate,
    samples: new Uint16Array(buffer, payloadOffset, sampleCount),
  };
}

export function decodeLiveWaveformBase64(value: string,
                                         sampleRate: number): LiveWaveformChunk | null {
  if (!value || !Number.isFinite(sampleRate) || sampleRate <= 0) return null;
  try {
    const binary = atob(value);
    const count = Math.floor(binary.length / 2);
    if (!count) return null;
    const samples = new Uint16Array(count);
    for (let i = 0; i < count; i += 1) {
      samples[i] = binary.charCodeAt(i * 2)
        | (binary.charCodeAt(i * 2 + 1) << 8);
    }
    return { sampleRate, samples };
  } catch {
    return null;
  }
}

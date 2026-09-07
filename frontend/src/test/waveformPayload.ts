import type { WaveformHeader } from '../api/binary';

type DType = WaveformHeader['arrays'][number]['dtype'];

interface TestArray {
  name: string;
  dtype: DType;
  values: number[];
}

function encodedValues(dtype: DType, values: number[]): Uint8Array {
  const typed = dtype === 'u1' ? new Uint8Array(values)
    : dtype === 'u2' ? new Uint16Array(values)
      : dtype === 'u4' ? new Uint32Array(values)
        : new Float32Array(values);
  return new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength);
}

export function buildWaveformPayload(
  sessionId: string,
  arrays: TestArray[] = [{ name: 'digital', dtype: 'u2', values: [1, 2, 3] }],
): ArrayBuffer {
  const header: WaveformHeader = {
    session_id: sessionId,
    start: 0,
    end: 3,
    num_samples: 3,
    sample_rate: 1_000_000,
    mode: 'raw',
    samples_per_bin: 1,
    arrays: arrays.map((array) => ({
      name: array.name,
      dtype: array.dtype,
      count: array.values.length,
    })),
  };

  const encoder = new TextEncoder();
  const unpaddedHeader = encoder.encode(JSON.stringify(header));
  const headerLength = unpaddedHeader.length + (-(8 + unpaddedHeader.length) % 4 + 4) % 4;
  const encodedArrays = arrays.map((array) => encodedValues(array.dtype, array.values));
  const totalLength = 8 + headerLength + encodedArrays.reduce(
    (total, encoded) => total + encoded.length + (-encoded.length % 4 + 4) % 4,
    0,
  );
  const buffer = new ArrayBuffer(totalLength);
  const bytes = new Uint8Array(buffer);
  bytes.set([0x4d, 0x53, 0x41, 0x57], 0);
  new DataView(buffer).setUint32(4, headerLength, true);
  bytes.fill(0x20, 8, 8 + headerLength);
  bytes.set(unpaddedHeader, 8);

  let offset = 8 + headerLength;
  for (const encoded of encodedArrays) {
    bytes.set(encoded, offset);
    offset += encoded.length + (-encoded.length % 4 + 4) % 4;
  }
  return buffer;
}

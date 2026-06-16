// Parser for the backend's binary waveform wire format (MSAW).
// Arrays are returned as zero-copy TypedArray views onto the response buffer
// — nothing here ever lands in React state.

export interface WaveformHeader {
  session_id: string;
  start: number;
  end: number;
  num_samples: number;
  sample_rate: number;
  mode: 'raw' | 'lod' | 'overview';
  samples_per_bin: number;
  bin_start?: number;
  edges_channels?: number;
  arrays: { name: string; dtype: 'u1' | 'u2' | 'u4' | 'f4'; count: number }[];
}

export interface WaveformPayload {
  header: WaveformHeader;
  arrays: Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>;
}

const MAGIC = 0x4d534157; // 'MSAW' big-endian read

export function parseWaveformPayload(buf: ArrayBuffer): WaveformPayload {
  const dv = new DataView(buf);
  if (dv.getUint32(0, false) !== MAGIC) {
    throw new Error('Bad waveform payload magic');
  }
  const hlen = dv.getUint32(4, true);
  const headerText = new TextDecoder().decode(new Uint8Array(buf, 8, hlen));
  const header = JSON.parse(headerText) as WaveformHeader;
  let offset = 8 + hlen; // backend pads the header to a 4-byte boundary
  const arrays = new Map<string, any>();
  for (const a of header.arrays) {
    let view;
    let bytes;
    switch (a.dtype) {
      case 'u1': view = new Uint8Array(buf, offset, a.count); bytes = a.count; break;
      case 'u2': view = new Uint16Array(buf, offset, a.count); bytes = a.count * 2; break;
      case 'u4': view = new Uint32Array(buf, offset, a.count); bytes = a.count * 4; break;
      case 'f4': view = new Float32Array(buf, offset, a.count); bytes = a.count * 4; break;
    }
    offset += bytes! + ((-bytes!) % 4 + 4) % 4; // arrays are 4-byte padded
    arrays.set(a.name, view);
  }
  return { header, arrays };
}

export interface LiveAccelerometerConfig {
  output_data_rate_hz?: number;
  full_scale_g?: number;
  operating_mode?: 'normal' | 'high_resolution' | 'low_power';
  endian?: 'little' | 'big';
}

export interface LiveDecodedSample {
  x_g: number;
  y_g: number;
  z_g: number;
  source_index: number | null;
  sample_offset: number;
}

/**
 * Ring-fallback waveform packets from older backends do not carry the
 * generator read length. Infer the FIFO burst size from the active LIS3DH
 * ODR so the browser decoder still consumes the complete transaction.
 */
export function inferLiveReadLength(data: Record<string, unknown>): number {
  const explicit = Number(data.read_length);
  if (Number.isInteger(explicit) && explicit >= 6 && explicit % 6 === 0) {
    return explicit;
  }
  const config = data.config && typeof data.config === 'object'
    ? data.config as Record<string, unknown> : {};
  const odr = Number(config.output_data_rate_hz);
  if (odr === 5376) return 24;
  if (odr === 1600) return 12;
  return 6;
}

const SENSOR_REGISTER = 0xA8;
const LIS3DH_SENSITIVITY_MG: Record<string, Record<number, number>> = {
  normal: { 2: 4, 4: 8, 8: 16, 16: 48 },
  high_resolution: { 2: 1, 4: 2, 8: 4, 16: 12 },
  low_power: { 2: 16, 4: 32, 8: 64, 16: 192 },
};

function signed16(bytes: number[], offset: number, endian: string): number {
  const value = endian === 'big'
    ? (bytes[offset] << 8) | bytes[offset + 1]
    : bytes[offset] | (bytes[offset + 1] << 8);
  return value & 0x8000 ? value - 0x10000 : value;
}

function decodeAxis(bytes: number[], offset: number, config: LiveAccelerometerConfig): number {
  const mode = config.operating_mode ?? 'high_resolution';
  const scale = Number(config.full_scale_g ?? 2);
  const shift = mode === 'normal' ? 6 : mode === 'low_power' ? 8 : 4;
  const sensitivity = LIS3DH_SENSITIVITY_MG[mode]?.[scale] ?? 1;
  return (signed16(bytes, offset, config.endian ?? 'little') >> shift)
    * sensitivity / 1000;
}

function decodeBase64(value: string): Uint16Array {
  const binary = atob(value);
  const count = Math.floor(binary.length / 2);
  const result = new Uint16Array(count);
  for (let i = 0; i < count; i += 1) {
    result[i] = binary.charCodeAt(i * 2) | (binary.charCodeAt(i * 2 + 1) << 8);
  }
  return result;
}

/**
 * Incremental decoder for the raw LIS3DH I²C waveform.
 *
 * Its only input contract is an ordered packed waveform chunk. State is kept
 * across chunks, so a chunk boundary cannot turn a partial transaction into
 * a fake sample. This is deliberately independent of React and can be
 * replaced by another protocol decoder behind the same worker seam.
 */
export class LiveI2cDecoder {
  private previous: [number, number] | null = null;
  private inTransaction = false;
  private phase: 'idle' | 'write' | 'read' = 'idle';
  private bits: number[] = [];
  private data: number[] = [];
  private acks: number[] = [];
  private plateauRuns: Array<[number, number]> = [];
  private controlPlateau = true;
  private frameEmitted = false;
  private transactionSamples = 0;
  private readonly address: number;
  private readonly config: LiveAccelerometerConfig;
  private readonly readLength: number;

  constructor(address = 0x19, config: LiveAccelerometerConfig = {}, readLength = 6) {
    this.address = address & 0x7f;
    this.config = config;
    this.readLength = readLength;
  }

  reset() {
    this.previous = null;
    this.inTransaction = false;
    this.phase = 'idle';
    this.bits = [];
    this.data = [];
    this.acks = [];
    this.plateauRuns = [];
    this.controlPlateau = true;
    this.frameEmitted = false;
    this.transactionSamples = 0;
  }

  private startTransaction() {
    const writeAddress = (this.address << 1) & 0xfe;
    const isRegisterWrite = this.phase === 'write'
      && this.data[0] === writeAddress
      && this.data[1] === SENSOR_REGISTER
      && this.acks[0] === 0 && this.acks[1] === 0;
    if (this.inTransaction && isRegisterWrite) {
      this.phase = 'read';
      this.bits = [];
      this.data = [];
      this.acks = [];
      this.plateauRuns = [];
      this.controlPlateau = true;
      this.frameEmitted = false;
      this.transactionSamples = 0;
      return;
    }
    this.inTransaction = true;
    this.phase = 'write';
    this.bits = [];
    this.data = [];
    this.acks = [];
    this.plateauRuns = [];
    this.controlPlateau = true;
    this.frameEmitted = false;
    this.transactionSamples = 0;
  }

  private appendPlateau(sda: number, count = 1) {
    if (count <= 0) return;
    const last = this.plateauRuns[this.plateauRuns.length - 1];
    if (last && last[0] === sda) last[1] += count;
    else this.plateauRuns.push([sda, count]);
  }

  private plateauCenter(): number {
    let target = 0;
    for (const [, length] of this.plateauRuns) target += length;
    target = Math.floor(target / 2);
    for (const [level, length] of this.plateauRuns) {
      if (target < length) return level;
      target -= length;
    }
    return 0;
  }

  private finishByte(out: LiveDecodedSample[], offset: number) {
    if (this.bits.length < 9) return;
    let value = 0;
    for (const bit of this.bits.slice(0, 8)) value = (value << 1) | bit;
    this.data.push(value);
    this.acks.push(this.bits[8]);
    this.bits = [];

    const expectedRead = ((this.address << 1) | 1) & 0xff;
    const complete = !this.frameEmitted && this.phase === 'read'
      && this.data.length === this.readLength + 1
      && this.data[0] === expectedRead
      && this.acks.length === this.readLength + 1
      && this.acks.slice(0, this.readLength).every((ack) => ack === 0)
      && this.acks[this.readLength] === 1;
    if (!complete) return;

    const payload = this.data.slice(1);
    const mask = this.config.operating_mode === 'normal' ? 0x003f
      : this.config.operating_mode === 'low_power' ? 0x00ff : 0x000f;
    for (let i = 0; i + 5 < payload.length; i += 6) {
      const bytes = payload.slice(i, i + 6);
      if ((signed16(bytes, 0, this.config.endian ?? 'little') & mask)
          || (signed16(bytes, 2, this.config.endian ?? 'little') & mask)
          || (signed16(bytes, 4, this.config.endian ?? 'little') & mask)) {
        continue;
      }
      out.push({
        x_g: decodeAxis(bytes, 0, this.config),
        y_g: decodeAxis(bytes, 2, this.config),
        z_g: decodeAxis(bytes, 4, this.config),
        source_index: null,
        sample_offset: offset,
      });
    }
    this.frameEmitted = true;
  }

  feed(values: Uint16Array, sourceIndex: number | null = null): LiveDecodedSample[] {
    const out: LiveDecodedSample[] = [];
    // The 2 MHz tap repeats each I2C level for several samples. Process
    // identical runs as a unit so this worker remains a real-time consumer
    // of the ordered waveform. The midpoint and transition offset remain
    // unchanged from the sample-by-sample decoder.
    const levels = new Uint8Array(values.length);
    for (let i = 0; i < values.length; i += 1) levels[i] = (values[i] >> 13) & 3;
    let runStart = 0;
    while (runStart < levels.length) {
      let runEnd = runStart + 1;
      while (runEnd < levels.length && levels[runEnd] === levels[runStart]) runEnd += 1;
      const level = levels[runStart];
      const scl = (level >> 1) & 1;
      const sda = level & 1;
      let count = runEnd - runStart;
      let offset = runStart;
      let transition = true;
      if (!this.previous) {
        this.previous = [scl, sda];
        count -= 1;
        offset += 1;
        transition = false;
      } else {
        transition = this.previous[0] !== scl || this.previous[1] !== sda;
      }
      if (count > 0) {
        const [previousScl, previousSda] = this.previous;
        if (this.inTransaction) {
          this.transactionSamples += count;
          if (this.transactionSamples > 8192) this.reset();
        }
        if (scl) {
          if (transition) {
            const ackEdge = this.inTransaction && !this.controlPlateau
              && this.bits.length === 8 && previousScl === 0;
            if (ackEdge) this.appendPlateau(sda, count);
            else if (previousScl && previousSda && !sda) this.startTransaction();
            else if (previousScl && !previousSda && sda) {
              this.inTransaction = false;
              this.phase = 'idle';
              this.bits = [];
              this.data = [];
              this.acks = [];
              this.plateauRuns = [];
              this.controlPlateau = true;
              this.frameEmitted = false;
            } else if (this.inTransaction && !this.controlPlateau) this.appendPlateau(sda, count);
            else if (this.inTransaction && previousScl === 0) this.appendPlateau(sda, count);
          } else if (this.inTransaction && !this.controlPlateau) {
            this.appendPlateau(sda, count);
          }
        } else if (transition && previousScl) {
          if (this.inTransaction && !this.controlPlateau && this.plateauRuns.length) {
            this.bits.push(this.plateauCenter());
            const before = out.length;
            this.finishByte(out, offset);
            for (const sample of out.slice(before)) {
              if (sample.source_index === null && sourceIndex !== null) {
                sample.source_index = sourceIndex + offset;
              }
            }
          }
          this.plateauRuns = [];
          this.controlPlateau = false;
        }
      }
      this.previous = [scl, sda];
      runStart = runEnd;
    }
    return out;
  }

  feedBase64(samplesB64: string, sourceIndex: number | null = null) {
    return this.feed(decodeBase64(samplesB64), sourceIndex);
  }
}

export { decodeBase64 };

import { describe, expect, it } from 'vitest';
import { inferLiveReadLength, LiveI2cDecoder } from './liveI2cDecoder';

const busWord = (scl: number, sda: number) =>
  ((scl & 1) << 14) | ((sda & 1) << 13);

function hold(scl: number, sda: number, count = 3): number[] {
  return Array.from({ length: count }, () => busWord(scl, sda));
}

function byteTrace(value: number, ack: number): number[] {
  const result: number[] = [];
  for (let bit = 7; bit >= 0; bit -= 1) {
    const sda = (value >> bit) & 1;
    result.push(...hold(0, sda, 2), ...hold(1, sda, 4));
  }
  result.push(...hold(0, ack, 2), ...hold(1, ack, 4));
  return result;
}

function transaction(payload: number[]): number[] {
  const result = [
    ...hold(1, 1), // idle
    ...hold(1, 0), // START
  ];
  result.push(...byteTrace(0x32, 0));
  result.push(...byteTrace(0xA8, 0));
  result.push(...hold(0, 1, 2), ...hold(1, 1));
  result.push(...hold(1, 0)); // repeated START
  result.push(...byteTrace(0x33, 0));
  payload.forEach((value, index) => {
    result.push(...byteTrace(value, index === payload.length - 1 ? 1 : 0));
  });
  result.push(...hold(0, 1, 2), ...hold(1, 1)); // STOP
  return result;
}

function sampleBytes(x: number, y: number, z: number): number[] {
  const words = [x << 4, y << 4, z << 4];
  return words.flatMap((word) => [word & 0xff, (word >> 8) & 0xff]);
}

describe('LiveI2cDecoder', () => {
  it('infers ring-fallback burst lengths from the LIS3DH ODR', () => {
    expect(inferLiveReadLength({
      config: { output_data_rate_hz: 1600 },
    })).toBe(12);
    expect(inferLiveReadLength({
      config: { output_data_rate_hz: 5376 },
    })).toBe(24);
    expect(inferLiveReadLength({
      config: { output_data_rate_hz: 1344 },
    })).toBe(6);
    expect(inferLiveReadLength({
      read_length: 18, config: { output_data_rate_hz: 1600 },
    })).toBe(18);
  });

  it('decodes an ordered waveform even when every chunk splits the transaction', () => {
    const waveform = transaction(sampleBytes(128, -64, 1000));
    const decoder = new LiveI2cDecoder(0x19, {
      operating_mode: 'high_resolution', full_scale_g: 2, endian: 'little',
    });
    const decoded = [
      decoder.feed(Uint16Array.from(waveform.slice(0, 37)), 1000),
      decoder.feed(Uint16Array.from(waveform.slice(37, 113)), 1037),
      decoder.feed(Uint16Array.from(waveform.slice(113)), 1113),
    ].flat();

    expect(decoded).toHaveLength(1);
    expect(decoded[0].x_g).toBeCloseTo(0.128, 6);
    expect(decoded[0].y_g).toBeCloseTo(-0.064, 6);
    expect(decoded[0].z_g).toBeCloseTo(1, 6);
    expect(decoded[0].source_index).toBeGreaterThan(1000);
    expect(decoded[0].sample_offset).toBeGreaterThan(0);
  });

  it('does not emit a frame for a register/address-shaped false match', () => {
    const bytes = sampleBytes(128, -64, 1000);
    bytes[0] |= 1; // non-zero low padding bit in the first LIS3DH word
    const waveform = transaction(bytes);
    const decoder = new LiveI2cDecoder(0x19, { operating_mode: 'high_resolution' });

    expect(decoder.feed(Uint16Array.from(waveform))).toHaveLength(0);
  });

  it('decodes the 24-byte high-ODR FIFO burst across chunk boundaries', () => {
    const payload = Array.from({ length: 24 }, () => 0);
    const waveform = transaction(payload);
    const decoder = new LiveI2cDecoder(0x19, {
      output_data_rate_hz: 5376,
      operating_mode: 'low_power', full_scale_g: 2, endian: 'little',
    }, 24);
    const decoded = [
      decoder.feed(Uint16Array.from(waveform.slice(0, 41)), 2000),
      decoder.feed(Uint16Array.from(waveform.slice(41, 197)), 2041),
      decoder.feed(Uint16Array.from(waveform.slice(197)), 2197),
    ].flat();

    expect(decoded).toHaveLength(4);
    expect(decoded.every((sample) => sample.x_g === 0
      && sample.y_g === 0 && sample.z_g === 0)).toBe(true);
  });

  it('supports base64 chunks, big-endian/normal decoding, and reset', () => {
    const waveform = transaction(sampleBytes(128, -64, 1000));
    let binary = '';
    for (const value of waveform) {
      binary += String.fromCharCode(value & 0xff, (value >> 8) & 0xff);
    }
    const decoder = new LiveI2cDecoder(0x19, {
      operating_mode: 'normal', full_scale_g: 4, endian: 'big',
    });
    expect(decoder.feedBase64(btoa(binary))).toEqual([]);
    decoder.reset();
    expect(decoder.feed(Uint16Array.from(waveform))).toHaveLength(0);
    expect(new LiveI2cDecoder(0x19, { operating_mode: 'normal', full_scale_g: 2 })
      .feed(Uint16Array.from(transaction(sampleBytes(4, -4, 8))))).toHaveLength(1);
  });

  it('clears an in-flight transaction on STOP and handles a control plateau', () => {
    const decoder = new LiveI2cDecoder();
    const internals = decoder as unknown as {
      inTransaction: boolean;
      controlPlateau: boolean;
      previous: [number, number] | null;
    };
    internals.inTransaction = true;
    internals.controlPlateau = true;
    internals.previous = [1, 0];
    decoder.feed(Uint16Array.from([busWord(1, 1)]));
    expect(internals.inTransaction).toBe(false);

    internals.inTransaction = true;
    internals.controlPlateau = true;
    internals.previous = [0, 0];
    decoder.feed(Uint16Array.from([busWord(1, 0)]));

    const center = decoder as unknown as {
      plateauRuns: Array<[number, number]>;
      plateauCenter: () => number;
    };
    center.plateauRuns = [[0, 0]];
    expect(center.plateauCenter()).toBe(0);
  });

  it('drops overlong transactions and accepts zero-length plateau calls', () => {
    const decoder = new LiveI2cDecoder();
    const internals = decoder as unknown as {
      inTransaction: boolean;
      transactionSamples: number;
      previous: [number, number] | null;
      appendPlateau: (sda: number, count?: number) => void;
    };
    internals.appendPlateau(1, 0);
    internals.inTransaction = true;
    internals.transactionSamples = 8192;
    internals.previous = [0, 0];
    decoder.feed(Uint16Array.from([busWord(0, 0), busWord(0, 1)]));
    expect(internals.inTransaction).toBe(false);
  });

  it('takes the control plateau branch after an SCL-low transition', () => {
    const decoder = new LiveI2cDecoder();
    const internals = decoder as unknown as {
      inTransaction: boolean;
      controlPlateau: boolean;
      previous: [number, number] | null;
    };
    internals.inTransaction = true;
    internals.controlPlateau = true;
    internals.previous = [0, 0];
    decoder.feed(Uint16Array.from([busWord(1, 1)]));
    expect(internals.inTransaction).toBe(true);

    internals.inTransaction = false;
    internals.previous = [0, 0];
    decoder.feed(Uint16Array.from([busWord(1, 0)]));
  });

  it('covers low-power axis scaling and the non-ack transition path', () => {
    const waveform = transaction(sampleBytes(128, -64, 1000));
    const decoder = new LiveI2cDecoder(0x19, {
      operating_mode: 'low_power', full_scale_g: 16, endian: 'big',
    });
    expect(decoder.feed(Uint16Array.from(waveform))).toHaveLength(0);
    const internals = decoder as unknown as {
      inTransaction: boolean;
      controlPlateau: boolean;
      previous: [number, number] | null;
    };
    internals.inTransaction = true;
    internals.controlPlateau = false;
    internals.previous = [1, 0];
    decoder.feed(Uint16Array.from([busWord(1, 0), busWord(1, 1)]));
    expect(internals.inTransaction).toBe(false);
  });

  it('uses the defensive sensitivity fallback for unknown legacy configs', () => {
    const waveform = transaction(sampleBytes(128, -64, 1000));
    const decoder = new LiveI2cDecoder(0x19, {
      operating_mode: 'legacy' as any, full_scale_g: 99 as any,
    });
    expect(decoder.feed(Uint16Array.from(waveform))).toHaveLength(1);
  });

  it.skipIf(!(globalThis as any).process?.env?.LIVE_HW_TEST)(
    'decodes a live high-ODR binary waveform packet', async () => {
      const packet = await new Promise<ArrayBuffer>((resolve, reject) => {
        const ws = new WebSocket(
          'ws://127.0.0.1:8002/ws/accelerometer/waveform-binary');
        ws.binaryType = 'arraybuffer';
        ws.onerror = () => reject(new Error('live waveform websocket failed'));
        ws.onmessage = (event) => {
          if (typeof event.data === 'string') return;
          resolve(event.data as ArrayBuffer);
          ws.close();
        };
      });
      const packetBytes = packet instanceof ArrayBuffer
        ? new Uint8Array(packet)
        : Uint8Array.from(packet as any);
      const packetBuffer = packetBytes.buffer;
      const view = new DataView(packetBuffer);
      const headerLength = view.getUint32(0, true);
      const header = JSON.parse(new TextDecoder().decode(
        new Uint8Array(packetBuffer, 4, headerLength)));
      const samples = new Uint16Array(
        packetBuffer, 4 + headerLength, header.sample_count);
      const decoder = new LiveI2cDecoder(
        header.address, header.config, header.read_length);

      expect(decoder.feed(samples).length).toBeGreaterThan(0);
    },
  );
});

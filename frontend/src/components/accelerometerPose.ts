import type { MilAccelerometerStatus } from '../api/types';

export interface Orientation {
  pitch: number;
  roll: number;
}

type AccelerometerSample = NonNullable<MilAccelerometerStatus['sample']>;

// A hand-held board moves much more slowly than this cutoff, while LIS3DH
// quantisation/noise is visible in the Euler angles. At the normal ~400 Hz
// stream cadence this is a ~13 ms one-pole time constant (about 40 ms to
// settle to 95%), materially reducing shake while staying under the 100 ms
// movement-response budget.
export const POSE_FILTER_CUTOFF_HZ = 12;
const MAX_SAMPLE_GAP_S = 0.25;
// Tail-follow can skip source samples between fresh windows. Do not let that
// capture-time gap turn the next valid point into a presentation snap.
const MAX_FILTER_STEP_S = 1 / 60;
// A complete but misaligned/protocol-shaped frame must not rotate the board
// by an impossible amount in one update.
const MAX_POSE_STEP_DEG = 75;

function angularDistanceDegrees(left: number, right: number): number {
  const wrapped = Math.abs(left - right) % 360;
  return wrapped > 180 ? 360 - wrapped : wrapped;
}

export function orientationFor(sample: MilAccelerometerStatus['sample']): Orientation {
  if (!sample) return { pitch: 0, roll: 0 };
  // A stationary accelerometer measures gravity. Keep the board level when
  // gravity is +Z, and use X/Y to tilt it around its two screen axes.
  return {
    pitch: Math.atan2(-sample.x_g, Math.hypot(sample.y_g, sample.z_g)) * 180 / Math.PI,
    roll: Math.atan2(sample.y_g, sample.z_g) * 180 / Math.PI,
  };
}

/**
 * Causal, timestamp-aware low-pass filter for the displayed pose.
 *
 * Filtering the XYZ vector before converting to Euler angles avoids magnifying
 * small sensor noise near a level orientation.  A long gap or a timestamp
 * reset starts a new filter state, so reconnects never animate from stale
 * board data.
 */
export class AdaptivePoseFilter {
  private previous: AccelerometerSample | null = null;
  private history: AccelerometerSample[] = [];

  reset(): void {
    this.previous = null;
    this.history = [];
  }

  private robustSample(sample: AccelerometerSample): AccelerometerSample {
    this.history.push(sample);
    if (this.history.length > 3) this.history.shift();
    if (this.history.length < 3) return sample;
    const median = (axis: keyof Pick<AccelerometerSample, 'x_g' | 'y_g' | 'z_g'>) =>
      [...this.history].map((item) => item[axis]).sort((a, b) => a - b)[1];
    // A one-sample USB/decoder corruption can produce tens of degrees of
    // Euler-angle motion. A causal three-sample median rejects that spike
    // without waiting for a future sample; a real step is delayed by at most
    // two source samples (under 6 ms at the measured stream rate).
    return { ...sample, x_g: median('x_g'), y_g: median('y_g'), z_g: median('z_g') };
  }

  update(sample: AccelerometerSample): AccelerometerSample {
    const previous = this.previous;
    const dt = previous ? sample.timestamp - previous.timestamp : 0;
    if (previous && (!Number.isFinite(dt) || dt <= 0 || dt > MAX_SAMPLE_GAP_S)) {
      this.history = [sample];
      this.previous = { ...sample };
      return { ...sample };
    }
    const robust = this.robustSample(sample);
    if (!previous) {
      this.previous = { ...robust };
      return { ...robust };
    }

    const previousOrientation = orientationFor(previous);
    const robustOrientation = orientationFor(robust);
    const poseStep = Math.max(
      angularDistanceDegrees(previousOrientation.pitch, robustOrientation.pitch),
      angularDistanceDegrees(previousOrientation.roll, robustOrientation.roll),
    );
    if (poseStep > MAX_POSE_STEP_DEG) {
      // Consume the source timestamp while holding the causal display pose.
      this.history = [{ ...previous, timestamp: sample.timestamp }];
      this.previous = { ...previous, timestamp: sample.timestamp };
      return { ...previous, timestamp: sample.timestamp };
    }

    // Exact-timestamp updates are commonly REST/WebSocket duplicates. Keep
    // the latest source sample rather than repeatedly filtering it as if time
    // had advanced.
    const presentationDt = Math.min(dt, MAX_FILTER_STEP_S);
    const alpha = 1 - Math.exp(
      -2 * Math.PI * POSE_FILTER_CUTOFF_HZ * presentationDt);
    const filtered = {
      ...sample,
      x_g: previous.x_g + alpha * (robust.x_g - previous.x_g),
      y_g: previous.y_g + alpha * (robust.y_g - previous.y_g),
      z_g: previous.z_g + alpha * (robust.z_g - previous.z_g),
    };
    this.previous = filtered;
    return filtered;
  }
}

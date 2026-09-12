import { expect, it } from 'vitest';
import { AdaptivePoseFilter, orientationFor, POSE_FILTER_CUTOFF_HZ } from './accelerometerPose';

const sample = (x_g: number, timestamp: number) => ({
  x_g, y_g: 0, z_g: 1, timestamp,
});

it('suppresses a one-sample XYZ spike without adding a long tail', () => {
  const filter = new AdaptivePoseFilter();
  filter.update(sample(0, 1));
  const filtered = filter.update(sample(1, 1.0025));

  expect(filtered.x_g).toBeGreaterThan(0);
  expect(filtered.x_g).toBeLessThan(1);
  // The cutoff is fast enough that the one-pole response is well inside the
  // 20 ms live-pose budget at the measured ~400 Hz stream cadence.
  expect(filtered.x_g).toBeCloseTo(
    1 - Math.exp(-2 * Math.PI * POSE_FILTER_CUTOFF_HZ * 0.0025), 8,
  );
});

it('resets after reconnect gaps and ignores duplicate timestamps', () => {
  const filter = new AdaptivePoseFilter();
  filter.update(sample(0, 1));
  expect(filter.update(sample(1, 1)).x_g).toBe(1);
  expect(filter.update(sample(0.25, 2)).x_g).toBe(0.25);
});

it('does not turn a skipped-tail source gap into a full presentation step', () => {
  const filter = new AdaptivePoseFilter();
  filter.update(sample(0, 100));

  // Tail-follow transport can legitimately skip tens of milliseconds of
  // source history between two fresh pose messages. That capture-time gap is
  // not permission for the browser target to jump almost all the way to the
  // new angle in one update.
  const filtered = filter.update(sample(1, 100.08));

  expect(filtered.x_g).toBeGreaterThan(0);
  expect(filtered.x_g).toBeLessThan(0.9);
});

it('does not move the pose on an impossible orientation branch flip', () => {
  const filter = new AdaptivePoseFilter();
  const steady = filter.update({ x_g: 0.2, y_g: 0, z_g: 0.98, timestamp: 20 });

  // The vector still has a perfectly plausible gravity magnitude, but a
  // hand-held board cannot rotate this far between adjacent live points.
  // This is the semantic check needed after a protocol-shaped false frame:
  // it must not become a visible pose step while the raw history remains
  // lossless.
  const filtered = filter.update({
    x_g: 0.2, y_g: 0.98, z_g: -0.05, timestamp: 20.02,
  });

  expect(Math.abs(
    orientationFor(filtered).roll - orientationFor(steady).roll,
  )).toBeLessThan(45);
});

it('reduces realistic 0.1 g tilt noise while following a movement step', () => {
  const filter = new AdaptivePoseFilter();
  const rawAngles: number[] = [];
  const filteredAngles: number[] = [];
  let timestamp = 10;
  for (let i = 0; i < 80; i += 1) {
    // Deterministic alternating sensor noise approximates a noisy stationary
    // board without making this regression test depend on random data.
    const x = (i % 2 === 0 ? 0.1 : -0.1);
    const current = sample(x, timestamp);
    rawAngles.push(Math.atan2(-x, 1) * 180 / Math.PI);
    filteredAngles.push(Math.atan2(-filter.update(current).x_g, 1) * 180 / Math.PI);
    timestamp += 0.0025;
  }
  const rawMean = rawAngles.reduce((sum, value) => sum + value, 0) / rawAngles.length;
  const filteredMean = filteredAngles.reduce((sum, value) => sum + value, 0) / filteredAngles.length;
  const variance = (values: number[], mean: number) =>
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
  expect(variance(filteredAngles.slice(20), filteredMean))
    .toBeLessThan(variance(rawAngles, rawMean) * 0.5);

  // A sustained change must settle promptly: after 100 ms the causal filter
  // should be effectively at the new orientation.
  let stepped = filter.update(sample(1, timestamp));
  for (let i = 1; i <= 40; i += 1) {
    stepped = filter.update(sample(1, timestamp + i * 0.0025));
  }
  expect(stepped.x_g).toBeGreaterThan(0.99);
});

it('rejects a single corrupted frame between valid readings', () => {
  const filter = new AdaptivePoseFilter();
  filter.update(sample(0.2, 20));
  filter.update(sample(0.2, 20.0025));
  const corrupted = filter.update(sample(1.8, 20.005));
  expect(corrupted.x_g).toBeCloseTo(0.2, 8);
  const recovered = filter.update(sample(0.2, 20.0075));
  expect(recovered.x_g).toBeCloseTo(0.2, 8);
});

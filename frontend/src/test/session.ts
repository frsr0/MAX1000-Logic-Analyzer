import { defaultCaptureSettings, type Session } from '../api/types';

export function session(id = 'test-session', overrides: Partial<Session> = {}): Session {
  return {
    id, name: id, created_at: 0, modified_at: 0, app_version: '3.0.0',
    device: { driver: 'test', device_name: 'fixture', connection: 'test', port: '',
      firmware_version: 'test', protocol_version: 'test', sys_clk_hz: 100_000_000,
      sample_clk_hz: 200_000_000, mock: true, extra: {} },
    settings: defaultCaptureSettings(), sample_rate: 1000, sample_clk_hz: 200_000_000,
    num_samples: 0, channels: [], decoders: [], measurements: [], markers: [],
    notes: '', tags: [], exports: [], diagnostics: [], ...overrides,
  };
}

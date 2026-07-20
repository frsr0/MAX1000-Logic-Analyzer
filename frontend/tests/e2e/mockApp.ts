import { type Page, type Request } from '@playwright/test';

type Json = Record<string, any>;

function digitalPinMap() {
  const pins = [
    ['D0', 'PIN_H8', 'J1 / 9'],
    ['D1', 'PIN_K10', 'J1 / 10'],
    ['D2', 'PIN_H5', 'J1 / 11'],
    ['D3', 'PIN_H4', 'J1 / 12'],
    ['D4', 'PIN_J1', 'J1 / 13'],
    ['D5', 'PIN_J2', 'J1 / 14'],
    ['D6', 'PIN_L12', 'J2 / 1'],
    ['D7', 'PIN_J12', 'J2 / 2'],
    ['D8', 'PIN_J13', 'J2 / 3'],
    ['D9', 'PIN_K11', 'J2 / 4'],
    ['D10', 'PIN_K12', 'J2 / 5'],
    ['D11', 'PIN_J10', 'J2 / 6'],
    ['D12', 'PIN_H10', 'J2 / 7'],
    ['D13', 'PIN_H13', 'J2 / 8'],
    ['D14', 'PIN_G12', 'J2 / 9'],
    ['PIO_01', 'PIN_M3', 'PMOD / 1'],
    ['SEN_SDO', 'PIN_K5', 'Accelerometer SDO'],
    ['SEN_SDI', 'PIN_J7', 'Accelerometer SDA'],
    ['SEN_SPC', 'PIN_J6', 'Accelerometer SCL'],
  ];
  return pins.map(([board_label, fpga_pin, header], index) => ({
    pin_index: index,
    board_label,
    fpga_pin,
    header,
  }));
}

function analogPinMap() {
  return [
    { board_label: 'AIN3', fpga_pin: 'PIN_D1', header: 'J1 / 5', adc_channel: 1, available: true, current_rtl_stream: true,
      note: 'High-speed single analog lane.' },
    { board_label: 'AIN1', fpga_pin: 'PIN_C2', header: 'J1 / 3', adc_channel: 2, available: true, current_rtl_stream: true },
    { board_label: 'AIN4', fpga_pin: 'PIN_E3', header: 'J1 / 6', adc_channel: 3, available: true, current_rtl_stream: true },
    { board_label: 'AIN6', fpga_pin: 'PIN_E4', header: 'J1 / 8', adc_channel: 4, available: true, current_rtl_stream: true },
  ];
}

function triggerMatrix() {
  const items = [
    ['none', 'hardware', 'No trigger'],
    ['rising', 'hardware', 'Edge trigger'],
    ['falling', 'hardware', 'Edge trigger'],
    ['uart_byte', 'hardware', 'UART byte match'],
    ['any_edge', 'post_capture', 'Software search after capture'],
    ['pattern', 'post_capture', 'Pattern match'],
  ];
  return items.map(([type, execution, description]) => ({ type, execution, description }));
}

function makeCap(): Json {
  return {
    digital_channels: 16,
    analog_channels: 4,
    max_sample_rate: 200e6,
    min_sample_rate: 6,
    max_samples: 4_194_304,
    bram_samples: 1024,
    sample_clk_hz: 200e6,
    supports_pre_trigger: true,
    supports_rolling: true,
    supports_continuous: true,
    supports_analog: true,
    analog_rate_note: 'MAX10 ADC supports 1 MSPS single-channel analog and 125 kframes/s 4-input physical analog scans. Mixed mode scans ADC0..ADC3 at the same scan frame rate.',
    generator_protocols: ['uart', 'rs485', 'i2c', 'bitbang'],
    triggers: triggerMatrix(),
    trigger_matrix: triggerMatrix(),
    notes: [
      'The MAX1000 has 64 Mbit SDRAM for deep capture.',
      'Single-shot digital capture is validated up to the full 200 MHz sample clock.',
      'Maximum analog scans AIN3, AIN1, AIN4, and AIN6 at 125 kframes/s.',
      'Mixed mode captures 16 digital bits plus the ADC0..ADC3 scan on a shared frame.',
    ],
    digital_pin_map: digitalPinMap(),
    analog_pin_map: analogPinMap(),
  };
}

function makeDevice() {
  return {
    id: 'hardware',
    name: 'MAX1000 OLS Logic Analyzer',
    driver: 'ols_spi',
    connection: 'FTDI FT2232H MPSSE SPI (Channel B)',
    available: true,
    mock: false,
    detail: 'Board-aware MAX1000 capture device',
  };
}

function makeStatus(): Json {
  return {
    app_version: '2.0.0',
    uptime_s: 7261,
    device_connected: true,
    device_kind: 'hardware',
    device: {
      driver: 'ols_spi',
      device_name: 'MAX1000 OLS Logic Analyzer',
      connection: 'FTDI FT2232H MPSSE SPI (Channel B)',
      port: 'ftdi://channel-b',
      firmware_version: 'mock-firmware',
      protocol_version: '1',
      sys_clk_hz: 100.2e6,
      sample_clk_hz: 200.4e6,
      mock: false,
      extra: {},
    },
    capture_state: 'idle',
    capture_progress: { samples_read: 0, samples_total: 0, message: '', repeat: 1 },
    last_session_id: null,
    last_error: null,
    control: { held: true, holder: 'playwright', holder_name: 'Playwright', acquired_at: 1_725_000_000 },
    ws_clients: 1,
    session_count: 0,
  };
}

function makeSessionSummary(): Json {
  return {
    id: 'session-demo',
    name: 'MAX1000 hardware demo',
    created_at: 1_725_000_000,
    modified_at: 1_725_000_500,
    num_samples: 100_000,
    sample_rate: 1_000_000,
    duration_s: 0.1,
    channel_count: 16,
    has_analog: true,
    decoder_count: 0,
    marker_count: 0,
    tags: ['playwright', 'hardware'],
    notes: 'Fixture session for UI screenshots',
    device: 'MAX1000 OLS Logic Analyzer',
    mock: false,
  };
}

function makeAnalogSessionSummary(): Json {
  return {
    id: 'session-analog',
    name: 'MAX1000 mixed analog sweep',
    created_at: 1_725_100_000,
    modified_at: 1_725_100_600,
    num_samples: 50_000,
    sample_rate: 125_000,
    duration_s: 0.4,
    channel_count: 20,
    has_analog: true,
    decoder_count: 1,
    marker_count: 1,
    tags: ['playwright', 'analog', 'mixed'],
    notes: 'Fixture session with mixed digital + 4 analog rows',
    device: 'MAX1000 OLS Logic Analyzer',
    mock: true,
  };
}

function makeAccelSessionSummary(): Json {
  return {
    id: 'session-accel',
    name: 'LIS3DH WHO_AM_I dialogue',
    created_at: 1_725_200_000,
    modified_at: 1_725_200_600,
    num_samples: 32_000,
    sample_rate: 2_000_000,
    duration_s: 0.016,
    channel_count: 16,
    has_analog: false,
    decoder_count: 1,
    marker_count: 0,
    tags: ['playwright', 'hardware', 'accelerometer'],
    notes: 'Fixture session for the on-board LIS3DH dialogue',
    device: 'MAX1000 OLS Logic Analyzer',
    mock: true,
  };
}

function makeAccelSession(): Json {
  const channels = Array.from({ length: 16 }, (_, i) => ({
    id: `d${i}`,
    name: `D${i}`,
    type: 'digital',
    enabled: true,
    color: undefined,
    units: '',
    volts_per_div: 1,
    offset: 0,
    probe_attenuation: 1,
    cal_gain: 1,
    cal_offset: 0,
    threshold: 1.65,
    coupling: 'DC',
    members: [],
    display_base: 'hex',
    board_label: `D${i}`,
    fpga_pin: 'PIN_XX',
    header: 'J1',
    pin_index: i,
  }));
  return {
    id: 'session-accel',
    name: 'LIS3DH WHO_AM_I dialogue',
    created_at: 1_725_200_000,
    modified_at: 1_725_200_600,
    app_version: '2.0.0',
    device: makeStatus().device,
    settings: {
      sample_rate: 2_000_000,
      num_samples: 32_000,
      mode: 'single',
      analog_enabled: false,
      enabled_digital: [13, 14, 15],
      trigger: { type: 'none', channels: [], pre_trigger_samples: 0, position_pct: 0, execution: 'hardware' },
      auto_rearm: false,
      repeat_count: 1,
      auto_save: false,
      readback_compression: 'raw',
      mock_scenario: 'accel_whoami',
    },
    sample_rate: 2_000_000,
    divider: null,
    sample_clk_hz: 2_000_000,
    num_samples: 32_000,
    trigger_sample: null,
    channels,
    decoders: [
      {
        id: 'dec-accel',
        decoder_id: 'i2c',
        name: 'LIS3DH WHO_AM_I decode',
        enabled: true,
        channels: { sda: 'd13', scl: 'd14' },
        settings: { address: '0x19', speed: 100_000 },
        region: null,
        status: 'done',
        error: null,
        event_count: 4,
        warning_count: 0,
      },
    ],
    measurements: [],
    markers: [],
    notes: 'Accelerometer dialogue fixture',
    tags: ['playwright', 'hardware', 'accelerometer'],
    exports: [],
    diagnostics: [],
  };
}

function makeSession(): Json {
  const channels = Array.from({ length: 16 }, (_, i) => ({
    id: `d${i}`,
    name: `D${i}`,
    type: 'digital',
    enabled: true,
    color: undefined,
    units: '',
    volts_per_div: 1,
    offset: 0,
    probe_attenuation: 1,
    cal_gain: 1,
    cal_offset: 0,
    threshold: 1.65,
    coupling: 'DC',
    members: [],
    display_base: 'hex',
    board_label: `D${i}`,
    fpga_pin: 'PIN_XX',
    header: 'J1',
    pin_index: i,
  }));
  return {
    id: 'session-demo',
    name: 'MAX1000 hardware demo',
    created_at: 1_725_000_000,
    modified_at: 1_725_000_500,
    app_version: '2.0.0',
    device: makeStatus().device,
    settings: {
      sample_rate: 1_000_000,
      num_samples: 100_000,
      mode: 'single',
      analog_enabled: false,
      enabled_digital: Array.from({ length: 16 }, (_, i) => i),
      trigger: { type: 'none', channels: [], pre_trigger_samples: 0, position_pct: 0, execution: 'hardware' },
      auto_rearm: false,
      repeat_count: 1,
      auto_save: false,
      readback_compression: 'raw',
      mock_scenario: null,
    },
    sample_rate: 1_000_000,
    divider: null,
    sample_clk_hz: 200.4e6,
    num_samples: 100_000,
    trigger_sample: null,
    channels,
    decoders: [
      {
        id: 'dec-fixture',
        decoder_id: 'uart',
        name: 'UART decode',
        enabled: true,
        channels: { rx: 'd0' },
        settings: { baud: 115200, parity: 'none' },
        region: null,
        status: 'done',
        error: null,
        event_count: 3,
        warning_count: 0,
      },
    ],
    measurements: [],
    markers: [],
    notes: '',
    tags: ['playwright', 'hardware'],
    exports: [],
    diagnostics: [],
  };
}

function makeAnalogSession(): Json {
  const channels = [
    ...Array.from({ length: 16 }, (_, i) => ({
      id: `d${i}`,
      name: `D${i}`,
      type: 'digital',
      enabled: true,
      color: undefined,
      units: '',
      volts_per_div: 1,
      offset: 0,
      probe_attenuation: 1,
      cal_gain: 1,
      cal_offset: 0,
      threshold: 1.65,
      coupling: 'DC',
      members: [],
      display_base: 'hex',
      board_label: `D${i}`,
      fpga_pin: 'PIN_XX',
      header: 'J1',
      pin_index: i,
    })),
    {
      id: 'a0',
      name: 'AIN3',
      type: 'analog',
      enabled: true,
      color: undefined,
      units: 'V',
      volts_per_div: 0.5,
      offset: 0,
      probe_attenuation: 1,
      cal_gain: 1,
      cal_offset: 0,
      threshold: 1.65,
      coupling: 'DC',
      members: [],
      display_base: 'hex',
      board_label: 'AIN3',
      fpga_pin: 'PIN_D1',
      header: 'J1 / 5',
      adc_channel: 1,
      physical_available: true,
    },
    {
      id: 'a1',
      name: 'AIN1',
      type: 'analog',
      enabled: true,
      color: undefined,
      units: 'V',
      volts_per_div: 0.5,
      offset: 0,
      probe_attenuation: 1,
      cal_gain: 1,
      cal_offset: 0,
      threshold: 1.65,
      coupling: 'DC',
      members: [],
      display_base: 'hex',
      board_label: 'AIN1',
      fpga_pin: 'PIN_C2',
      header: 'J1 / 3',
      adc_channel: 2,
      physical_available: true,
    },
    {
      id: 'a2',
      name: 'AIN4',
      type: 'analog',
      enabled: true,
      color: undefined,
      units: 'V',
      volts_per_div: 0.5,
      offset: 0,
      probe_attenuation: 1,
      cal_gain: 1,
      cal_offset: 0,
      threshold: 1.65,
      coupling: 'DC',
      members: [],
      display_base: 'hex',
      board_label: 'AIN4',
      fpga_pin: 'PIN_E3',
      header: 'J1 / 6',
      adc_channel: 3,
      physical_available: true,
    },
    {
      id: 'a3',
      name: 'AIN6',
      type: 'analog',
      enabled: true,
      color: undefined,
      units: 'V',
      volts_per_div: 0.5,
      offset: 0,
      probe_attenuation: 1,
      cal_gain: 1,
      cal_offset: 0,
      threshold: 1.65,
      coupling: 'DC',
      members: [],
      display_base: 'hex',
      board_label: 'AIN6',
      fpga_pin: 'PIN_E4',
      header: 'J1 / 8',
      adc_channel: 4,
      physical_available: true,
    },
  ];
  return {
    id: 'session-analog',
    name: 'MAX1000 mixed analog sweep',
    created_at: 1_725_100_000,
    modified_at: 1_725_100_600,
    app_version: '2.0.0',
    device: makeStatus().device,
    settings: {
      sample_rate: 125_000,
      num_samples: 50_000,
      mode: 'mixed',
      analog_enabled: true,
      enabled_digital: [0, 1],
      trigger: { type: 'none', channels: [], pre_trigger_samples: 0, position_pct: 0, execution: 'hardware' },
      auto_rearm: false,
      repeat_count: 1,
      auto_save: false,
      readback_compression: 'raw',
      mock_scenario: 'analog_demo',
    },
    sample_rate: 125_000,
    divider: null,
    sample_clk_hz: 125_000,
    num_samples: 50_000,
    trigger_sample: null,
    channels,
    decoders: [
      {
        id: 'dec-fixture',
        decoder_id: 'uart',
        name: 'UART decode',
        enabled: true,
        channels: { rx: 'd0' },
        settings: { baud: 115200, parity: 'none' },
        region: null,
        status: 'done',
        error: null,
        event_count: 3,
        warning_count: 0,
      },
    ],
    measurements: [],
    markers: [
      { id: 'm-1', sample: 8_000, label: 'A1', note: 'analog marker', kind: 'glitch', channel: 'a1', color: '#ba68c8' },
    ],
    notes: 'Analog-heavy mixed capture fixture',
    tags: ['playwright', 'analog', 'mixed'],
    exports: [],
    diagnostics: [],
  };
}

function makeDecoderRows() {
  return {
    total: 3,
    events: [
      {
        id: 'evt-1',
        decoder_id: 'dec-fixture',
        type: 'start',
        start_sample: 1200,
        end_sample: 1200,
        start_time: 0.0012,
        end_time: 0.0012,
        label: 'START',
        severity: 'normal',
        fields: { value: 0x48, ascii: 'H' },
      },
      {
        id: 'evt-2',
        decoder_id: 'dec-fixture',
        type: 'byte',
        start_sample: 4200,
        end_sample: 4200,
        start_time: 0.0042,
        end_time: 0.0042,
        label: '0x45',
        severity: 'normal',
        fields: { value: 0x45, ascii: 'E' },
      },
      {
        id: 'evt-3',
        decoder_id: 'dec-fixture',
        type: 'stop',
        start_sample: 8800,
        end_sample: 8800,
        start_time: 0.0088,
        end_time: 0.0088,
        label: 'STOP',
        severity: 'normal',
        fields: { value: 0x4c, ascii: 'L' },
      },
    ],
  };
}

function makeAccelDecoderRows() {
  return {
    total: 4,
    events: [
      {
        id: 'evt-1',
        decoder_id: 'dec-accel',
        type: 'start',
        start_sample: 1800,
        end_sample: 1800,
        start_time: 0.0009,
        end_time: 0.0009,
        label: 'START',
        severity: 'normal',
        fields: { value: 0x19 },
      },
      {
        id: 'evt-2',
        decoder_id: 'dec-accel',
        type: 'byte',
        start_sample: 3800,
        end_sample: 3800,
        start_time: 0.0019,
        end_time: 0.0019,
        label: '0x0F',
        severity: 'normal',
        fields: { value: 0x0f },
      },
      {
        id: 'evt-3',
        decoder_id: 'dec-accel',
        type: 'byte',
        start_sample: 7600,
        end_sample: 7600,
        start_time: 0.0038,
        end_time: 0.0038,
        label: '0x33',
        severity: 'normal',
        fields: { value: 0x33, ascii: '3' },
      },
      {
        id: 'evt-4',
        decoder_id: 'dec-accel',
        type: 'stop',
        start_sample: 11200,
        end_sample: 11200,
        start_time: 0.0056,
        end_time: 0.0056,
        label: 'STOP',
        severity: 'normal',
        fields: { value: 0x33 },
      },
    ],
  };
}

function buildAccelBuffer(mode: 'lod' | 'overview' = 'lod') {
  const rawSamples = 32_000;
  const bins = mode === 'overview' ? 512 : 2048;
  const digital = buildDigitalSeries(rawSamples);
  const header: Json = {
    session_id: 'session-accel',
    start: 0,
    end: rawSamples,
    num_samples: rawSamples,
    sample_rate: 2_000_000,
    mode,
    samples_per_bin: Math.floor(rawSamples / bins),
    bin_start: 0,
  };
  const arrays: Array<{ name: string; dtype: 'u2' | 'u4'; data: Uint16Array | Uint32Array }> = [];
  const { andMask, orMask, edges } = downsampleDigital(digital, bins);
  header.edges_channels = 16;
  arrays.push({ name: 'digital_and', dtype: 'u2', data: andMask });
  arrays.push({ name: 'digital_or', dtype: 'u2', data: orMask });
  arrays.push({ name: 'digital_edges', dtype: 'u4', data: edges });

  const headerBytes = new TextEncoder().encode(JSON.stringify({
    ...header,
    arrays: arrays.map((arr) => ({ name: arr.name, dtype: arr.dtype, count: arr.data.length })),
  }));
  const pad = (4 - ((8 + headerBytes.length) % 4)) % 4;
  const total = 8 + headerBytes.length + pad
    + arrays.reduce((sum, arr) => sum + arr.data.byteLength + ((4 - (arr.data.byteLength % 4)) % 4), 0);
  const buf = new ArrayBuffer(total);
  const dv = new DataView(buf);
  const u8 = new Uint8Array(buf);
  u8.set([0x4d, 0x53, 0x41, 0x57], 0);
  dv.setUint32(4, headerBytes.length + pad, true);
  u8.set(headerBytes, 8);
  u8.fill(0x20, 8 + headerBytes.length, 8 + headerBytes.length + pad);
  let offset = 8 + headerBytes.length + pad;
  for (const arr of arrays) {
    u8.set(new Uint8Array(arr.data.buffer, arr.data.byteOffset, arr.data.byteLength), offset);
    offset += arr.data.byteLength;
    const arrayPad = (4 - (arr.data.byteLength % 4)) % 4;
    if (arrayPad) {
      u8.fill(0x00, offset, offset + arrayPad);
      offset += arrayPad;
    }
  }
  return buf;
}

function buildDigitalSeries(length: number) {
  const digital = new Uint16Array(length);
  for (let i = 0; i < length; i += 1) {
    let word = 0;
    if (Math.floor(i / 40) % 2) word |= 1 << 0;
    if (Math.floor(i / 90) % 2) word |= 1 << 1;
    if ((i % 48) < 24) word |= 1 << 2;
    if (Math.sin(i / 70) > 0) word |= 1 << 3;
    digital[i] = word;
  }
  return digital;
}

function buildAnalogSeries(length: number) {
  const a0 = new Float32Array(length);
  const a1 = new Float32Array(length);
  const a2 = new Float32Array(length);
  const a3 = new Float32Array(length);
  const a4 = new Float32Array(length);
  const a5 = new Float32Array(length);
  const a6 = new Float32Array(length);
  const a7 = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const t = i / Math.max(1, length - 1);
    a0[i] = 1.65 + 1.4 * Math.sin(t * Math.PI * 14);
    a1[i] = 1.65 + 1.05 * Math.sin(t * Math.PI * 12) + 0.15 * Math.sin(t * Math.PI * 60);
    a2[i] = 0.6 + 1.25 * (0.5 + 0.5 * Math.sin(t * Math.PI * 6 + 0.7));
    a3[i] = 1.4 + 0.9 * Math.sin(t * Math.PI * 8 + 0.3);
    a4[i] = 1.2 + 0.7 * Math.sin(t * Math.PI * 10 + 1.1);
    a5[i] = 1.0 + 0.8 * (0.5 + 0.5 * Math.sin(t * Math.PI * 4 + 1.8));
    a6[i] = 1.65 + 1.5 * Math.sin(t * Math.PI * 18 + 0.5);
    a7[i] = 0.9 + 1.2 * (0.5 + 0.5 * Math.sin(t * Math.PI * 5 + 2.2));
  }
  return { a0, a1, a2, a3, a4, a5, a6, a7 };
}

function downsampleDigital(series: Uint16Array, bins: number) {
  const andMask = new Uint16Array(bins);
  const orMask = new Uint16Array(bins);
  const edges = new Uint32Array(bins * 16);
  const total = series.length;
  for (let i = 0; i < bins; i += 1) {
    const start = Math.floor((i * total) / bins);
    const end = Math.max(start + 1, Math.floor(((i + 1) * total) / bins));
    let andWord = 0xffff;
    let orWord = 0;
    let prev = series[start];
    for (let s = start; s < end; s += 1) {
      const word = series[s];
      andWord &= word;
      orWord |= word;
      if (s > start) {
        const delta = prev ^ word;
        for (let bit = 0; bit < 16; bit += 1) {
          if (delta & (1 << bit)) edges[i * 16 + bit] += 1;
        }
      }
      prev = word;
    }
    andMask[i] = andWord;
    orMask[i] = orWord;
  }
  return { andMask, orMask, edges };
}

function downsampleAnalog(series: Float32Array, bins: number) {
  const vmin = new Float32Array(bins);
  const vmax = new Float32Array(bins);
  const total = series.length;
  for (let i = 0; i < bins; i += 1) {
    const start = Math.floor((i * total) / bins);
    const end = Math.max(start + 1, Math.floor(((i + 1) * total) / bins));
    let lo = Number.POSITIVE_INFINITY;
    let hi = Number.NEGATIVE_INFINITY;
    for (let s = start; s < end; s += 1) {
      const v = series[s];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    vmin[i] = lo;
    vmax[i] = hi;
  }
  return { vmin, vmax };
}

function buildMixedAnalogBuffer(mode: 'lod' | 'overview' = 'lod') {
  const rawSamples = 50_000;
  const bins = mode === 'overview' ? 512 : 2048;
  const digital = buildDigitalSeries(rawSamples);
  const analog = buildAnalogSeries(rawSamples);
  const header: Json = {
    session_id: 'session-analog',
    start: 0,
    end: rawSamples,
    num_samples: rawSamples,
    sample_rate: 125_000,
    mode,
    samples_per_bin: Math.floor(rawSamples / bins),
    bin_start: 0,
  };
  const arrays: Array<{ name: string; dtype: 'u2' | 'u4' | 'f4'; data: Uint16Array | Uint32Array | Float32Array }> = [];
  if (mode === 'overview') {
    const { andMask, orMask, edges } = downsampleDigital(digital, bins);
    const analogStats = Object.fromEntries(
      Object.entries(analog).map(([name, data]) => [name, downsampleAnalog(data, bins)]),
    ) as Record<string, { vmin: Float32Array; vmax: Float32Array }>;
    header.edges_channels = 16;
    arrays.push({ name: 'digital_and', dtype: 'u2', data: andMask });
    arrays.push({ name: 'digital_or', dtype: 'u2', data: orMask });
    arrays.push({ name: 'digital_edges', dtype: 'u4', data: edges });
    for (const [name, stats] of Object.entries(analogStats)) {
      arrays.push({ name: `analog_min:${name}`, dtype: 'f4', data: stats.vmin });
      arrays.push({ name: `analog_max:${name}`, dtype: 'f4', data: stats.vmax });
    }
  } else {
    arrays.push({ name: 'digital', dtype: 'u2', data: digital });
    for (const [name, data] of Object.entries(analog)) {
      arrays.push({ name: `analog:${name}`, dtype: 'f4', data });
    }
  }

  const headerBytes = new TextEncoder().encode(JSON.stringify({
    ...header,
    arrays: arrays.map((arr) => ({ name: arr.name, dtype: arr.dtype, count: arr.data.length })),
  }));
  const pad = (4 - ((8 + headerBytes.length) % 4)) % 4;
  const total = 8 + headerBytes.length + pad
    + arrays.reduce((sum, arr) => sum + arr.data.byteLength + ((4 - (arr.data.byteLength % 4)) % 4), 0);
  const buf = new ArrayBuffer(total);
  const dv = new DataView(buf);
  const u8 = new Uint8Array(buf);
  u8.set([0x4d, 0x53, 0x41, 0x57], 0);
  dv.setUint32(4, headerBytes.length + pad, true);
  u8.set(headerBytes, 8);
  u8.fill(0x20, 8 + headerBytes.length, 8 + headerBytes.length + pad);
  let offset = 8 + headerBytes.length + pad;
  for (const arr of arrays) {
    u8.set(new Uint8Array(arr.data.buffer, arr.data.byteOffset, arr.data.byteLength), offset);
    offset += arr.data.byteLength;
    const arrayPad = (4 - (arr.data.byteLength % 4)) % 4;
    if (arrayPad) {
      u8.fill(0x00, offset, offset + arrayPad);
      offset += arrayPad;
    }
  }
  return buf;
}

function buildMsawBuffer(mode: 'lod' | 'overview' = 'lod') {
  const bins = mode === 'overview' ? 256 : 1024;
  const start = 0;
  const end = 100000;
  const sampleRate = 1_000_000;
  const samplesPerBin = Math.floor((end - start) / bins);
  const digitalAnd = new Uint16Array(bins);
  const digitalOr = new Uint16Array(bins);
  const digitalEdges = new Uint32Array(bins * 16);

  for (let i = 0; i < bins; i += 1) {
    let word = 0;
    if (Math.floor(i / 16) % 2) word |= 1 << 0;
    if (Math.floor(i / 32) % 2) word |= 1 << 1;
    if (i % 8 < 4) word |= 1 << 2;
    if (Math.sin(i / 12) > 0) word |= 1 << 3;
    digitalAnd[i] = word;
    digitalOr[i] = word;
    digitalEdges[i * 16 + 0] = i % 16 === 0 ? 1 : 0;
    digitalEdges[i * 16 + 1] = i % 32 === 0 ? 1 : 0;
    digitalEdges[i * 16 + 2] = i % 8 === 0 ? 1 : 0;
    digitalEdges[i * 16 + 3] = i % 12 === 0 ? 1 : 0;
  }

  const header = {
    session_id: 'session-demo',
    start,
    end,
    num_samples: end,
    sample_rate: sampleRate,
    mode,
    samples_per_bin: samplesPerBin,
    bin_start: 0,
    edges_channels: 16,
    arrays: [
      { name: 'digital_and', dtype: 'u2', count: digitalAnd.length },
      { name: 'digital_or', dtype: 'u2', count: digitalOr.length },
      { name: 'digital_edges', dtype: 'u4', count: digitalEdges.length },
    ],
  };
  const headerBytes = new TextEncoder().encode(JSON.stringify(header));
  const pad = (4 - ((8 + headerBytes.length) % 4)) % 4;
  const total = 8 + headerBytes.length + pad + digitalAnd.byteLength + digitalOr.byteLength + digitalEdges.byteLength;
  const buf = new ArrayBuffer(total);
  const dv = new DataView(buf);
  const u8 = new Uint8Array(buf);
  u8.set([0x4d, 0x53, 0x41, 0x57], 0);
  dv.setUint32(4, headerBytes.length + pad, true);
  u8.set(headerBytes, 8);
  u8.fill(0x20, 8 + headerBytes.length, 8 + headerBytes.length + pad);
  let offset = 8 + headerBytes.length + pad;
  u8.set(new Uint8Array(digitalAnd.buffer), offset);
  offset += digitalAnd.byteLength + ((4 - (digitalAnd.byteLength % 4)) % 4);
  u8.set(new Uint8Array(digitalOr.buffer), offset);
  offset += digitalOr.byteLength + ((4 - (digitalOr.byteLength % 4)) % 4);
  u8.set(new Uint8Array(digitalEdges.buffer), offset);
  return buf;
}

function okJson(data: Json, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

function okText(body: string, status = 200) {
  return { status, contentType: 'text/plain', body };
}

function matches(method: string, req: Request, suffix: string) {
  return req.method() === method && new URL(req.url()).pathname === suffix;
}

export async function installMockApp(page: Page) {
  await page.addInitScript(() => {
    class MockWebSocket {
      url: string;
      readyState = 1;
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent<string>) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        setTimeout(() => this.onopen?.(new Event('open')), 0);
      }
      send() {}
      close() {
        this.readyState = 3;
        this.onclose?.(new CloseEvent('close'));
      }
    }
    // @ts-expect-error test stub
    window.WebSocket = MockWebSocket;
  });

  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;

    if (!path.startsWith('/api/')) {
      await route.continue();
      return;
    }

    if (matches('GET', req, '/api/status')) return route.fulfill(okJson(makeStatus()));
    if (matches('GET', req, '/api/devices')) return route.fulfill(okJson({ devices: [makeDevice(), { ...makeDevice(), id: 'mock', name: 'Mock MAX1000 Analyser', driver: 'mock', connection: 'mock', mock: true, detail: 'Synthetic device for preview and tests' }] }));
    if (matches('POST', req, '/api/connect')) return route.fulfill(okJson({ connected: true, metadata: makeStatus().device }));
    if (matches('POST', req, '/api/disconnect')) return route.fulfill(okJson({ connected: false }));
    if (matches('GET', req, '/api/device/metadata')) return route.fulfill(okJson(makeStatus().device));
    if (matches('GET', req, '/api/device/capabilities')) return route.fulfill(okJson(makeCap()));
    if (matches('GET', req, '/api/device/debug')) return route.fulfill(okJson({ raw_metadata: 'mock', raw_status: { ok: true }, last_command: 'noop', last_response: 'ok', last_error: '', command_log: [], timings: {}, extra: {} }));
    if (matches('POST', req, '/api/device/self-test')) {
      return route.fulfill(okJson({ passed: true, message: 'Self-test passed', checks: [{ passed: true, name: 'SPI link', detail: 'fixture ok' }] }));
    }

    if (matches('GET', req, '/api/decoders')) return route.fulfill(okJson({ decoders: [] }));
    if (matches('GET', req, '/api/measurements/types')) return route.fulfill(okJson({ types: [] }));
    if (matches('GET', req, '/api/sessions')) return route.fulfill(okJson({ sessions: [makeSessionSummary(), makeAnalogSessionSummary(), makeAccelSessionSummary()] }));
    if (req.method() === 'GET' && /\/api\/sessions\/[^/]+\/dashboard$/.test(new URL(req.url()).pathname)) {
      return route.fulfill(okJson({ event_count: 12, error_count: 1, warning_count: 2,
        events_per_second: 4.5, by_type: { uart_byte: 10, decoder_error: 2 },
        timeline: [0, 2, 1, 4, 3], error_timeline: [0, 0, 1, 0, 0],
        events: [
          { id: 'ev-1', type: 'uart_byte', label: '0x48 H', severity: 'normal', start_sample: 1200, start_time: 0.0012, end_sample: 1300, end_time: 0.0013 },
          { id: 'ev-2', type: 'decoder_error', label: 'framing error', severity: 'error', start_sample: 2400, start_time: 0.0024, end_sample: 2500, end_time: 0.0025 },
        ] }));
    }
    if (req.method() === 'GET' && /\/api\/sessions\/[^/]+\/eye$/.test(new URL(req.url()).pathname)) {
      return route.fulfill(okJson({ channel: 'd0', baud: 115200, unit_samples: 8.68, traces: 24,
        grid: Array.from({ length: 64 }, (_, y) => Array.from({ length: 160 }, (_, x) => (x + y) % 7 === 0 ? 1 : 0)) }));
    }
    if (matches('GET', req, '/api/logs')) return route.fulfill(okJson({ logs: [] }));
    if (matches('GET', req, '/api/diagnostics')) return route.fulfill(okJson({ lan_urls: ['http://127.0.0.1:4173', 'http://192.168.0.10:4173'] }));
    if (matches('GET', req, '/api/capture/scenarios')) return route.fulfill(okJson({ scenarios: [
      { id: 'demo_mixed', name: 'Demo mixed' },
      { id: 'uart', name: 'UART' },
      { id: 'i2c', name: 'I2C' },
      { id: 'spi', name: 'SPI' },
    ] }));
    if (matches('POST', req, '/api/capture/settings/validate')) return route.fulfill(okJson({ findings: [] }));
    if (matches('POST', req, '/api/control/acquire')) return route.fulfill(okJson({ acquired: true }));
    if (matches('POST', req, '/api/control/release')) return route.fulfill(okJson({ released: true }));

    if (matches('GET', req, '/api/generator/capabilities')) {
      return route.fulfill(okJson({ protocols: ['uart', 'rs485', 'i2c', 'bitbang'], status: { busy: false, running: false, supported: true, detail: 'fixture ready' } }));
    }
    if (matches('GET', req, '/api/generator/bitbang/presets')) return route.fulfill(okJson({ presets: ['idle', 'pulse', 'square', 'alternating', 'counter', 'walking', 'prbs'] }));
    if (matches('POST', req, '/api/generator/preview')) return route.fulfill(okJson({ symbols: [3, 0, 1, 2], count: 4, duration_s: 0.0004, tx_levels: [1, 0, 1, 0], clock_levels: [1, 0, 0, 1] }));
    if (matches('GET', req, '/api/generator/status')) return route.fulfill(okJson({ busy: false, running: false, supported: true, detail: 'fixture ready' }));
    if (matches('POST', req, '/api/generator/send')) return route.fulfill(okJson({ passed: true, sent_hex: '48656c6c6f21', decoded_hex: '48656c6c6f21', detail: 'fixture loopback', session_id: 'session-demo' }));
    if (matches('POST', req, '/api/generator/self-test')) return route.fulfill(okJson({ passed: true, sent_hex: '48656c6c6f21', decoded_hex: '48656c6c6f21', detail: 'fixture self-test' }));
    if (matches('POST', req, '/api/generator/configure')) return route.fulfill(okJson({ ok: true }));
    if (matches('POST', req, '/api/generator/start')) return route.fulfill(okJson({ ok: true }));
    if (matches('POST', req, '/api/generator/stop')) return route.fulfill(okJson({ ok: true }));

    if (matches('GET', req, '/api/mil/presets')) return route.fulfill(okJson({ presets: [
      { id: 'modbus-rtu-demo', name: 'Modbus RTU demo', protocol: 'modbus_uart', description: 'Fixture preset', source: 'tests' },
    ] }));
    if (matches('GET', req, '/api/mil/status')) return route.fulfill(okJson({
      loaded: false,
      running: false,
      config: null,
      preset_id: null,
      last_error: null,
      events: [],
    }));
    if (matches('POST', req, '/api/mil/load')) {
      return route.fulfill(okJson({
        loaded: true,
        running: false,
        preset_id: 'modbus-rtu-demo',
        last_error: null,
        events: [],
        config: {
          name: 'Modbus RTU Demo',
          protocol: 'modbus_uart',
          description: 'Fixture preset',
          trigger: {
            mode: 'modbus_frame',
            rx_pin: 0,
            tx_pin: 1,
            baud: 115200,
            data_bits: 8,
            parity: 'none',
            stop_bits: 1,
            rs485_de_pin: null,
            frame_gap_chars: 3.5,
          },
          timing: {
            response_delay_us: 1000,
            inter_byte_gap_us: 0,
            jitter_us: 0,
          },
          capture: {
            mode: 'auto',
            sample_rate: 14_000_000,
            max_response_bytes: 64,
            manual_post_packet_us: 1000,
            extra_digital_channels: [],
          },
          unit_id: 17,
          registers: [
            { address: 256, name: 'status', width: 16, access: 'ro', value: 1, response_hex: '0042', description: 'Status register' },
          ],
          default_response_hex: '0042',
          notes: ['fixture'],
        },
      }));
    }
    if (matches('POST', req, '/api/mil/start')) {
      return route.fulfill(okJson({
        loaded: true,
        running: true,
        preset_id: 'modbus-rtu-demo',
        last_error: null,
        events: [],
      }));
    }
    if (matches('POST', req, '/api/mil/stop')) {
      return route.fulfill(okJson({
        loaded: true,
        running: false,
        preset_id: 'modbus-rtu-demo',
        last_error: null,
        events: [],
      }));
    }
    if (matches('POST', req, '/api/mil/transaction')) {
      return route.fulfill(okJson({
        request_hex: '110301000002c767',
        response_hex: '1103040042004300',
        detail: 'fixture transaction',
        register_address: 256,
        action: 'response',
        session_id: 'session-demo',
      }));
    }

    if (matches('POST', req, '/api/diagnostics/mock-capture')) {
      const body = req.postDataJSON() as { scenario?: string; analog?: boolean };
      return route.fulfill(okJson({ started: true, scenario: body.scenario ?? 'demo_mixed', analog: !!body.analog }));
    }
    if (matches('GET', req, '/api/sessions/session-demo')) return route.fulfill(okJson(makeSession()));
    if (matches('GET', req, '/api/sessions/session-demo/metadata')) {
      return route.fulfill(okJson({ num_samples: 100_000, sample_rate: 1_000_000 }));
    }
    if (matches('GET', req, '/api/sessions/session-demo/overview')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildMsawBuffer('overview')),
      });
    }
    if (matches('GET', req, '/api/sessions/session-demo/waveform')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildMsawBuffer('lod')),
      });
    }
    if (path === '/api/sessions/session-demo/decoders/dec-fixture/table') {
      return route.fulfill(okJson(makeDecoderRows()));
    }
    if (path === '/api/sessions/session-demo/decoder-events') {
      return route.fulfill(okJson({ events: makeDecoderRows().events }));
    }
    if (path === '/api/sessions/session-demo/decoders/dec-fixture/annotations') {
      return route.fulfill(okJson({ events: makeDecoderRows().events, truncated: false }));
    }
    if (matches('GET', req, '/api/sessions/session-analog')) return route.fulfill(okJson(makeAnalogSession()));
    if (matches('GET', req, '/api/sessions/session-analog/metadata')) {
      return route.fulfill(okJson({ num_samples: 50_000, sample_rate: 125_000 }));
    }
    if (matches('GET', req, '/api/sessions/session-analog/overview')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildMixedAnalogBuffer('overview')),
      });
    }
    if (matches('GET', req, '/api/sessions/session-analog/waveform')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildMixedAnalogBuffer('lod')),
      });
    }
    if (matches('GET', req, '/api/sessions/session-accel')) return route.fulfill(okJson(makeAccelSession()));
    if (matches('GET', req, '/api/sessions/session-accel/metadata')) {
      return route.fulfill(okJson({ num_samples: 32_000, sample_rate: 2_000_000 }));
    }
    if (matches('GET', req, '/api/sessions/session-accel/overview')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildAccelBuffer('overview')),
      });
    }
    if (matches('GET', req, '/api/sessions/session-accel/waveform')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(buildAccelBuffer('lod')),
      });
    }
    if (path === '/api/sessions/session-accel/decoders/dec-accel/table') {
      return route.fulfill(okJson(makeAccelDecoderRows()));
    }
    if (path === '/api/sessions/session-accel/decoder-events') {
      return route.fulfill(okJson({ events: makeAccelDecoderRows().events }));
    }
    if (path === '/api/sessions/session-accel/decoders/dec-accel/annotations') {
      return route.fulfill(okJson({ events: makeAccelDecoderRows().events, truncated: false }));
    }
    if (path === '/api/sessions/session-analog/decoders/dec-fixture/table') {
      return route.fulfill(okJson(makeDecoderRows()));
    }
    if (path === '/api/sessions/session-analog/decoder-events') {
      return route.fulfill(okJson({ events: makeDecoderRows().events }));
    }
    if (path === '/api/sessions/session-analog/decoders/dec-fixture/annotations') {
      return route.fulfill(okJson({ events: makeDecoderRows().events, truncated: false }));
    }

    if (matches('POST', req, '/api/diagnostics/debug-bundle')) return route.fulfill(okText('mock zip', 200));

    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: `Unhandled ${req.method()} ${path}` }) });
  });
}

export function screenshotsDir() {
  return 'test-results/screenshots';
}

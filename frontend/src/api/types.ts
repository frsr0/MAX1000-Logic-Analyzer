// API types mirroring the backend pydantic models.

export interface DeviceMetadata {
  driver: string;
  device_name: string;
  connection: string;
  port: string;
  firmware_version: string;
  protocol_version: string;
  sys_clk_hz: number;
  sample_clk_hz: number;
  mock: boolean;
  extra: Record<string, unknown>;
}

export interface DeviceDescriptor {
  id: string;
  name: string;
  driver: string;
  connection: string;
  available: boolean;
  mock: boolean;
  detail: string;
}

export interface TriggerCapability {
  type: string;
  execution: 'hardware' | 'post_capture' | 'unavailable';
  description: string;
}

export interface GeneratorRouteCapability {
  protocol: string;
  name: string;
  available: boolean;
  physical: boolean;
  outputs: Record<string, string>;
  features: string[];
  detail: string;
}

export interface DeviceCapabilities {
  digital_channels: number;
  analog_channels: number;
  max_sample_rate: number;
  min_sample_rate: number;
  max_samples: number;
  bram_samples: number;
  sample_clk_hz: number;
  supports_pre_trigger: boolean;
  supports_rolling: boolean;
  supports_continuous: boolean;
  supports_analog: boolean;
  analog_rate_note: string;
  generator_protocols: string[];
  generator_routes?: GeneratorRouteCapability[];
  triggers: TriggerCapability[];
  trigger_matrix: TriggerCapability[];
  notes: string[];
  digital_pin_map: PinMapInfo[];
  analog_pin_map: PinMapInfo[];
}

export interface PinMapInfo {
  pin_index?: number;
  board_label: string;
  fpga_pin?: string;
  header?: string;
  adc_channel?: number;
  available?: boolean;
  current_rtl_stream?: boolean;
  note?: string;
}

export interface TriggerConfig {
  type: string;
  channels: number[];
  channel_refs?: string[];
  pattern?: string | null;
  value?: number | null;
  width_s?: number | null;
  baud?: number | null;
  clock_source?: 'internal_baud' | 'external_edge';
  clock_edge?: 'rising' | 'falling';
  start_mode?: 'edge_on_channel' | 'none';
  start_channel?: number;
  start_polarity?: number;
  clock_channel?: number;
  frame_width?: number;
  match_mask?: number | null;
  bit_order?: 'msb_first' | 'lsb_first';
  occurrence?: number;
  window_s?: number | null;
  sequence_steps?: { type: string; value?: number | null }[];
  min_duration_s?: number | null;
  max_duration_s?: number | null;
  consecutive?: number;
  holdoff_s?: number | null;
  rearm?: boolean;
  pre_trigger_samples: number;
  position_pct: number;
  execution: string;
}

export interface CaptureSettings {
  sample_rate: number;
  num_samples: number;
  mode: 'single' | 'continuous' | 'rolling' | 'digital_narrow' | 'triggered' | 'analog' | 'mixed'
    | 'analog_fast' | 'analog_all' | 'analog_continuous'
    | 'analog_all_continuous' | 'mixed_continuous';
  analog_enabled: boolean;
  enabled_digital: number[];
  trigger: TriggerConfig;
  auto_rearm: boolean;
  repeat_count: number;
  auto_save: boolean;
  readback_compression: 'raw' | 'delta_rle' | 'delta' | 'rle';
  mock_scenario?: string | null;
  packed_mode: boolean;
}

export interface ChannelInfo {
  id: string;
  name: string;
  type: 'digital' | 'analog' | 'derived' | 'decoder' | 'bus';
  enabled: boolean;
  color?: string | null;
  units: string;
  volts_per_div: number;
  offset: number;
  probe_attenuation: number;
  cal_gain: number;
  cal_offset: number;
  threshold: number;
  coupling: string;
  members: string[];
  display_base: 'bin' | 'hex' | 'dec' | 'ascii';
  display_height_scale?: number;
  source?: string | null;
  derive?: Record<string, unknown> | null;
  board_label?: string | null;
  fpga_pin?: string | null;
  header?: string | null;
  pin_index?: number | null;
  adc_channel?: number | null;
  physical_available?: boolean | null;
}

export interface DecoderInstance {
  id: string;
  decoder_id: string;
  name: string;
  enabled: boolean;
  channels: Record<string, string>;
  settings: Record<string, unknown>;
  region?: number[] | null;
  status: 'idle' | 'running' | 'done' | 'error' | 'cancelled';
  error?: string | null;
  event_count: number;
  warning_count: number;
  quality_score?: number | null;
}

export interface DecoderEvent {
  id: string;
  decoder_id: string;
  type: string;
  start_sample: number;
  end_sample: number;
  start_time: number;
  end_time: number;
  label: string;
  severity: 'normal' | 'warning' | 'error';
  fields: Record<string, any>;
}

export interface DecoderDescription {
  id: string;
  name: string;
  description: string;
  consumes: string | null;
  channels: { role: string; name: string; required: boolean; types: string[] }[];
  settings: {
    key: string; name: string; type: string; default: any;
    options?: any[] | null; min?: number | null; max?: number | null; help: string;
  }[];
}

export interface MeasurementInstance {
  id: string;
  type: string;
  channels: string[];
  scope: 'capture' | 'cursors' | 'region';
  region?: number[] | null;
  settings: Record<string, unknown>;
  result?: Record<string, any> | null;
  error?: string | null;
}

export interface MeasurementType {
  id: string;
  name: string;
  category: string;
  unit: string;
  channel_types: string[];
  needs_decoder: boolean;
  description: string;
}

export interface Marker {
  id: string;
  sample: number;
  label: string;
  note: string;
  kind: string;
  channel?: string | null;
  color?: string | null;
}

export interface SessionSummary {
  id: string;
  name: string;
  created_at: number;
  modified_at: number;
  num_samples: number;
  sample_rate: number;
  duration_s: number;
  channel_count: number;
  has_analog: boolean;
  decoder_count: number;
  marker_count: number;
  tags: string[];
  notes: string;
  device: string;
  mock: boolean;
}

export interface Session {
  id: string;
  name: string;
  created_at: number;
  modified_at: number;
  app_version: string;
  device: DeviceMetadata;
  settings: CaptureSettings;
  sample_rate: number;
  divider?: number | null;
  sample_clk_hz: number;
  num_samples: number;
  trigger_sample?: number | null;
  channels: ChannelInfo[];
  decoders: DecoderInstance[];
  measurements: MeasurementInstance[];
  markers: Marker[];
  notes: string;
  tags: string[];
  exports: { id: string; format: string; filename: string; timestamp: number }[];
  diagnostics: { level: string; message: string; ts?: number }[];
}

export interface BackendStatus {
  app_version: string;
  uptime_s: number;
  device_connected: boolean;
  device_kind: string | null;
  device: DeviceMetadata | null;
  capture_state: string;
  capture_progress: { samples_read: number; samples_total: number; message: string; repeat: number };
  last_session_id: string | null;
  last_error: string | null;
  control: { held: boolean; holder: string | null; holder_name: string; acquired_at: number };
  ws_clients: number;
  session_count: number;
}

export interface VirtualComPair {
  index: string;
  a: string;
  b: string;
}

export interface VirtualBridgeStatus {
  driver: {
    available: boolean;
    name: string;
    setup_path?: string | null;
    ports: VirtualComPair[];
    error?: string | null;
  };
  running: boolean;
  transport?: 'tcp' | 'com' | null;
  app_port?: string | null;
  tcp_host?: string | null;
  tcp_port?: number | null;
  tcp_endpoint?: string | null;
  protocol: string;
  hardware_changes: boolean;
  physical_interfaces_untouched: boolean;
  detail: string;
}

export interface VirtualComPairCreateResponse {
  created: boolean;
  port_a: string;
  port_b: string;
  driver: VirtualBridgeStatus['driver'];
  output: string;
  pairs: VirtualComPair[];
}

export interface LogEntry {
  ts: number;
  level: string;
  logger: string;
  message: string;
}

export interface WsMessage {
  type: string;
  ts: number;
  data: any;
}

export interface GeneratorConfig {
  protocol: string;
  data_hex: string;
  baud: number;
  tx_pin: number;
  scl_pin: number;
  i2c_address: number;
  i2c_register: number;
  i2c_read_len: number;
  freq_hz: number;
  duty_pct: number;
  repeat: number;
  continuous: boolean;
  extra?: Record<string, any>;
}

export type MilProtocol = 'uart' | 'modbus_uart' | 'rs485_modbus';

export interface MilRegister {
  address: number;
  name: string;
  width: number;
  access: 'ro' | 'rw' | 'wo';
  value: number;
  response_hex?: string | null;
  description: string;
}

export interface MilTrigger {
  mode: 'uart_start_bit' | 'modbus_frame';
  rx_pin: number;
  tx_pin: number;
  baud: number;
  data_bits: number;
  parity: 'none' | 'even' | 'odd';
  stop_bits: number;
  rs485_de_pin?: number | null;
  frame_gap_chars: number;
}

export interface MilTiming {
  response_delay_us: number;
  inter_byte_gap_us: number;
  jitter_us: number;
}

export interface MilCaptureConfig {
  mode: 'auto' | 'manual';
  sample_rate: number;
  max_response_bytes: number;
  manual_post_packet_us: number;
  extra_digital_channels: number[];
}

export interface MilNode {
  unit_id: number;
  name: string;
  registers: MilRegister[];
  description: string;
}

export interface MilConfig {
  name: string;
  protocol: MilProtocol;
  description: string;
  trigger: MilTrigger;
  timing: MilTiming;
  capture: MilCaptureConfig;
  unit_id: number;
  registers: MilRegister[];
  nodes?: MilNode[];
  default_response_hex: string;
  notes: string[];
}

export interface MilPresetSummary {
  id: string;
  name: string;
  protocol: MilProtocol;
  description: string;
  source: string;
}

export interface MilRuntimeStatus {
  loaded: boolean;
  running: boolean;
  config?: MilConfig | null;
  preset_id?: string | null;
  last_error?: string | null;
  events: Record<string, any>[];
}

export interface MilTransactionResponse {
  request_hex: string;
  response_hex: string;
  detail: string;
  register_address?: number | null;
  unit_id?: number | null;
  action: string;
  session_id?: string | null;
}

export const defaultTrigger = (): TriggerConfig => ({
  type: 'none', channels: [], pre_trigger_samples: 0, position_pct: 0,
  execution: 'hardware', clock_source: 'external_edge', clock_edge: 'rising',
  start_mode: 'edge_on_channel', start_channel: 0, start_polarity: 0,
  clock_channel: 0, frame_width: 8, match_mask: 0xFFFFFFFF, bit_order: 'lsb_first',
});

export const defaultCaptureSettings = (): CaptureSettings => ({
  sample_rate: 1_000_000,
  num_samples: 100_000,
  mode: 'single',
  analog_enabled: false,
  enabled_digital: Array.from({ length: 16 }, (_, i) => i),
  trigger: defaultTrigger(),
  auto_rearm: false,
  repeat_count: 1,
  auto_save: false,
  readback_compression: 'raw',
  packed_mode: false,
  mock_scenario: 'demo_mixed',
});

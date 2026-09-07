// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { EyePanel } from './EyePanel';
import { useApp } from '../state/appStore';
import { session } from '../test/session';
import type { ChannelInfo } from '../api/types';

const digital = (id: string, type: ChannelInfo['type'] = 'digital'): ChannelInfo => ({ id, name: id,
  type, enabled: true, units: '', volts_per_div: 1, offset: 0, probe_attenuation: 1,
  cal_gain: 1, cal_offset: 0, threshold: 0.5, coupling: '', members: [], display_base: 'hex' });
const draw = vi.fn();
const ctx = new Proxy({} as Record<string, unknown>, {
  get(object, property) { return object[property as string] ?? ((...args: unknown[]) => draw(String(property), ...args)); },
  set(object, property, value) { object[property as string] = value; return true; },
}) as unknown as CanvasRenderingContext2D;

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true); draw.mockClear();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx);
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 480 });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth'); });

it.each([
  [null, 'No session open.'],
  [session('analog', { channels: [digital('a0', 'analog')] }), 'No digital channels in this capture.'],
] as const)('renders the unavailable state', (activeSession, text) => {
  useApp.setState({ activeSession });
  render(<EyePanel />);
  expect(screen.getByText(text)).toBeTruthy();
});

it('requests the selected line and draws a normalized eye-density grid', async () => {
  const fetcher = vi.fn(async () => Response.json({ grid: [[0, 2], [1, 0]], traces: 1234, unit_samples: 8.5 }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s', { channels: [digital('d0'), digital('f0', 'derived'), digital('bus', 'bus')] }) });
  render(<EyePanel />);
  await waitFor(() => expect(screen.getByLabelText('Digital channel')).toHaveProperty('value', 'd0'));
  fireEvent.change(screen.getByLabelText('Digital channel'), { target: { value: 'f0' } });
  fireEvent.change(screen.getByLabelText('Bit/clock rate (baud)'), { target: { value: '0' } });
  expect(screen.getByLabelText('Bit/clock rate (baud)')).toHaveProperty('value', '1');
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  expect(await screen.findByText('1,234 folded traces · 8.50 samples/UI')).toBeTruthy();
  expect(fetcher).toHaveBeenCalledWith('/api/sessions/s/eye?channel=f0&baud=1', expect.anything());
  const canvas = screen.getByLabelText('Eye diagram') as HTMLCanvasElement;
  expect([canvas.width, canvas.height, canvas.style.width]).toEqual([960, 560, '480px']);
  expect(draw.mock.calls.filter(([method]) => method === 'fillRect')).toHaveLength(4);
  expect(draw.mock.calls).toContainEqual(['moveTo', 240, 0]);
});

it('shows busy state, reports analysis errors, and safely accepts an empty grid', async () => {
  let fail!: (error: Error) => void;
  const fetcher = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((_resolve, reject) => { fail = reject; }))
    .mockResolvedValueOnce(Response.json({ grid: [], traces: 0, unit_samples: 0 }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s', { channels: [digital('d0')] }) });
  render(<EyePanel />);
  await waitFor(() => expect(screen.getByLabelText('Digital channel')).toHaveProperty('value', 'd0'));
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  expect(screen.getByRole('button', { name: 'Computing…' }).hasAttribute('disabled')).toBe(true);
  fail(new Error('insufficient transitions'));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'insufficient transitions')).toBe(true));
  const canvas = screen.getByLabelText('Eye diagram');
  Object.defineProperty(canvas, 'parentElement', { configurable: true, value: null });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 0 });
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  expect(await screen.findByText('0 folded traces · 0.00 samples/UI')).toBeTruthy();
  expect((canvas as HTMLCanvasElement).width).toBe(480);
});

it('does not show an old analysis result after the user changes sessions', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  useApp.setState({ activeSession: session('old', { channels: [digital('d0')] }) });
  const { rerender } = render(<EyePanel />);
  await waitFor(() => expect(screen.getByLabelText('Digital channel')).toHaveProperty('value', 'd0'));
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  useApp.setState({ activeSession: session('new', { channels: [digital('d1')] }) });
  rerender(<EyePanel />);
  finish(Response.json({ grid: [[1]], traces: 999, unit_samples: 4 }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText(/999 folded traces/)).toBeNull();
});

it('ignores compute without a selected channel and suppresses stale errors', async () => {
  let fail!: (error: Error) => void;
  const fetcher = vi.fn(() => new Promise<Response>((_resolve, reject) => { fail = reject; }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('old', { channels: [digital('d0')] }) });
  const { rerender } = render(<EyePanel />);
  await waitFor(() => expect(screen.getByLabelText('Digital channel')).toHaveProperty('value', 'd0'));
  fireEvent.change(screen.getByLabelText('Digital channel'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Digital channel'), { target: { value: 'd0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Compute eye diagram' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  useApp.setState({ activeSession: session('new', { channels: [digital('d1')] }) }); rerender(<EyePanel />);
  fail(new Error('old failure'));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(useApp.getState().toasts.some((toast) => toast.message === 'old failure')).toBe(false);
});

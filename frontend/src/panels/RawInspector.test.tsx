// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RawInspector } from './RawInspector';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

beforeEach(async () => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  await waveformView.load('', 256, 1000, null);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps the latest raw window when responses arrive out of order', async () => {
  let finishOld!: (response: Response) => void;
  const fetcher = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOld = resolve; }))
    .mockResolvedValueOnce(Response.json({ digital_packed: [0x00ff] }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s') });
  render(<RawInspector />);
  fireEvent.click(screen.getByRole('button', { name: '⟩' }));
  expect(await screen.findByText('0x00FF')).toBeTruthy();
  await act(async () => finishOld(Response.json({ digital_packed: [0xaaaa] })));
  expect(screen.queryByText('0xAAAA')).toBeNull();
  expect(screen.getByText('0x00FF')).toBeTruthy();
  expect(fetcher).toHaveBeenNthCalledWith(2, '/api/sessions/s/raw?start=64&end=128', expect.anything());
});

it('shows empty state without a session and does not make a request', () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  render(<RawInspector />);
  expect(screen.getByText('No session open.')).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});

it('displays exact hex and bit values, bounds navigation, and jumps from a row', async () => {
  const fetcher = vi.fn(async () => Response.json({ digital_packed: [0x8001, 0] }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s') });
  waveformView.cursorA = 10;
  render(<RawInspector />);
  expect(await screen.findByText('0x8001')).toBeTruthy();
  expect(screen.getByText('1000 0000 0000 0001')).toBeTruthy();
  expect(screen.getByText('0x0000')).toBeTruthy();
  expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('10');
  fireEvent.click(screen.getByText('0x8001'));
  expect([waveformView.start, waveformView.end]).toEqual([0, 256]);
  fireEvent.click(screen.getByRole('button', { name: '⟨' }));
  await waitFor(() => expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('0'));
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1000' } });
  await waitFor(() => expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('192'));
  waveformView.cursorA = null;
  fireEvent.click(screen.getByRole('button', { name: '@ cursor A' }));
  await waitFor(() => expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('0'));
  waveformView.cursorA = 42;
  fireEvent.click(screen.getByRole('button', { name: '@ cursor A' }));
  await waitFor(() => expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('42'));
  waveformView.setView(100.9, 150.9);
  fireEvent.click(screen.getByRole('button', { name: '@ view' }));
  await waitFor(() => expect(screen.getByRole('spinbutton').getAttribute('value')).toBe('100'));
});

it('clears rows for missing digital data and current errors, but ignores obsolete errors', async () => {
  let failOld!: (error: Error) => void;
  const fetcher = vi.fn()
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { failOld = reject; }))
    .mockResolvedValueOnce(Response.json({ digital_packed: [42] }))
    .mockResolvedValueOnce(Response.json({}))
    .mockRejectedValueOnce(new Error('offline'));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s') });
  render(<RawInspector />);
  fireEvent.click(screen.getByRole('button', { name: '⟩' }));
  expect(await screen.findByText('0x002A')).toBeTruthy();
  await act(async () => failOld(new Error('obsolete')));
  expect(screen.getByText('0x002A')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '⟩' }));
  await waitFor(() => expect(screen.queryByText('0x002A')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: '⟩' }));
  await act(async () => {});
  expect(screen.getAllByRole('row')).toHaveLength(1);
});

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MarkerPanel } from './MarkerPanel';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import type { Marker } from '../api/types';

beforeEach(async () => {
  localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  useApp.setState(useApp.getInitialState(), true);
  await waveformView.load('', 1000, 1000, null);
  waveformView.setView(100, 200);
});
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('explains that marker controls require an open session', () => {
  render(<MarkerPanel />);
  expect(screen.getByText('No session open.')).toBeTruthy();
});

it('creates, edits, orders, navigates and deletes bookmarks through the server API', async () => {
  const markers: Marker[] = [
    { id: 'cursor', sample: 1, label: '', note: '', kind: 'cursor_a' },
    { id: 'late', sample: 300, label: 'late', note: '', kind: 'bookmark' },
    { id: 'early', sample: 50, label: '', note: 'existing', kind: 'bookmark' },
  ];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      markers.push({ id: `m${markers.length}`, kind: 'bookmark', note: '', label: body.label, sample: body.sample });
      return Response.json(markers[markers.length - 1]);
    }
    if (init?.method === 'PATCH') {
      const marker = markers.find((item) => url.endsWith(item.id))!;
      marker.note = JSON.parse(String(init.body)).note;
      return Response.json(marker);
    }
    if (init?.method === 'DELETE') {
      markers.splice(markers.findIndex((item) => url.endsWith(item.id)), 1);
      return Response.json({});
    }
    return Response.json({ markers });
  });
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s') });
  waveformView.markers = markers;
  waveformView.cursorA = 25.6;
  waveformView.hoverSample = 75.4;
  render(<MarkerPanel />);
  expect(screen.getAllByRole('row')[1].textContent).toContain('bookmark50');
  fireEvent.click(screen.getByRole('button', { name: 'next marker ⟩' }));
  expect([waveformView.start, waveformView.end]).toEqual([250, 350]);
  fireEvent.click(screen.getByRole('button', { name: '⟨ prev marker' }));
  expect([waveformView.start, waveformView.end]).toEqual([0, 100]);
  fireEvent.change(screen.getByPlaceholderText('marker label'), { target: { value: 'edge' } });
  fireEvent.click(screen.getByRole('button', { name: '@ cursor A' }));
  await waitFor(() => expect(screen.getByText('edge')).toBeTruthy());
  expect(markers[markers.length - 1]).toMatchObject({ sample: 26, label: 'edge' });
  expect(screen.getByPlaceholderText('marker label').getAttribute('value')).toBe('');
  fireEvent.click(screen.getByRole('button', { name: '@ hover' }));
  await waitFor(() => expect(screen.getByText('M5')).toBeTruthy());
  const note = screen.getByDisplayValue('existing');
  fireEvent.click(note);
  fireEvent.change(note, { target: { value: 'updated' } });
  await waitFor(() => expect(markers.find((item) => item.id === 'early')?.note).toBe('updated'));
  fireEvent.click(screen.getByText('late'));
  expect([waveformView.start, waveformView.end]).toEqual([250, 350]);
  const lateRow = screen.getByText('late').closest('tr')!;
  fireEvent.click(lateRow.querySelector('button')!);
  await waitFor(() => expect(screen.queryByText('late')).toBeNull());
});

it('warns when no placement exists and reports API errors without losing bookmarks', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'control denied' }, { status: 409 })));
  useApp.setState({ activeSession: session('s') });
  waveformView.cursorA = null;
  waveformView.hoverSample = null;
  render(<MarkerPanel />);
  fireEvent.click(screen.getByRole('button', { name: '@ cursor A' }));
  expect(useApp.getState().toasts[useApp.getState().toasts.length - 1]).toMatchObject({ level: 'warning', message: 'Place cursor A or hover the waveform first' });
  waveformView.cursorA = 5;
  fireEvent.click(screen.getByRole('button', { name: '@ cursor A' }));
  await waitFor(() => expect(useApp.getState().toasts[useApp.getState().toasts.length - 1]).toMatchObject({ level: 'error', message: 'control denied' }));
  expect(screen.getByText(/No markers yet/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'next marker ⟩' }));
  fireEvent.click(screen.getByRole('button', { name: '⟨ prev marker' }));
  expect([waveformView.start, waveformView.end]).toEqual([100, 200]);
});

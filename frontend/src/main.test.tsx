// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';

const root = vi.hoisted(() => ({ render: vi.fn(), createRoot: vi.fn() }));
vi.mock('react-dom/client', () => ({ default: { createRoot: root.createRoot } }));
vi.mock('./App', () => ({ default: () => <div>application</div> }));

beforeEach(() => {
  vi.resetModules();
  root.render.mockReset();
  root.createRoot.mockReset().mockReturnValue({ render: root.render });
  document.body.innerHTML = '<div id="root"></div>';
});

it('mounts the application into the root element under strict mode', async () => {
  await import('./main');
  expect(root.createRoot).toHaveBeenCalledWith(document.getElementById('root'));
  expect(root.render).toHaveBeenCalledOnce();
});

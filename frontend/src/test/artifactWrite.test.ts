import { describe, expect, it, vi } from 'vitest';
import { isTransientArtifactWriteError, writeArtifactWithRetry } from './artifactWrite';

describe('artifact write retry', () => {
  it.each(['UNKNOWN', 'EPERM', 'EBUSY'])('recognizes transient %s failures', (code) => {
    expect(isTransientArtifactWriteError({ code })).toBe(true);
  });

  it('retries transient failures and then succeeds', async () => {
    const write = vi.fn<(_: string, __: string) => Promise<void>>()
      .mockRejectedValueOnce({ code: 'UNKNOWN' })
      .mockRejectedValueOnce({ code: 'EBUSY' })
      .mockResolvedValueOnce();
    const sleep = vi.fn<(_: number) => Promise<void>>().mockResolvedValue();

    await writeArtifactWithRetry('evidence.json', '{}', { write, sleep, delayMs: 10 });

    expect(write).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenNthCalledWith(1, 10);
    expect(sleep).toHaveBeenNthCalledWith(2, 20);
  });

  it('does not retry permanent failures or exhaust transient attempts', async () => {
    const permanent = vi.fn<(_: string, __: string) => Promise<void>>()
      .mockRejectedValue({ code: 'EACCES' });
    await expect(writeArtifactWithRetry('evidence.json', '{}', {
      write: permanent,
      sleep: vi.fn<(_: number) => Promise<void>>().mockResolvedValue(),
    })).rejects.toMatchObject({ code: 'EACCES' });
    expect(permanent).toHaveBeenCalledTimes(1);

    const transient = vi.fn<(_: string, __: string) => Promise<void>>()
      .mockRejectedValue({ code: 'UNKNOWN' });
    await expect(writeArtifactWithRetry('evidence.json', '{}', {
      write: transient,
      sleep: vi.fn<(_: number) => Promise<void>>().mockResolvedValue(),
      attempts: 2,
    })).rejects.toMatchObject({ code: 'UNKNOWN' });
    expect(transient).toHaveBeenCalledTimes(2);
  });
});

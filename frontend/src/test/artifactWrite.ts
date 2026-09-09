const TRANSIENT_WRITE_CODES = new Set(['UNKNOWN', 'EPERM', 'EBUSY']);

type ArtifactData = string;

type WriteOptions = {
  write: (filePath: string, data: ArtifactData) => Promise<void>;
  attempts?: number;
  delayMs?: number;
  sleep?: (delayMs: number) => Promise<void>;
};

function defaultSleep(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

export function isTransientArtifactWriteError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { code?: unknown; message?: unknown };
  if (typeof candidate.code === 'string' && TRANSIENT_WRITE_CODES.has(candidate.code)) return true;
  const message = typeof candidate.message === 'string' ? candidate.message : String(error);
  return /\b(?:UNKNOWN|EPERM|EBUSY)\b/.test(message);
}

/** Write test evidence while tolerating short-lived Windows Defender locks. */
export async function writeArtifactWithRetry(
  filePath: string,
  data: ArtifactData,
  { attempts = 5, delayMs = 250, write, sleep = defaultSleep }: WriteOptions,
): Promise<void> {
  const totalAttempts = Math.max(1, attempts);
  for (let attempt = 1; attempt <= totalAttempts; attempt += 1) {
    try {
      await write(filePath, data);
      return;
    } catch (error) {
      if (attempt === totalAttempts || !isTransientArtifactWriteError(error)) throw error;
      await sleep(delayMs * attempt);
    }
  }
}

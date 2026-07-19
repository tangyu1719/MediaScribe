const DEFAULT_DELAY_MS = 350;
const MIN_DELAY_MS = 50;
const MAX_DELAY_MS = 5_000;

interface TimedStep {
  recordedAt?: number;
}

/** 保留用户操作节奏，同时限制过短抖动和过长人工停顿。 */
export function replayDelayMs(previous: TimedStep | undefined, current: TimedStep): number {
  if (!previous) return 0;
  const before = Number(previous.recordedAt);
  const after = Number(current.recordedAt);
  if (!Number.isFinite(before) || !Number.isFinite(after) || after <= before) {
    return DEFAULT_DELAY_MS;
  }
  return Math.min(MAX_DELAY_MS, Math.max(MIN_DELAY_MS, Math.round(after - before)));
}

/** 与 webXport 一致：同 origin + pathname 视为同一 frame 上下文 */
export function frameUrlMatches(recorded: string | undefined, current: string): boolean {
  if (!recorded) return true;
  try {
    const a = new URL(recorded);
    const b = new URL(current);
    return a.origin === b.origin && a.pathname === b.pathname;
  } catch {
    return recorded === current;
  }
}

const UNITS = ["KB", "MB", "GB"] as const;

function scale(n: number): { value: number; unit: string } {
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < UNITS.length - 1) {
    value /= 1024;
    i += 1;
  }
  return { value, unit: UNITS[i] };
}

/** "2.3 MB (2,413,611 bytes)" — the human figure to read at a glance, and the
 *  exact one because that is the number that shows up in logs, S3 and
 *  metrics when something needs chasing down. */
export function formatBytes(n: number): string {
  const exact = `${n.toLocaleString()} bytes`;
  if (n < 1024) return exact;
  const { value, unit } = scale(n);
  return `${value.toFixed(value < 10 ? 1 : 0)} ${unit} (${exact})`;
}

/** Same figure without the parenthetical, for table cells. */
export function formatBytesShort(n: number): string {
  if (n < 1024) return `${n} B`;
  const { value, unit } = scale(n);
  return `${value.toFixed(value < 10 ? 1 : 0)} ${unit}`;
}

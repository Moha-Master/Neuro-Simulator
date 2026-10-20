/** 数据库时间（`YYYY-MM-DD HH:MM:SS`，本地时区）与 ISO 时间的统一展示。 */

function parseTimestamp(ts?: string): Date | null {
  if (!ts) return null;
  const normalized = ts.includes("T") ? ts : ts.replace(" ", "T")
  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? null : d
}

const pad = (n: number): string => String(n).padStart(2, "0")

/** 消息外侧/内容旁使用的短时间（HH:MM:SS）。 */
export function formatTime(ts?: string): string {
  const d = parseTimestamp(ts)
  if (!d) return ts ?? ""
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 折叠卡片展开后使用的完整时间（YYYY-MM-DD HH:MM:SS）。 */
export function formatDateTime(ts?: string): string {
  const d = parseTimestamp(ts)
  if (!d) return ts ?? ""
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

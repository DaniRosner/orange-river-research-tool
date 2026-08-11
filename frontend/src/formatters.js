// Shared display formatting for the Drive-style data tables (TickerDetail's
// file list, TickerList's ticker list) — kept separate from activityLabels.js
// since these format raw Dropbox metadata (size/modified), not activity_log
// records.

export function formatFileSize(bytes) {
  if (bytes == null) return '—'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent
  // Whole bytes stay exact; anything scaled up gets one decimal place,
  // matching how Drive itself rounds ("197 KB", "1.2 MB").
  const rounded = exponent === 0 ? value : Math.round(value * 10) / 10
  return `${rounded} ${units[exponent]}`
}

// "Aug 4" for the current year, "Aug 4, 2025" once it's not — same idea as
// Drive's own date column, which drops the year for anything recent.
export function formatDate(isoString) {
  if (!isoString) return '—'
  const date = new Date(isoString)
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString('en-US', sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' })
}

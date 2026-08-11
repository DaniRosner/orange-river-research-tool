// Human-readable past-tense verb for each activity_log action string (see
// backend/app/services/activity_log.py's `record()` for the literal
// values). Kept as its own module since both TickerList and TickerDetail
// need the same mapping for their "X by Y" captions.
const ACTION_LABELS = {
  uploaded: 'Uploaded',
  deleted: 'Deleted',
  assigned: 'Assigned',
  created: 'Created',
  moved: 'Moved',
  renamed: 'Renamed',
}

// "by Dani" on its own doesn't say what Dani did — this turns an activity
// record into e.g. "Uploaded by Dani", falling back to the raw action
// string (still better than nothing) if a new action type is ever added
// here without updating ACTION_LABELS above.
export function describeActivity(action, userName) {
  const verb = ACTION_LABELS[action] || action
  return `${verb} by ${userName}`
}

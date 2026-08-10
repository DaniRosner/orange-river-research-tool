import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Shows a warning banner if two theme folders (e.g. two different folders
// both named "... (.BB)") are claiming the same suffix. Left unnoticed,
// one of them silently stops receiving anything — see
// category_routing.find_duplicate_category_suffixes() for the full
// reasoning. Rendered at the top of the app, above both the ticker list
// and a ticker's detail view, since this is a structural issue that
// matters regardless of which screen is showing.
//
// Re-checks two ways: immediately whenever `recheckTrigger` changes (App
// bumps this after anything that creates/moves/deletes a ticker — a
// collision can appear the moment a ticker is created, e.g. via the
// empty-folder-drop flow, and waiting on a timer for that isn't good
// enough), and periodically regardless, as a backstop for any collision
// caused some other way (e.g. the user renaming a folder directly in
// Dropbox, which this app has no event for at all).
const RECHECK_INTERVAL_MS = 60_000

function CategorySuffixWarning({ recheckTrigger }) {
  // null = still loading / nothing to show. Non-empty object = real
  // collisions found.
  const [warnings, setWarnings] = useState(null)

  useEffect(() => {
    function check() {
      api
        .getCategorySuffixWarnings()
        .then(setWarnings)
        .catch(() => setWarnings(null))
    }
    check()
    const interval = setInterval(check, RECHECK_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [recheckTrigger])

  if (!warnings || Object.keys(warnings).length === 0) return null

  return (
    <div className="category-suffix-warning">
      <p>
        <strong>Heads up:</strong> more than one folder is using the same theme-folder suffix. Only one of each
        pair will actually receive files — the other won't get anything, with no error shown. Rename one of them
        to use a different suffix to fix this.
      </p>
      <ul>
        {Object.entries(warnings).map(([suffix, paths]) => (
          <li key={suffix}>
            <strong>{suffix}</strong> is used by: {paths.map((path) => path.split('/').pop()).join(', ')}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default CategorySuffixWarning

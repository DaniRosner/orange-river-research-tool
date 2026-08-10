import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { useAutoDismiss } from '../useAutoDismiss.js'

const STATUS_LABELS = { active: 'Active', inactive: 'Inactive', historicals: 'Historicals' }
const STATUSES = Object.keys(STATUS_LABELS)

// Cache of each ticker's file list, keyed by ticker name. Declared at
// module scope rather than component state because TickerDetail fully
// unmounts every time the user goes back to the list — a cache that
// needs to survive that has to live outside the component. Same idea as
// TickerList's tab cache: instant on a repeat visit, with a background
// refetch to catch anything that changed since. In-memory only, so it
// resets on a full page reload.
const filesCache = {}

// Each file from the API is {name, relative_path} — relative_path is ""
// for a file sitting directly in the ticker folder, or a subfolder path
// (e.g. "Old Models") for one nested inside it. A stable per-file key
// combining both, since two different subfolders could otherwise contain
// same-named files.
function fileKey(file) {
  return `${file.relative_path}/${file.name}`
}

// Splits a flat file list into root-level files and subfolder groups, so
// the UI can render subfolders as their own collapsible section instead
// of silently flattening or hiding them.
function groupFiles(files) {
  const root = []
  const folders = new Map()
  for (const file of files) {
    if (!file.relative_path) {
      root.push(file)
      continue
    }
    if (!folders.has(file.relative_path)) folders.set(file.relative_path, [])
    folders.get(file.relative_path).push(file)
  }
  return { root, folders }
}

// Shows one ticker's file list, plus controls to move it between Active,
// Inactive, and Historicals, delete individual files, or delete the
// entire ticker. `status` (the ticker's status when this screen was
// opened) is passed in from wherever the user clicked (the ticker list or
// a search result) rather than looked up again here.
function TickerDetail({ ticker, status, onBack, onDataChanged }) {
  // Seeded from the cache so a repeat visit to a ticker already shows its
  // files immediately, before the background refetch below even resolves.
  const [files, setFiles] = useState(() => filesCache[ticker] || [])
  // Tracked separately from the `status` prop so the "Move to ..."
  // buttons update immediately after a successful move, without needing
  // to re-fetch or navigate away and back.
  const [currentStatus, setCurrentStatus] = useState(status)
  // Auto-clears itself a few seconds after being set — see useAutoDismiss.
  const [message, setMessage] = useAutoDismiss('')
  // Which single file (if any, identified by fileKey) currently has its
  // "really delete this?" confirmation showing. Only one at a time —
  // null means none.
  const [confirmDeleteFile, setConfirmDeleteFile] = useState(null)
  // Whether the "delete this whole ticker" confirmation is showing.
  const [confirmDeleteTicker, setConfirmDeleteTicker] = useState(false)
  // Whether the rename input is showing, and what's currently typed into
  // it. Works on theme folders too, not just real tickers — e.g. renaming
  // "Toronto Names (.TO)" to resolve a suffix collision, without needing
  // to go into Dropbox directly.
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(ticker)
  // Which subfolders are currently expanded, by relative_path. Starts
  // empty and gets seeded with every subfolder once files load, so
  // everything shows expanded by default rather than requiring a click
  // just to see what's there.
  const [expandedFolders, setExpandedFolders] = useState(() => new Set())

  useEffect(() => {
    api
      .getFilesForTicker(ticker)
      .then((data) => {
        filesCache[ticker] = data
        setFiles(data)
        setExpandedFolders(new Set(data.map((f) => f.relative_path).filter(Boolean)))
      })
      .catch(() => setFiles([]))
  }, [ticker])

  function toggleFolder(relativePath) {
    setExpandedFolders((prev) => {
      const next = new Set(prev)
      if (next.has(relativePath)) {
        next.delete(relativePath)
      } else {
        next.add(relativePath)
      }
      return next
    })
  }

  async function handleMove(targetStatus) {
    try {
      await api.moveTicker(ticker, targetStatus)
      setCurrentStatus(targetStatus)
      setMessage(`Moved to ${STATUS_LABELS[targetStatus]}.`)
    } catch (err) {
      setMessage(`Move failed: ${err.message}`)
    }
  }

  async function handleDeleteFile(file) {
    try {
      await api.deleteTickerFile(ticker, file.name, file.relative_path)
      // Update the list locally instead of re-fetching — we already know
      // exactly what changed. Keep the cache in sync too, so a later
      // revisit doesn't resurrect the deleted file for a moment.
      const next = files.filter((f) => fileKey(f) !== fileKey(file))
      filesCache[ticker] = next
      setFiles(next)
      setMessage(`"${file.name}" deleted.`)
    } catch (err) {
      setMessage(`Failed to delete "${file.name}": ${err.message}`)
    } finally {
      setConfirmDeleteFile(null)
    }
  }

  async function handleRename() {
    const newName = renameValue.trim()
    if (!newName || newName === ticker) {
      setRenaming(false)
      setRenameValue(ticker)
      return
    }
    try {
      await api.renameTicker(ticker, newName)
      delete filesCache[ticker]
      // The name changed, so this screen's `ticker` prop is now stale for
      // everything (files, cache key, etc.) — simplest correct thing is
      // to go back to the list, which will show the new name once it
      // refreshes. Also could be exactly what resolves a suffix
      // collision, so let App know to re-check the warning banner.
      onDataChanged?.()
      onBack()
    } catch (err) {
      setMessage(`Rename failed: ${err.message}`)
      setRenaming(false)
      setRenameValue(ticker)
    }
  }

  async function handleDeleteTicker() {
    try {
      await api.deleteTicker(ticker)
      delete filesCache[ticker]
      // Deleting a ticker could be exactly what resolves a suffix
      // collision (if this ticker's name was the one colliding with a
      // theme folder) — let App know to re-check the warning banner.
      onDataChanged?.()
      // The ticker no longer exists, so there's nothing left to show —
      // go back to the list, which will naturally no longer include it
      // next time it refreshes.
      onBack()
    } catch (err) {
      setMessage(`Failed to delete ${ticker}: ${err.message}`)
      setConfirmDeleteTicker(false)
    }
  }

  function renderFileRow(file) {
    const key = fileKey(file)
    return (
      <li key={key}>
        {file.name}{' '}
        {confirmDeleteFile === key ? (
          <span>
            Delete this file?{' '}
            <button onClick={() => handleDeleteFile(file)}>Yes, delete</button>{' '}
            <button onClick={() => setConfirmDeleteFile(null)}>Cancel</button>
          </span>
        ) : (
          <button onClick={() => setConfirmDeleteFile(key)}>Delete</button>
        )}
      </li>
    )
  }

  const { root, folders } = groupFiles(files)

  return (
    <div className="ticker-detail">
      <button onClick={onBack}>&larr; Back</button>
      <h2>{ticker}</h2>

      {renaming ? (
        <span>
          <input type="text" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} />
          <button onClick={handleRename}>Save</button>{' '}
          <button
            onClick={() => {
              setRenaming(false)
              setRenameValue(ticker)
            }}
          >
            Cancel
          </button>
        </span>
      ) : (
        <button onClick={() => setRenaming(true)}>Rename</button>
      )}{' '}

      {STATUSES.includes(currentStatus) && (
        <>
          {STATUSES.filter((s) => s !== currentStatus).map((targetStatus) => (
            <button key={targetStatus} onClick={() => handleMove(targetStatus)}>
              Move to {STATUS_LABELS[targetStatus]}
            </button>
          ))}{' '}
        </>
      )}
      {confirmDeleteTicker ? (
        <span>
          Delete this entire ticker and all its files? This can't be undone from the app.{' '}
          <button onClick={handleDeleteTicker}>Yes, delete {ticker}</button>{' '}
          <button onClick={() => setConfirmDeleteTicker(false)}>Cancel</button>
        </span>
      ) : (
        <button onClick={() => setConfirmDeleteTicker(true)}>Delete ticker</button>
      )}

      {message && <p>{message}</p>}

      <ul>{root.map(renderFileRow)}</ul>

      {Array.from(folders.entries()).map(([relativePath, folderFiles]) => (
        <div key={relativePath} className="ticker-detail__subfolder">
          <button onClick={() => toggleFolder(relativePath)}>
            {expandedFolders.has(relativePath) ? '▾' : '▸'} {relativePath}/
          </button>
          {expandedFolders.has(relativePath) && <ul className="ticker-detail__subfolder-files">{folderFiles.map(renderFileRow)}</ul>}
        </div>
      ))}
    </div>
  )
}

export default TickerDetail

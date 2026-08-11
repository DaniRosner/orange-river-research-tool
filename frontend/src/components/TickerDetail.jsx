import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { useAutoDismiss } from '../useAutoDismiss.js'
import { describeActivity } from '../activityLabels.js'
import { formatDate, formatFileSize } from '../formatters.js'
import RowMenu from './RowMenu.jsx'
import FileThumbnail from './FileThumbnail.jsx'

const STATUS_LABELS = { active: 'Active', inactive: 'Inactive', historicals: 'Historicals' }
const STATUSES = Object.keys(STATUS_LABELS)

// Combines field + direction into one choice (rather than two separate
// controls) — `size`/`modified` come free off the same Dropbox listing
// call already being made (see dropbox_client.list_folder_recursive()),
// so none of these cost an extra request.
const SORT_OPTIONS = {
  'name-asc': { label: 'Name (A–Z)', compare: (a, b) => a.name.localeCompare(b.name) },
  'name-desc': { label: 'Name (Z–A)', compare: (a, b) => b.name.localeCompare(a.name) },
  'modified-desc': { label: 'Date modified (newest)', compare: (a, b) => new Date(b.modified) - new Date(a.modified) },
  'modified-asc': { label: 'Date modified (oldest)', compare: (a, b) => new Date(a.modified) - new Date(b.modified) },
  'size-desc': { label: 'Size (largest)', compare: (a, b) => b.size - a.size },
  'size-asc': { label: 'Size (smallest)', compare: (a, b) => a.size - b.size },
}

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
// (e.g. "Old Models", or "Old Models/Q1" nested two deep) for one nested
// inside it. A stable per-file key combining both, since two different
// subfolders could otherwise contain same-named files.
function fileKey(file) {
  return `${file.relative_path}/${file.name}`
}

// Splits the ticker's full (already-recursive) listing into what belongs
// directly at `basePath` — the files sitting right there, and the names
// of its immediate child subfolders — without needing a separate fetch
// per folder level. `basePath` is "" for the ticker's own root, or a
// (possibly multi-segment) subfolder path for anywhere navigated into via
// onNavigateToSubfolder. This is what makes clicking into "Old Models"
// (and, for anything nested further, "Old Models/Q1") behave like its own
// real screen instead of an inline expand — see TickerDetail's
// `subfolderPath` prop.
//
// `data` is {files, folders} (see api.js's getFilesForTicker) — most
// subfolders are inferred from files.relative_path alone, but `folders`
// is still needed on top of that for a genuinely empty one (e.g. just
// created via the "+ New folder" button), which has no file to infer it
// from otherwise.
function directChildren(data, basePath) {
  const prefix = basePath ? `${basePath}/` : ''
  const directFiles = []
  const folderNames = new Set()
  for (const file of data.files) {
    if (file.relative_path === basePath) {
      directFiles.push(file)
    } else if (file.relative_path.startsWith(prefix)) {
      folderNames.add(file.relative_path.slice(prefix.length).split('/')[0])
    }
  }
  for (const folderPath of data.folders) {
    if (folderPath.startsWith(prefix)) {
      folderNames.add(folderPath.slice(prefix.length).split('/')[0])
    }
  }
  return { directFiles, folders: Array.from(folderNames).sort() }
}

// Shows one ticker's file list, plus controls to move it between Active,
// Inactive, and Historicals, delete individual files, or delete the
// entire ticker. `status` (the ticker's status when this screen was
// opened) is passed in from wherever the user clicked (the ticker list or
// a search result) rather than looked up again here. `subfolderPath` ("" at
// the ticker's own root, otherwise e.g. "Old Models") and
// `onNavigateToSubfolder` come from the URL (see App.jsx's DetailView) —
// they're what let a subfolder be clicked into like its own real screen,
// with the ticker-level actions (rename/move/delete) only showing at the
// root, since they don't apply once browsing inside a subfolder.
function TickerDetail({ ticker, status, subfolderPath, onBack, onNavigateToSubfolder, onDataChanged }) {
  // {files, folders} — seeded from the cache so a repeat visit to a
  // ticker already shows its files immediately, before the background
  // refetch below even resolves.
  const [files, setFiles] = useState(() => filesCache[ticker] || { files: [], folders: [] })
  // True only on a genuinely first-ever visit to this ticker (nothing in
  // the cache yet) — shown as an explicit "Loading files…" message so a
  // real network delay reads as "still loading," not as "there's nothing
  // here." A repeat visit skips this entirely, since the cache already
  // has real data to show immediately.
  const [loading, setLoading] = useState(() => !(ticker in filesCache))
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
  // Which single file (if any, identified by fileKey) currently has its
  // "⋯" options menu open. Only one at a time.
  const [openFileMenu, setOpenFileMenu] = useState(null)
  // Same idea, for a subfolder card/row — identified by its full path
  // relative to the ticker root (e.g. "Old Filings", not just its own
  // name), since that's what's needed to actually delete it anyway.
  const [confirmDeleteFolder, setConfirmDeleteFolder] = useState(null)
  const [openFolderMenu, setOpenFolderMenu] = useState(null)
  // Whether the "delete this whole ticker" confirmation is showing.
  const [confirmDeleteTicker, setConfirmDeleteTicker] = useState(false)
  // Whether the rename input is showing, and what's currently typed into
  // it. Works on theme folders too, not just real tickers — e.g. renaming
  // "Toronto Names (.TO)" to resolve a suffix collision, without needing
  // to go into Dropbox directly.
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(ticker)
  // 'grid' (Drive/Docs-style preview cards) or 'list' (compact text rows)
  // — remembered across visits/tickers via localStorage, same idea as
  // Drive itself remembering your last view choice.
  const [viewMode, setViewMode] = useState(() => localStorage.getItem('ticker-detail-view-mode') || 'grid')
  // Client-side-only filter on filenames already loaded for this ticker —
  // same idea as TickerList's tab filter, not a real search.
  const [fileFilter, setFileFilter] = useState('')
  // Which SORT_OPTIONS key is active — also remembered across visits.
  const [sortKey, setSortKey] = useState(() => localStorage.getItem('ticker-detail-sort') || 'name-asc')
  // Whether the "+ New folder" input is showing, and what's currently
  // typed into it. Creates the folder wherever the user is currently
  // browsing (the ticker's own root, or a subfolder — see subfolderPath).
  const [creatingFolder, setCreatingFolder] = useState(false)
  const [newSubfolderName, setNewSubfolderName] = useState('')
  // Mass-selection for bulk delete. Checkboxes only ever show while
  // `selectionMode` is on — off by default, opted into via the toolbar's
  // "Select items" button or an item's own "⋯" → Select. `selected` is a
  // Set of keys prefixed `file:` or `folder:` (their own fileKey()/
  // relative path), so both kinds can be selected together and told apart
  // at delete time. All reset whenever the ticker or current subfolder
  // changes, since a selection made at one level has no meaning at
  // another.
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState(new Set())
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false)

  useEffect(() => {
    setSelectionMode(false)
    setSelected(new Set())
    setBulkDeleteConfirm(false)
  }, [ticker, subfolderPath])

  // Turns selection mode on, optionally pre-checking one item — see
  // TickerList.jsx's identical helper for why.
  function enterSelectionMode(key) {
    setSelectionMode(true)
    if (key != null) setSelected((prev) => new Set(prev).add(key))
  }

  function exitSelectionMode() {
    setSelectionMode(false)
    setSelected(new Set())
    setBulkDeleteConfirm(false)
  }

  function changeSortKey(key) {
    setSortKey(key)
    localStorage.setItem('ticker-detail-sort', key)
  }

  // Lets a table column header double as a sort control (click Name/Date
  // modified/File size to sort by it), same idea as Drive's own list-view
  // headers — clicking the active field flips direction, clicking a
  // different one switches to it with a sensible default direction (newest/
  // largest first for date and size, A–Z for name).
  function toggleSort(field) {
    const [currentField, currentDir] = sortKey.split('-')
    if (currentField === field) {
      changeSortKey(`${field}-${currentDir === 'asc' ? 'desc' : 'asc'}`)
    } else {
      changeSortKey(`${field}-${field === 'name' ? 'asc' : 'desc'}`)
    }
  }

  function changeViewMode(mode) {
    setViewMode(mode)
    localStorage.setItem('ticker-detail-view-mode', mode)
  }

  useEffect(() => {
    setLoading(!(ticker in filesCache))
    api
      .getFilesForTicker(ticker)
      .then((data) => {
        filesCache[ticker] = data
        setFiles(data)
        setLoading(false)
      })
      .catch(() => {
        setFiles({ files: [], folders: [] })
        setLoading(false)
      })
  }, [ticker])

  // Navigates into a subfolder sitting directly under whatever level is
  // currently showing — e.g. from the ticker's own root, clicking "Old
  // Models" goes to subfolderPath "Old Models"; from inside that, clicking
  // "Q1" goes to "Old Models/Q1". A real URL change (see App.jsx), not
  // local state, so it's back/forward-able and refreshable like any other
  // screen in the app.
  function handleOpenFolder(name) {
    onNavigateToSubfolder(subfolderPath ? `${subfolderPath}/${name}` : name)
  }

  // Creates an empty subfolder at whichever level is currently showing
  // (the ticker's own root, or wherever subfolderPath points into). Adds
  // it straight into local state/cache instead of re-fetching — we
  // already know exactly what changed, same reasoning as handleDeleteFile.
  async function handleCreateFolder() {
    const name = newSubfolderName.trim()
    setCreatingFolder(false)
    setNewSubfolderName('')
    if (!name) return
    try {
      await api.createSubfolder(ticker, name, subfolderPath)
      const newPath = subfolderPath ? `${subfolderPath}/${name}` : name
      const next = { ...files, folders: [...files.folders, newPath] }
      filesCache[ticker] = next
      setFiles(next)
      setMessage(`"${name}" created.`)
    } catch (err) {
      setMessage(`Failed to create "${name}": ${err.message}`)
    }
  }

  // Deletes a subfolder (and everything inside it) sitting directly at
  // whichever level is currently showing. `name` is just that folder's
  // own name — its full path relative to the ticker root (what the API
  // actually needs) is computed here the same way handleOpenFolder does.
  async function handleDeleteFolder(name) {
    const path = subfolderPath ? `${subfolderPath}/${name}` : name
    // Clear the confirm prompt immediately — see handleDeleteFile for why.
    setConfirmDeleteFolder(null)
    // Remove the folder itself, and anything that was inside it at any
    // depth (both other folders and files), from local state/cache right
    // away — rather than waiting for the request to finish, so it
    // disappears instantly instead of sitting there unchanged. Restored
    // below if the delete actually failed.
    const previous = files
    const prefix = `${path}/`
    const next = {
      files: files.files.filter((f) => f.relative_path !== path && !f.relative_path.startsWith(prefix)),
      folders: files.folders.filter((f) => f !== path && !f.startsWith(prefix)),
    }
    filesCache[ticker] = next
    setFiles(next)
    try {
      await api.deleteSubfolder(ticker, path)
      setMessage(`"${name}" deleted.`)
    } catch (err) {
      filesCache[ticker] = previous
      setFiles(previous)
      setMessage(`Failed to delete "${name}": ${err.message}`)
    }
  }

  function toggleSelect(key) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // Selects/deselects every currently-visible (filtered) file and folder
  // at once — `allKeys` is the combined list computed where this is
  // called, since a folder's key needs the folder-name-to-path logic that
  // only the render functions (with subfolderPath in scope) can build.
  function toggleSelectAll(allKeys) {
    setSelected((prev) => (prev.size === allKeys.length ? new Set() : new Set(allKeys)))
  }

  // Bulk-deletes every selected file and folder in parallel (allSettled,
  // not all — one bad item shouldn't stop the rest, same reasoning as the
  // upload batch queue). Everything selected disappears from the list
  // immediately rather than waiting for every delete to actually finish;
  // if anything turns out to have failed, a real refetch below reconciles
  // with the truth instead of trying to reconstruct it by hand.
  async function handleBulkDelete() {
    const keys = Array.from(selected)
    setSelected(new Set())
    setBulkDeleteConfirm(false)

    const targets = keys.map((key) => {
      if (key.startsWith('file:')) {
        const fk = key.slice('file:'.length)
        const file = files.files.find((f) => fileKey(f) === fk)
        return {
          type: 'file',
          key: fk,
          promise: file
            ? api.deleteTickerFile(ticker, file.name, file.relative_path)
            : Promise.reject(new Error('File not found')),
        }
      }
      const path = key.slice('folder:'.length)
      return { type: 'folder', key: path, promise: api.deleteSubfolder(ticker, path) }
    })

    const targetFileKeys = new Set(targets.filter((t) => t.type === 'file').map((t) => t.key))
    const targetFolderPaths = targets.filter((t) => t.type === 'folder').map((t) => t.key)
    const targetFolderPrefixes = targetFolderPaths.map((p) => `${p}/`)
    const isTargeted = (relativePath) =>
      targetFolderPaths.includes(relativePath) || targetFolderPrefixes.some((p) => relativePath.startsWith(p))
    const optimistic = {
      files: files.files.filter((f) => !targetFileKeys.has(fileKey(f)) && !isTargeted(f.relative_path)),
      folders: files.folders.filter((f) => !isTargeted(f)),
    }
    filesCache[ticker] = optimistic
    setFiles(optimistic)

    const results = await Promise.allSettled(targets.map((t) => t.promise))
    const failed = results.filter((r) => r.status === 'rejected').length

    if (failed === 0) {
      setMessage(`${keys.length} item${keys.length === 1 ? '' : 's'} deleted.`)
      return
    }

    setMessage(`Deleted ${keys.length - failed} of ${keys.length} items (${failed} failed).`)
    try {
      const fresh = await api.getFilesForTicker(ticker)
      filesCache[ticker] = fresh
      setFiles(fresh)
    } catch {
      // Best-effort reconciliation — if even this fails, the optimistic
      // (slightly wrong) state stays until the next natural refetch.
    }
  }

  async function handleMove(targetStatus) {
    // Show the new status immediately rather than waiting for the move to
    // actually finish — reverted below if it turns out to have failed.
    const previousStatus = currentStatus
    setCurrentStatus(targetStatus)
    try {
      await api.moveTicker(ticker, targetStatus)
      setMessage(`Moved to ${STATUS_LABELS[targetStatus]}.`)
    } catch (err) {
      setCurrentStatus(previousStatus)
      setMessage(`Move failed: ${err.message}`)
    }
  }

  async function handleDeleteFile(file) {
    // Clear the confirm prompt immediately, before the (async) delete call
    // below — otherwise it just sits there, visibly unchanged, for the
    // whole network round-trip after clicking "Yes, delete." Removing the
    // file from the list right away too, instead of only after the
    // request finishes — restored below if the delete actually failed.
    setConfirmDeleteFile(null)
    const previous = files
    const next = { ...files, files: files.files.filter((f) => fileKey(f) !== fileKey(file)) }
    filesCache[ticker] = next
    setFiles(next)
    try {
      await api.deleteTickerFile(ticker, file.name, file.relative_path)
      setMessage(`"${file.name}" deleted.`)
    } catch (err) {
      filesCache[ticker] = previous
      setFiles(previous)
      setMessage(`Failed to delete "${file.name}": ${err.message}`)
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
    // Same reasoning as handleDeleteFile — clear the confirm prompt right
    // away rather than leaving it up for the whole request.
    setConfirmDeleteTicker(false)
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
    }
  }

  // Opens a file in Dropbox's own preview UI in a new tab. The tab is
  // opened synchronously, right on the click, and only pointed at the
  // real URL once the (async) request resolves — opening it only after
  // the await would make most browsers treat it as an unrequested popup
  // and block it, since by then it's no longer running inside the
  // original click's event handler.
  function handleOpenFile(file) {
    const tab = window.open('', '_blank')
    api
      .getOpenLink(ticker, file.name, file.relative_path)
      .then(({ url }) => {
        if (tab) tab.location = url
      })
      .catch((err) => {
        if (tab) tab.close()
        setMessage(`Couldn't open "${file.name}": ${err.message}`)
      })
  }

  // A subfolder, rendered the same way a ticker itself is rendered in
  // TickerList (a plain folder icon, no peek at its contents) — clicking
  // navigates into it via handleOpenFolder rather than expanding inline.
  function renderFolderCard(name) {
    const key = subfolderPath ? `${subfolderPath}/${name}` : name
    return (
      <div key={`folder:${key}`} className="card">
        <div className="card__preview card__preview--icon" onClick={() => handleOpenFolder(name)}>
          📁
          {selectionMode && (
            <input
              type="checkbox"
              className="card__select"
              checked={selected.has(`folder:${key}`)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => toggleSelect(`folder:${key}`)}
              aria-label={`Select ${name}`}
            />
          )}
        </div>
        {confirmDeleteFolder === key ? (
          <div className="card__confirm">
            <p>Delete this folder and everything inside it?</p>
            <button className="danger" onClick={() => handleDeleteFolder(name)}>
              Yes, delete
            </button>
            <button onClick={() => setConfirmDeleteFolder(null)}>Cancel</button>
          </div>
        ) : (
          <div className="card__footer">
            <div className="card__footer-text">
              <span className="card__name" title={name} onClick={() => handleOpenFolder(name)}>
                {name}
              </span>
            </div>
            <RowMenu open={openFolderMenu === key} onToggle={() => setOpenFolderMenu(openFolderMenu === key ? null : key)}>
              <button
                onClick={() => {
                  enterSelectionMode(`folder:${key}`)
                  setOpenFolderMenu(null)
                }}
              >
                Select
              </button>
              <button
                className="danger"
                onClick={() => {
                  setConfirmDeleteFolder(key)
                  setOpenFolderMenu(null)
                }}
              >
                Delete
              </button>
            </RowMenu>
          </div>
        )}
      </div>
    )
  }

  // List-view counterpart to renderFolderCard — Owner/Date modified/File
  // size don't apply to a folder itself, so they show "—", same as a
  // ticker's own folder row in TickerList.
  function renderFolderListRow(name) {
    const key = subfolderPath ? `${subfolderPath}/${name}` : name
    if (confirmDeleteFolder === key) {
      return (
        <div key={`folder:${key}`} className="data-table__row data-table__row--confirm">
          <span>
            Delete this folder and everything inside it?{' '}
            <button className="danger" onClick={() => handleDeleteFolder(name)}>
              Yes, delete
            </button>{' '}
            <button onClick={() => setConfirmDeleteFolder(null)}>Cancel</button>
          </span>
        </div>
      )
    }
    return (
      <div key={`folder:${key}`} className={`data-table__row${selectionMode ? ' data-table__row--selecting' : ''}`}>
        {selectionMode && (
          <span className="data-table__select">
            <input
              type="checkbox"
              checked={selected.has(`folder:${key}`)}
              onChange={() => toggleSelect(`folder:${key}`)}
              aria-label={`Select ${name}`}
            />
          </span>
        )}
        <span className="data-table__name">
          <span className="data-table__folder-icon">📁</span>
          <span className="data-table__name-text" title={name} onClick={() => handleOpenFolder(name)}>
            {name}
          </span>
        </span>
        <span className="data-table__owner">—</span>
        <span className="data-table__modified">—</span>
        <span className="data-table__size">—</span>
        <span className="data-table__actions">
          <RowMenu open={openFolderMenu === key} onToggle={() => setOpenFolderMenu(openFolderMenu === key ? null : key)}>
            <button
              onClick={() => {
                enterSelectionMode(`folder:${key}`)
                setOpenFolderMenu(null)
              }}
            >
              Select
            </button>
            <button
              className="danger"
              onClick={() => {
                setConfirmDeleteFolder(key)
                setOpenFolderMenu(null)
              }}
            >
              Delete
            </button>
          </RowMenu>
        </span>
      </div>
    )
  }

  // Renders one file as a preview card (Drive/Docs-style grid) — see
  // FileThumbnail for how the preview image itself is resolved. Clicking
  // the preview or name opens the file in Dropbox; the "⋯" menu holds the
  // other actions (open, delete). See renderFileListRow below for the
  // compact-list alternative (viewMode === 'list').
  function renderFileCard(file) {
    const key = fileKey(file)
    return (
      <div key={key} className="card">
        <div className="card__preview" onClick={() => handleOpenFile(file)}>
          <FileThumbnail ticker={ticker} filename={file.name} relativePath={file.relative_path} size="large" />
          {selectionMode && (
            <input
              type="checkbox"
              className="card__select"
              checked={selected.has(`file:${key}`)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => toggleSelect(`file:${key}`)}
              aria-label={`Select ${file.name}`}
            />
          )}
        </div>
        {confirmDeleteFile === key ? (
          <div className="card__confirm">
            <p>Delete this file?</p>
            <button className="danger" onClick={() => handleDeleteFile(file)}>
              Yes, delete
            </button>
            <button onClick={() => setConfirmDeleteFile(null)}>Cancel</button>
          </div>
        ) : (
          <div className="card__footer">
            <div className="card__footer-text">
              <span className="card__name" title={file.name} onClick={() => handleOpenFile(file)}>
                {file.name}
              </span>
              {file.last_action_by && (
                <span className="card__meta">{describeActivity(file.last_action, file.last_action_by)}</span>
              )}
            </div>
            <RowMenu open={openFileMenu === key} onToggle={() => setOpenFileMenu(openFileMenu === key ? null : key)}>
              <button
                onClick={() => {
                  enterSelectionMode(`file:${key}`)
                  setOpenFileMenu(null)
                }}
              >
                Select
              </button>
              <button
                onClick={() => {
                  handleOpenFile(file)
                  setOpenFileMenu(null)
                }}
              >
                Open in Dropbox
              </button>
              <button
                className="danger"
                onClick={() => {
                  setConfirmDeleteFile(key)
                  setOpenFileMenu(null)
                }}
              >
                Delete
              </button>
            </RowMenu>
          </div>
        )}
      </div>
    )
  }

  // Drive-style column header for the list view — Name/Date modified/File
  // size double as sort controls (see toggleSort above); Owner doesn't,
  // since there's no "sort by who last touched it" option in SORT_OPTIONS.
  function renderFileTableHeader() {
    const [field, dir] = sortKey.split('-')
    const arrow = (col) => (field === col ? (dir === 'asc' ? ' ▲' : ' ▼') : '')
    return (
      <div className={`data-table__row data-table__header${selectionMode ? ' data-table__row--selecting' : ''}`}>
        {selectionMode && <span className="data-table__select" />}
        <span className="data-table__header-cell sortable" onClick={() => toggleSort('name')}>
          Name{arrow('name')}
        </span>
        <span className="data-table__header-cell">Owner</span>
        <span className="data-table__header-cell sortable" onClick={() => toggleSort('modified')}>
          Date modified{arrow('modified')}
        </span>
        <span className="data-table__header-cell sortable" onClick={() => toggleSort('size')}>
          File size{arrow('size')}
        </span>
        <span className="data-table__header-cell" />
      </div>
    )
  }

  // Compact-list alternative to renderFileCard above (viewMode === 'list')
  // — a Drive-style table row: Name / Owner / Date modified / File size,
  // copying Drive's own list-view column layout. Same actions (open,
  // delete) as the grid card, same underlying data.
  function renderFileListRow(file) {
    const key = fileKey(file)
    if (confirmDeleteFile === key) {
      return (
        <div key={key} className="data-table__row data-table__row--confirm">
          <span>
            Delete this file?{' '}
            <button className="danger" onClick={() => handleDeleteFile(file)}>
              Yes, delete
            </button>{' '}
            <button onClick={() => setConfirmDeleteFile(null)}>Cancel</button>
          </span>
        </div>
      )
    }
    return (
      <div key={key} className={`data-table__row${selectionMode ? ' data-table__row--selecting' : ''}`}>
        {selectionMode && (
          <span className="data-table__select">
            <input
              type="checkbox"
              checked={selected.has(`file:${key}`)}
              onChange={() => toggleSelect(`file:${key}`)}
              aria-label={`Select ${file.name}`}
            />
          </span>
        )}
        <span className="data-table__name">
          <FileThumbnail ticker={ticker} filename={file.name} relativePath={file.relative_path} />
          <span className="data-table__name-text" title={file.name} onClick={() => handleOpenFile(file)}>
            {file.name}
          </span>
        </span>
        <span
          className="data-table__owner"
          title={file.last_action_by ? describeActivity(file.last_action, file.last_action_by) : ''}
        >
          {file.last_action_by || '—'}
        </span>
        <span className="data-table__modified">{formatDate(file.modified)}</span>
        <span className="data-table__size">{formatFileSize(file.size)}</span>
        <span className="data-table__actions">
          <RowMenu open={openFileMenu === key} onToggle={() => setOpenFileMenu(openFileMenu === key ? null : key)}>
            <button
              onClick={() => {
                enterSelectionMode(`file:${key}`)
                setOpenFileMenu(null)
              }}
            >
              Select
            </button>
            <button
              onClick={() => {
                handleOpenFile(file)
                setOpenFileMenu(null)
              }}
            >
              Open in Dropbox
            </button>
            <button
              className="danger"
              onClick={() => {
                setConfirmDeleteFile(key)
                setOpenFileMenu(null)
              }}
            >
              Delete
            </button>
          </RowMenu>
        </span>
      </div>
    )
  }

  // Client-side filter, applied after loading — filters both the files and
  // the subfolders sitting directly at the current level (see
  // directChildren above) by name. Sort only applies to files; folders
  // always list first, alphabetically, same convention as most file
  // browsers (and as TickerList's own ticker sort, which is name-only too).
  const filterText = fileFilter.toLowerCase()
  const matchesFilter = (name) => name.toLowerCase().includes(filterText)
  const sortFiles = (fileList) => [...fileList].sort(SORT_OPTIONS[sortKey].compare)
  const { directFiles, folders } = directChildren(files, subfolderPath)
  const filteredFiles = sortFiles(directFiles.filter((file) => matchesFilter(file.name)))
  const filteredFolders = folders.filter(matchesFilter)

  const subfolderSegments = subfolderPath ? subfolderPath.split('/') : []

  return (
    <div className="ticker-detail">
      <button onClick={onBack}>&larr; Back</button>
      <h2>
        {subfolderSegments.length === 0 ? (
          ticker
        ) : (
          <span className="ticker-detail__breadcrumb">
            <span className="breadcrumb-link" onClick={() => onNavigateToSubfolder('')}>
              {ticker}
            </span>
            {subfolderSegments.map((segment, index) => {
              const isLast = index === subfolderSegments.length - 1
              const pathUpToHere = subfolderSegments.slice(0, index + 1).join('/')
              return (
                <span key={pathUpToHere}>
                  {' / '}
                  {isLast ? (
                    segment
                  ) : (
                    <span className="breadcrumb-link" onClick={() => onNavigateToSubfolder(pathUpToHere)}>
                      {segment}
                    </span>
                  )}
                </span>
              )
            })}
          </span>
        )}
      </h2>

      {/* Ticker-level actions only make sense at the ticker's own root —
          rename/move/delete act on the whole ticker folder, not whatever
          subfolder is currently being browsed. */}
      {!subfolderPath && (
        <div className="ticker-detail__actions">
          {renaming ? (
            <span className="ticker-detail__rename">
              <input type="text" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} />
              <button onClick={handleRename}>Save</button>
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
          )}

          {STATUSES.includes(currentStatus) &&
            STATUSES.filter((s) => s !== currentStatus).map((targetStatus) => (
              <button key={targetStatus} onClick={() => handleMove(targetStatus)}>
                Move to {STATUS_LABELS[targetStatus]}
              </button>
            ))}

          {confirmDeleteTicker ? (
            <span className="ticker-detail__confirm">
              Delete this entire ticker and all its files? This can't be undone from the app.{' '}
              <button className="danger" onClick={handleDeleteTicker}>
                Yes, delete {ticker}
              </button>{' '}
              <button onClick={() => setConfirmDeleteTicker(false)}>Cancel</button>
            </span>
          ) : (
            <button className="danger" onClick={() => setConfirmDeleteTicker(true)}>
              Delete ticker
            </button>
          )}
        </div>
      )}

      {message && <p>{message}</p>}

      {loading ? (
        <p>Loading files…</p>
      ) : (
        <div className="ticker-detail__files">
          <div className="ticker-detail__files-toolbar">
            <div className="ticker-detail__files-toolbar-left">
              <input
                type="text"
                placeholder="Filter files..."
                value={fileFilter}
                onChange={(e) => setFileFilter(e.target.value)}
              />
              {creatingFolder ? (
                <span className="ticker-detail__new-folder">
                  <input
                    type="text"
                    placeholder="Folder name"
                    autoFocus
                    value={newSubfolderName}
                    onChange={(e) => setNewSubfolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleCreateFolder()
                      if (e.key === 'Escape') {
                        setCreatingFolder(false)
                        setNewSubfolderName('')
                      }
                    }}
                  />
                  <button disabled={!newSubfolderName.trim()} onClick={handleCreateFolder}>
                    Create
                  </button>
                  <button
                    onClick={() => {
                      setCreatingFolder(false)
                      setNewSubfolderName('')
                    }}
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button onClick={() => setCreatingFolder(true)}>+ New folder</button>
              )}
            </div>
            <div className="ticker-detail__files-toolbar-right">
              {(filteredFolders.length > 0 || filteredFiles.length > 0) &&
                (selectionMode ? (
                  <span className="ticker-list__select-all">
                    <button
                      type="button"
                      onClick={() =>
                        toggleSelectAll([
                          ...filteredFolders.map((n) => `folder:${subfolderPath ? `${subfolderPath}/${n}` : n}`),
                          ...filteredFiles.map((f) => `file:${fileKey(f)}`),
                        ])
                      }
                    >
                      {selected.size === filteredFolders.length + filteredFiles.length ? 'Unselect all' : 'Select all'}
                    </button>
                    <button type="button" onClick={exitSelectionMode}>
                      Done
                    </button>
                  </span>
                ) : (
                  <button type="button" className="ticker-list__select-all" onClick={() => enterSelectionMode()}>
                    Select items
                  </button>
                ))}
              <select value={sortKey} onChange={(e) => changeSortKey(e.target.value)}>
                {Object.entries(SORT_OPTIONS).map(([key, { label }]) => (
                  <option key={key} value={key}>
                    Sort: {label}
                  </option>
                ))}
              </select>
              <div className="view-toggle">
                <button
                  className={viewMode === 'grid' ? 'active' : ''}
                  onClick={() => changeViewMode('grid')}
                >
                  Preview
                </button>
                <button
                  className={viewMode === 'list' ? 'active' : ''}
                  onClick={() => changeViewMode('list')}
                >
                  List
                </button>
              </div>
            </div>
          </div>

          {selected.size > 0 && (
            <div className="bulk-action-bar">
              <span>{selected.size} selected</span>
              {bulkDeleteConfirm ? (
                <>
                  <span>Delete {selected.size} item{selected.size === 1 ? '' : 's'}?</span>
                  <button className="danger" onClick={handleBulkDelete}>
                    Yes, delete
                  </button>
                  <button onClick={() => setBulkDeleteConfirm(false)}>Cancel</button>
                </>
              ) : (
                <button className="danger" onClick={() => setBulkDeleteConfirm(true)}>
                  Delete
                </button>
              )}
              <button onClick={() => setSelected(new Set())}>Clear selection</button>
            </div>
          )}

          {/* Subfolders always list first (alphabetically, like a ticker's
              own folder cards in TickerList), then the files sitting
              directly at this level — clicking a subfolder navigates into
              it as its own screen (see handleOpenFolder) instead of
              expanding inline. */}
          {viewMode === 'grid' ? (
            <div className="card-grid">
              {filteredFolders.map(renderFolderCard)}
              {filteredFiles.map(renderFileCard)}
            </div>
          ) : (
            <div className="data-table">
              {renderFileTableHeader()}
              {filteredFolders.map(renderFolderListRow)}
              {filteredFiles.map(renderFileListRow)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default TickerDetail

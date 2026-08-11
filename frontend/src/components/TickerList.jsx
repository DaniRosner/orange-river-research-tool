import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { useAutoDismiss } from '../useAutoDismiss.js'
import { describeActivity } from '../activityLabels.js'
import { formatDate } from '../formatters.js'
import RowMenu from './RowMenu.jsx'
import FileThumbnail from './FileThumbnail.jsx'
import { TABS } from '../tabSlugs.js'

// The main list view: a name filter plus two very different kinds of list
// depending on the tab. Active/Inactive show clickable tickers (folders).
// Needs Review shows individual loose files, each with its own inline
// "assign to a ticker" control instead of being clickable, since a Needs
// Review item isn't a ticker you can open — it's a file waiting to be
// filed into one. The tabs themselves and the upload control live in
// Sidebar now, not here — this component only owns what's shown for
// whichever tab is currently active.
const FETCHERS = {
  Active: api.getActiveTickers,
  Inactive: api.getInactiveTickers,
  Historicals: api.getHistoricalsTickers,
  'Needs Review': api.getNeedsReview,
}

// Cache of each tab's list, keyed by tab name. Declared at module scope
// rather than component state because TickerList fully unmounts while a
// ticker's detail view is open (see App.jsx) — a cache that needs to
// survive that has to live outside the component, same idea as
// TickerDetail's per-ticker files cache. Without this, every "back" click
// remounted TickerList with an empty cache and had to refetch all four
// tabs before showing anything, even for a tab that had just been loaded
// seconds earlier.
//
// Starts as `null` per tab, not `[]` — same reasoning as TickerDetail's
// `loading` state: a real "hasn't loaded yet" needs to read differently
// from "loaded and genuinely has nothing in it," otherwise a first-ever
// visit to a tab renders as a blank list with zero feedback rather than
// "still loading."
const itemsCache = { Active: null, Inactive: null, Historicals: null, 'Needs Review': null }

function TickerList({ onSelectTicker, activeTab, onDataChanged, refreshTrigger }) {
  // Every tab's list, keyed by tab name, seeded from the cache above so a
  // remount shows previously-loaded tabs immediately. Switching tabs (via
  // Sidebar navigating to a new URL, which changes the `activeTab` prop)
  // reads straight from this same object, already populated ahead of time
  // by the mount effect below — which is what stops a tab switch from
  // ever painting a frame with the new tab's label but the old tab's
  // items still showing.
  const [itemsByTab, setItemsByTab] = useState(() => ({ ...itemsCache }))
  // `null` while this tab hasn't loaded yet (see itemsCache above), a real
  // array once it has (possibly empty).
  const items = itemsByTab[activeTab]
  const [filter, setFilter] = useState('')
  // What's currently typed into each Needs Review row's ticker box, keyed
  // by filename (so every row remembers its own input independently).
  const [assignTicker, setAssignTicker] = useState({})
  // Auto-clears itself a few seconds after being set — see useAutoDismiss.
  const [listMessage, setListMessage] = useAutoDismiss('')
  // Holds the "did you mean...?" suggestion data for whichever Needs
  // Review row(s) currently need it, keyed by filename — a plain object
  // rather than a single value because in principle more than one row
  // could be mid-confirmation at once.
  const [confirmState, setConfirmState] = useState({})
  // Which Needs Review filename (if any) currently has its "really delete
  // this?" confirmation showing.
  const [confirmDeleteFile, setConfirmDeleteFile] = useState(null)
  // Which Needs Review filename (if any) currently has its "⋯" options
  // menu open.
  const [openFileMenu, setOpenFileMenu] = useState(null)
  // Same idea, for a ticker row's own "⋯" menu (Active/Inactive/
  // Historicals tabs) — lets a ticker be moved, renamed, or deleted
  // straight from the list, without having to click into its detail page
  // first. TickerDetail keeps its own separate controls for all three too
  // (see TickerDetail.jsx) — this is an additional entry point to the
  // same actions, not a replacement.
  const [confirmDeleteTicker, setConfirmDeleteTicker] = useState(null)
  const [openTickerMenu, setOpenTickerMenu] = useState(null)
  // Which ticker (if any) is currently showing its rename input, and what
  // that input currently holds.
  const [renamingTicker, setRenamingTicker] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  // 'grid' (Drive-style folder-icon cards) or 'list' — same idea as
  // TickerDetail's own view toggle, remembered the same way. Only ever
  // used for the ticker tabs — Needs Review stays list-only, since it's a
  // triage workflow (inline assign controls per row), not something a
  // folder-icon grid fits.
  const [viewMode, setViewMode] = useState(() => localStorage.getItem('ticker-list-view-mode') || 'grid')
  // 'asc' or 'desc' — the only sort that's actually well-defined for a
  // list of ticker folders (see filteredItems below for why there's no
  // "date modified"/"size" option here the way the file view has).
  const [sortDir, setSortDir] = useState(() => localStorage.getItem('ticker-list-sort-dir') || 'asc')
  // Mass-selection for bulk move/delete. Checkboxes are only ever shown
  // while `selectionMode` is on — off by default, so the list looks
  // exactly like it always did until the user deliberately opts in (via
  // the toolbar's "Select items" button, or an item's own "⋯" → Select).
  // `selected` is a Set of ticker names (or, on the Needs Review tab,
  // filenames). Both reset on every tab switch, since a selection made on
  // one tab has no meaning on another.
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState(new Set())
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false)

  useEffect(() => {
    setSelectionMode(false)
    setSelected(new Set())
    setBulkDeleteConfirm(false)
  }, [activeTab])

  // Turns selection mode on, optionally pre-checking one item — used by
  // an item's own "⋯" → Select, so picking that entry both reveals every
  // checkbox AND starts the selection with that one item already checked.
  function enterSelectionMode(key) {
    setSelectionMode(true)
    if (key != null) setSelected((prev) => new Set(prev).add(key))
  }

  // Fully backs out of selection mode — hides every checkbox again and
  // drops whatever was selected. Distinct from "Clear selection" in the
  // bulk-action bar, which empties the selection but leaves selection
  // mode (and the checkboxes) on, for picking a new set right away.
  function exitSelectionMode() {
    setSelectionMode(false)
    setSelected(new Set())
    setBulkDeleteConfirm(false)
  }

  function changeViewMode(mode) {
    setViewMode(mode)
    localStorage.setItem('ticker-list-view-mode', mode)
  }

  function changeSortDir(dir) {
    setSortDir(dir)
    localStorage.setItem('ticker-list-sort-dir', dir)
  }

  // Who last created/renamed/moved/deleted each ticker *through this
  // app* — see backend/app/services/activity_log.py for why this isn't
  // just Dropbox's own "modified by". Refreshed alongside the tab data
  // below (same triggers: anything that could actually change it).
  const [tickerActivity, setTickerActivity] = useState({})

  // Re-fetches one tab's list into the cache — both the module-level copy
  // (so it survives an unmount) and component state (so it re-renders).
  function refreshTab(tab) {
    FETCHERS[tab]()
      .then((data) => {
        itemsCache[tab] = data
        setItemsByTab((prev) => ({ ...prev, [tab]: data }))
      })
      .catch(() => {
        itemsCache[tab] = []
        setItemsByTab((prev) => ({ ...prev, [tab]: [] }))
      })
  }

  // Re-fetches every tab. Used after actions that could change more than
  // one tab at once — e.g. assigning a Needs Review file to a brand-new
  // ticker removes it from Needs Review AND adds a ticker to one of the
  // other three tabs. Passed down to UploadButton too, for the same
  // reason (an upload can do the same thing).
  function refreshAll() {
    TABS.forEach(refreshTab)
    api.getTickerActivity().then(setTickerActivity).catch(() => {})
    // Anything that reaches refreshAll (an upload, an assign, a delete)
    // could have created, moved, or removed a ticker — any of which could
    // change whether a suffix collision exists, so let App know to
    // re-check the warning banner right away rather than waiting on its
    // own timer.
    onDataChanged?.()
  }

  // Load all four tabs once up front, so every tab already has real data
  // sitting in the cache before the user ever clicks on it.
  useEffect(refreshAll, [])

  // Switching tabs (now driven by Sidebar navigating to a new URL, which
  // changes the `activeTab` prop) reads straight from the cache (already
  // populated by the effect above), so the very same render that shows
  // the new tab's label also shows that tab's real items — no frame in
  // between where the label says one tab but the list still belongs to
  // the last one. A background refetch of just that tab is kicked off
  // too, in case something changed in Dropbox since the cache was last
  // filled; the visible list doesn't change until that resolves, so it
  // can't cause a flash either. Also fires once on mount (redundant with
  // the effect above for whichever tab starts active) — harmless, not
  // worth adding extra state just to skip it.
  useEffect(() => {
    refreshTab(activeTab)
  }, [activeTab])

  // The "+ New" upload control lives in Sidebar now, outside this
  // component, so it can't call refreshAll() directly the way it could
  // when it was rendered as a child here. This is how it reaches back in:
  // App bumps `refreshTrigger` after an upload completes, and this effect
  // catches that change. Guarded on `!= null` so the initial render
  // (before any upload has ever happened) doesn't trigger a redundant
  // refetch on top of the mount effect above.
  useEffect(() => {
    if (refreshTrigger != null) refreshAll()
  }, [refreshTrigger])

  // Client-side-only filter on the names already loaded for this tab —
  // NOT the same thing as FileSearch's real cross-ticker file search.
  // `items` is null while this tab's first load is still in flight (see
  // itemsCache) — filteredItems stays empty in that case, and the render
  // below shows a loading message instead of the (empty) list. Tickers
  // (not Needs Review — those are files, sorted separately if ever
  // needed) are sorted alphabetically: Dropbox's own listing order isn't
  // guaranteed to be alphabetical, and folders don't carry a meaningful
  // "date modified" the way files do, so this is the one sort that's both
  // free and actually well-defined for a list of ticker folders.
  const filteredItems = (items ?? [])
    .filter((item) => item.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => {
      if (activeTab === 'Needs Review') return 0
      return sortDir === 'asc' ? a.localeCompare(b) : b.localeCompare(a)
    })

  function toggleSelect(key) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // Selects/deselects every currently-*visible* (filtered) item — not
  // necessarily everything loaded for this tab, matching how a filter box
  // usually reads ("select all of what I'm looking at").
  function toggleSelectAll() {
    setSelected((prev) => (prev.size === filteredItems.length ? new Set() : new Set(filteredItems)))
  }

  // Moves every selected ticker to `targetTab` in parallel. Uses
  // allSettled (not all) so one bad ticker doesn't stop the rest — same
  // reasoning as the upload batch queue not aborting a whole drop over one
  // failed item.
  async function handleBulkMove(targetTab) {
    const tickers = Array.from(selected)
    setSelected(new Set())
    const results = await Promise.allSettled(tickers.map((t) => api.moveTicker(t, targetTab.toLowerCase())))
    const failed = results.filter((r) => r.status === 'rejected').length
    setListMessage(
      failed === 0
        ? `${tickers.length} ticker${tickers.length === 1 ? '' : 's'} moved to ${targetTab}.`
        : `Moved ${tickers.length - failed} of ${tickers.length} tickers to ${targetTab} (${failed} failed).`
    )
    refreshAll()
  }

  // Bulk-deletes whatever's selected — real tickers (and all their files)
  // on the ticker tabs, or loose files on Needs Review.
  async function handleBulkDelete() {
    const keys = Array.from(selected)
    setSelected(new Set())
    setBulkDeleteConfirm(false)
    const deleteOne = activeTab === 'Needs Review' ? api.deleteNeedsReviewFile : api.deleteTicker
    const results = await Promise.allSettled(keys.map(deleteOne))
    const failed = results.filter((r) => r.status === 'rejected').length
    const noun = activeTab === 'Needs Review' ? 'file' : 'ticker'
    setListMessage(
      failed === 0
        ? `${keys.length} ${noun}${keys.length === 1 ? '' : 's'} deleted.`
        : `Deleted ${keys.length - failed} of ${keys.length} ${noun}s (${failed} failed).`
    )
    refreshAll()
  }

  // Handles the initial "Assign" click (ticker comes from the typed
  // input) and every follow-up click in a confirm dialog (ticker passed
  // explicitly, along with whatever's already been decided). `options`
  // mirrors the backend's `force`/`target_status` params — `force` skips
  // the typo-suggestion check once that question's been answered;
  // `targetStatus` answers the Active/Inactive question for a brand-new
  // ticker.
  async function handleAssign(filename, ticker, options = {}) {
    const targetTicker = (ticker ?? assignTicker[filename] ?? '').trim()
    if (!targetTicker) return
    // Clear any confirm dialog already showing for this file immediately
    // — this is a no-op on a first-ever assign attempt (nothing to
    // clear), but on a resubmission (clicking a suggestion inside the
    // dialog) it stops the same dialog sitting there unchanged for the
    // whole request.
    setConfirmState((prev) => {
      const next = { ...prev }
      delete next[filename]
      return next
    })
    try {
      const result = await api.assignNeedsReviewFile(filename, targetTicker, options)

      if (result.status === 'confirm_needed' || result.status === 'new_ticker_needs_status') {
        setConfirmState((prev) => ({ ...prev, [filename]: { kind: result.status, ...result } }))
        return
      }

      // "assigned_category" means the typed value ended with a registered
      // category suffix (e.g. ".BB") and got routed straight into that
      // themed folder — see category_routing.py — rather than becoming a
      // normal ticker.
      if (result.status === 'assigned_category') {
        setListMessage(`"${filename}" filed into ${result.category_folder}.`)
      } else {
        // Show the ticker the backend actually filed it under (its real
        // stored casing), not necessarily whatever was typed — e.g.
        // typing "teST5" gets reported back as "Test5".
        setListMessage(`"${filename}" assigned to ${result.ticker}.`)
      }
      refreshAll()
    } catch (err) {
      setListMessage(`Failed to assign "${filename}": ${err.message}`)
    }
  }

  // Opens a Needs Review file in Dropbox's own preview UI in a new tab —
  // see TickerDetail.jsx's handleOpenFile for why the tab is opened
  // synchronously before the (async) link request resolves.
  function handleOpenFile(filename) {
    const tab = window.open('', '_blank')
    api
      .getOpenLink(null, filename)
      .then(({ url }) => {
        if (tab) tab.location = url
      })
      .catch((err) => {
        if (tab) tab.close()
        setListMessage(`Couldn't open "${filename}": ${err.message}`)
      })
  }

  async function handleDeleteFile(filename) {
    // Clear the confirm prompt immediately — see TickerDetail.jsx's
    // handleDeleteFile for why (otherwise it sits there unchanged for the
    // whole request after clicking "Yes, delete").
    setConfirmDeleteFile(null)
    try {
      await api.deleteNeedsReviewFile(filename)
      setListMessage(`"${filename}" deleted.`)
      refreshAll()
    } catch (err) {
      setListMessage(`Failed to delete "${filename}": ${err.message}`)
    }
  }

  async function handleDeleteTicker(ticker) {
    setConfirmDeleteTicker(null)
    try {
      await api.deleteTicker(ticker)
      setListMessage(`${ticker} deleted.`)
      refreshAll()
    } catch (err) {
      setListMessage(`Failed to delete ${ticker}: ${err.message}`)
    }
  }

  async function handleMoveTicker(ticker, targetTab) {
    // Close the "⋯" menu immediately on click, same reasoning — it was
    // staying open, visibly unchanged, for the whole move request.
    setOpenTickerMenu(null)
    try {
      await api.moveTicker(ticker, targetTab.toLowerCase())
      setListMessage(`${ticker} moved to ${targetTab}.`)
      refreshAll()
    } catch (err) {
      setListMessage(`Failed to move ${ticker}: ${err.message}`)
    }
  }

  async function handleRenameTicker(ticker) {
    const newName = renameValue.trim()
    if (!newName || newName === ticker) {
      setRenamingTicker(null)
      return
    }
    setRenamingTicker(null)
    try {
      await api.renameTicker(ticker, newName)
      setListMessage(`${ticker} renamed to ${newName}.`)
      refreshAll()
    } catch (err) {
      setListMessage(`Failed to rename ${ticker}: ${err.message}`)
    }
  }

  // Shared between renderTickerCard and renderTickerListRow below — same
  // three actions (move/rename/delete) regardless of which view is
  // showing.
  function renderTickerMenu(ticker) {
    return (
      <RowMenu open={openTickerMenu === ticker} onToggle={() => setOpenTickerMenu(openTickerMenu === ticker ? null : ticker)}>
        <button
          onClick={() => {
            enterSelectionMode(ticker)
            setOpenTickerMenu(null)
          }}
        >
          Select
        </button>
        {TABS.filter((tab) => tab !== 'Needs Review' && tab !== activeTab).map((targetTab) => (
          <button key={targetTab} onClick={() => handleMoveTicker(ticker, targetTab)}>
            Move to {targetTab}
          </button>
        ))}
        <button
          onClick={() => {
            setRenamingTicker(ticker)
            setRenameValue(ticker)
            setOpenTickerMenu(null)
          }}
        >
          Rename
        </button>
        <button
          className="danger"
          onClick={() => {
            setConfirmDeleteTicker(ticker)
            setOpenTickerMenu(null)
          }}
        >
          Delete
        </button>
      </RowMenu>
    )
  }

  // Drive-style folder-icon card (viewMode === 'grid') — a plain large
  // folder icon, not a peek at the ticker's contents (same reasoning as
  // FileThumbnail's card__preview--icon: Drive's own grid view doesn't
  // preview folder contents either, and it'd cost a real request per
  // ticker to build here for no real benefit).
  function renderTickerCard(ticker) {
    return (
      <div className="card" key={ticker}>
        <div
          className="card__preview card__preview--icon"
          onClick={() => onSelectTicker({ ticker, status: activeTab.toLowerCase() })}
        >
          📁
          {selectionMode && (
            <input
              type="checkbox"
              className="card__select"
              checked={selected.has(ticker)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => toggleSelect(ticker)}
              aria-label={`Select ${ticker}`}
            />
          )}
        </div>
        {renamingTicker === ticker ? (
          <div className="card__rename">
            <input type="text" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} autoFocus />
            <button onClick={() => handleRenameTicker(ticker)}>Save</button>
            <button onClick={() => setRenamingTicker(null)}>Cancel</button>
          </div>
        ) : confirmDeleteTicker === ticker ? (
          <div className="card__confirm">
            <p>Delete this ticker and all its files?</p>
            <button className="danger" onClick={() => handleDeleteTicker(ticker)}>
              Yes, delete
            </button>
            <button onClick={() => setConfirmDeleteTicker(null)}>Cancel</button>
          </div>
        ) : (
          <div className="card__footer">
            <div className="card__footer-text">
              <span
                className="card__name"
                title={ticker}
                onClick={() => onSelectTicker({ ticker, status: activeTab.toLowerCase() })}
              >
                {ticker}
              </span>
              {tickerActivity[ticker] && (
                <span className="card__meta">
                  {describeActivity(tickerActivity[ticker].action, tickerActivity[ticker].user_name)}
                </span>
              )}
            </div>
            {renderTickerMenu(ticker)}
          </div>
        )}
      </div>
    )
  }

  // Drive-style column header for the ticker list view — Name doubles as a
  // sort control (the only sort that's well-defined for a list of folders,
  // see filteredItems above); Owner/Date modified/File size are plain
  // labels, same layout as TickerDetail's file table for visual
  // consistency (folders show "—" for size, same as Drive's own folder
  // rows).
  function renderTickerTableHeader() {
    return (
      <div className={`data-table__row data-table__header${selectionMode ? ' data-table__row--selecting' : ''}`}>
        {selectionMode && <span className="data-table__select" />}
        <span className="data-table__header-cell sortable" onClick={() => changeSortDir(sortDir === 'asc' ? 'desc' : 'asc')}>
          Name{sortDir === 'asc' ? ' ▲' : ' ▼'}
        </span>
        <span className="data-table__header-cell">Owner</span>
        <span className="data-table__header-cell">Date modified</span>
        <span className="data-table__header-cell">File size</span>
        <span className="data-table__header-cell" />
      </div>
    )
  }

  // Compact-list alternative to renderTickerCard above (viewMode ===
  // 'list') — same actions, same underlying data, laid out as a Drive-style
  // table row (Name / Owner / Date modified / File size) to match
  // TickerDetail's file table.
  function renderTickerListRow(ticker) {
    const activity = tickerActivity[ticker]
    if (confirmDeleteTicker === ticker) {
      return (
        <div className="data-table__row data-table__row--confirm" key={ticker}>
          <span>
            Delete this ticker and all its files?{' '}
            <button className="danger" onClick={() => handleDeleteTicker(ticker)}>
              Yes, delete
            </button>{' '}
            <button onClick={() => setConfirmDeleteTicker(null)}>Cancel</button>
          </span>
        </div>
      )
    }
    return (
      <div className={`data-table__row${selectionMode ? ' data-table__row--selecting' : ''}`} key={ticker}>
        {selectionMode && (
          <span className="data-table__select">
            <input
              type="checkbox"
              checked={selected.has(ticker)}
              onChange={() => toggleSelect(ticker)}
              aria-label={`Select ${ticker}`}
            />
          </span>
        )}
        {renamingTicker === ticker ? (
          <span className="data-table__name">
            <input type="text" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} autoFocus />
            <button onClick={() => handleRenameTicker(ticker)}>Save</button>
            <button onClick={() => setRenamingTicker(null)}>Cancel</button>
          </span>
        ) : (
          <span className="data-table__name">
            <span className="data-table__folder-icon">📁</span>
            <span
              className="data-table__name-text"
              onClick={() => onSelectTicker({ ticker, status: activeTab.toLowerCase() })}
            >
              {ticker}
            </span>
          </span>
        )}
        <span className="data-table__owner" title={activity ? describeActivity(activity.action, activity.user_name) : ''}>
          {activity ? activity.user_name : '—'}
        </span>
        <span className="data-table__modified">{activity ? formatDate(activity.timestamp) : '—'}</span>
        <span className="data-table__size">—</span>
        <span className="data-table__actions">{renamingTicker !== ticker && renderTickerMenu(ticker)}</span>
      </div>
    )
  }

  return (
    <div className="ticker-list">
      <div className="ticker-list__toolbar">
        <span className="ticker-list__filter-wrap">
          <input
            type="text"
            placeholder={activeTab === 'Needs Review' ? 'Filter files...' : 'Filter tickers...'}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {filter && (
            <button
              type="button"
              className="ticker-list__filter-clear"
              onClick={() => setFilter('')}
              aria-label="Clear filter"
            >
              &times;
            </button>
          )}
        </span>
        <div className="ticker-list__toolbar-right">
          {filteredItems.length > 0 &&
            (selectionMode ? (
              <span className="ticker-list__select-all">
                <button type="button" onClick={toggleSelectAll}>
                  {selected.size === filteredItems.length ? 'Unselect all' : 'Select all'}
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
          {activeTab !== 'Needs Review' && (
            <>
              <select value={sortDir} onChange={(e) => changeSortDir(e.target.value)}>
                <option value="asc">Sort: Name (A–Z)</option>
                <option value="desc">Sort: Name (Z–A)</option>
              </select>
              <div className="view-toggle">
                <button className={viewMode === 'grid' ? 'active' : ''} onClick={() => changeViewMode('grid')}>
                  Preview
                </button>
                <button className={viewMode === 'list' ? 'active' : ''} onClick={() => changeViewMode('list')}>
                  List
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="bulk-action-bar">
          <span>{selected.size} selected</span>
          {activeTab !== 'Needs Review' &&
            TABS.filter((tab) => tab !== 'Needs Review' && tab !== activeTab).map((targetTab) => (
              <button key={targetTab} onClick={() => handleBulkMove(targetTab)}>
                Move to {targetTab}
              </button>
            ))}
          {bulkDeleteConfirm ? (
            <>
              <span>Delete {selected.size} {activeTab === 'Needs Review' ? 'files' : 'tickers'}?</span>
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

      {listMessage && <p>{listMessage}</p>}

      {items === null ? (
        <p>Loading{activeTab === 'Needs Review' ? ' files' : ' tickers'}…</p>
      ) : activeTab === 'Needs Review' ? (
        <>
          <ul>
            {filteredItems.map((filename) => (
              <li key={filename} className="file-row">
                {selectionMode && (
                  <input
                    type="checkbox"
                    checked={selected.has(filename)}
                    onChange={() => toggleSelect(filename)}
                    aria-label={`Select ${filename}`}
                  />
                )}
                <FileThumbnail ticker={null} filename={filename} />
                <span className="file-row__name">{filename}</span>{' '}
                <input
                  type="text"
                  placeholder="Ticker"
                  value={assignTicker[filename] || ''}
                  onChange={(e) => setAssignTicker({ ...assignTicker, [filename]: e.target.value })}
                />
                <button onClick={() => handleAssign(filename)}>Assign</button>{' '}
                {confirmDeleteFile === filename ? (
                  <span>
                    Delete this file?{' '}
                    <button className="danger" onClick={() => handleDeleteFile(filename)}>Yes, delete</button>{' '}
                    <button onClick={() => setConfirmDeleteFile(null)}>Cancel</button>
                  </span>
                ) : (
                  <RowMenu
                    open={openFileMenu === filename}
                    onToggle={() => setOpenFileMenu(openFileMenu === filename ? null : filename)}
                  >
                    <button
                      onClick={() => {
                        enterSelectionMode(filename)
                        setOpenFileMenu(null)
                      }}
                    >
                      Select
                    </button>
                    <button
                      onClick={() => {
                        handleOpenFile(filename)
                        setOpenFileMenu(null)
                      }}
                    >
                      Open in Dropbox
                    </button>
                    <button
                      className="danger"
                      onClick={() => {
                        setConfirmDeleteFile(filename)
                        setOpenFileMenu(null)
                      }}
                    >
                      Delete
                    </button>
                  </RowMenu>
                )}
                {confirmState[filename] && confirmState[filename].kind === 'confirm_needed' && (
                  <div className="confirm-dialog">
                    <p>"{confirmState[filename].requested_ticker}" isn't a recognized ticker. Did you mean:</p>
                    <ul>
                      {confirmState[filename].suggestions.map((suggestion) => (
                        <li key={suggestion}>
                          <button onClick={() => handleAssign(filename, suggestion, { force: true })}>
                            {suggestion}
                          </button>
                        </li>
                      ))}
                    </ul>
                    <button
                      onClick={() =>
                        handleAssign(filename, confirmState[filename].requested_ticker, {
                          force: true,
                          skipCategory: true,
                        })
                      }
                    >
                      No, create new ticker "{confirmState[filename].requested_ticker}"
                    </button>
                  </div>
                )}
                {confirmState[filename] && confirmState[filename].kind === 'new_ticker_needs_status' && (
                  <div className="confirm-dialog">
                    <p>
                      There's no ticker named "{confirmState[filename].requested_ticker}" yet — create a new ticker
                      folder for it. Which status?
                    </p>
                    {['active', 'inactive', 'historicals'].map((statusOption) => (
                      <button
                        key={statusOption}
                        onClick={() =>
                          handleAssign(filename, confirmState[filename].requested_ticker, {
                            force: true,
                            skipCategory: true,
                            targetStatus: statusOption,
                          })
                        }
                      >
                        {statusOption === 'active' ? 'Active' : statusOption === 'inactive' ? 'Inactive' : 'Historicals'}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : viewMode === 'grid' ? (
        <div className="card-grid">{filteredItems.map(renderTickerCard)}</div>
      ) : (
        <div className="data-table">
          {renderTickerTableHeader()}
          {filteredItems.map(renderTickerListRow)}
        </div>
      )}
    </div>
  )
}

export default TickerList

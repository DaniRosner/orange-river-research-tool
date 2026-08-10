import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { useAutoDismiss } from '../useAutoDismiss.js'
import UploadButton from './UploadButton.jsx'

const TABS = ['Active', 'Inactive', 'Historicals', 'Needs Review']

// The main list view: the three tabs, the upload button, a name filter,
// and — importantly — two very different kinds of list depending on the
// tab. Active/Inactive show clickable tickers (folders). Needs Review
// shows individual loose files, each with its own inline "assign to a
// ticker" control instead of being clickable, since a Needs Review item
// isn't a ticker you can open — it's a file waiting to be filed into one.
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
const itemsCache = { Active: [], Inactive: [], Historicals: [], 'Needs Review': [] }

function TickerList({ onSelectTicker, activeTab, onTabChange }) {
  // Every tab's list, keyed by tab name, seeded from the cache above so a
  // remount shows previously-loaded tabs immediately. Switching tabs reads
  // from this same object (combined with updating it in the same
  // synchronous click, see `selectTab` below), which is what stops a tab
  // switch from ever painting a frame with the new tab's label but the
  // old tab's items still showing.
  const [itemsByTab, setItemsByTab] = useState(() => ({ ...itemsCache }))
  const items = itemsByTab[activeTab]
  const [filter, setFilter] = useState('')
  // What's currently typed into each Needs Review row's ticker box, keyed
  // by filename (so every row remembers its own input independently).
  const [assignTicker, setAssignTicker] = useState({})
  // Auto-clears itself a few seconds after being set — see useAutoDismiss.
  const [assignMessage, setAssignMessage] = useAutoDismiss('')
  // Holds the "did you mean...?" suggestion data for whichever Needs
  // Review row(s) currently need it, keyed by filename — a plain object
  // rather than a single value because in principle more than one row
  // could be mid-confirmation at once.
  const [confirmState, setConfirmState] = useState({})
  // Which Needs Review filename (if any) currently has its "really delete
  // this?" confirmation showing.
  const [confirmDeleteFile, setConfirmDeleteFile] = useState(null)

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
  }

  // Load all four tabs once up front, so every tab already has real data
  // sitting in the cache before the user ever clicks on it.
  useEffect(refreshAll, [])

  // Switching tabs reads straight from the cache (already populated by
  // the effect above), so the very same render that shows the new tab's
  // label also shows that tab's real items — no frame in between where
  // the label says one tab but the list still belongs to the last one.
  // A background refetch is kicked off too, in case something changed in
  // Dropbox since the cache was last filled; the visible list doesn't
  // change until that resolves, so it can't cause a flash either.
  function selectTab(tab) {
    onTabChange(tab)
    refreshTab(tab)
  }

  // Client-side-only filter on the names already loaded for this tab —
  // NOT the same thing as FileSearch's real cross-ticker file search.
  const filteredItems = items.filter((item) => item.toLowerCase().includes(filter.toLowerCase()))

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
    try {
      const result = await api.assignNeedsReviewFile(filename, targetTicker, options)

      if (result.status === 'confirm_needed' || result.status === 'new_ticker_needs_status') {
        setConfirmState({ ...confirmState, [filename]: { kind: result.status, ...result } })
        return
      }

      setConfirmState((prev) => {
        const next = { ...prev }
        delete next[filename]
        return next
      })
      // "assigned_category" means the typed value ended with a registered
      // category suffix (e.g. ".BB") and got routed straight into that
      // themed folder — see category_routing.py — rather than becoming a
      // normal ticker.
      if (result.status === 'assigned_category') {
        setAssignMessage(`"${filename}" filed into ${result.category_folder}.`)
      } else {
        // Show the ticker the backend actually filed it under (its real
        // stored casing), not necessarily whatever was typed — e.g.
        // typing "teST5" gets reported back as "Test5".
        setAssignMessage(`"${filename}" assigned to ${result.ticker}.`)
      }
      refreshAll()
    } catch (err) {
      setAssignMessage(`Failed to assign "${filename}": ${err.message}`)
    }
  }

  async function handleDeleteFile(filename) {
    try {
      await api.deleteNeedsReviewFile(filename)
      setAssignMessage(`"${filename}" deleted.`)
      refreshAll()
    } catch (err) {
      setAssignMessage(`Failed to delete "${filename}": ${err.message}`)
    } finally {
      setConfirmDeleteFile(null)
    }
  }

  return (
    <div className="ticker-list">
      <div className="tabs">
        {TABS.map((tab) => (
          <button key={tab} className={tab === activeTab ? 'active' : ''} onClick={() => selectTab(tab)}>
            {tab}
          </button>
        ))}
      </div>

      <UploadButton onUploaded={refreshAll} />

      <input
        type="text"
        placeholder={activeTab === 'Needs Review' ? 'Filter files...' : 'Filter tickers...'}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />

      {activeTab === 'Needs Review' ? (
        <>
          {assignMessage && <p>{assignMessage}</p>}
          <ul>
            {filteredItems.map((filename) => (
              <li key={filename}>
                {filename}{' '}
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
                    <button onClick={() => handleDeleteFile(filename)}>Yes, delete</button>{' '}
                    <button onClick={() => setConfirmDeleteFile(null)}>Cancel</button>
                  </span>
                ) : (
                  <button onClick={() => setConfirmDeleteFile(filename)}>Delete</button>
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
                    {confirmState[filename].category_folder && (
                      // The candidate also carries a suffix matching a
                      // theme folder — offer that as its own choice
                      // instead of only ever framing this as a typo.
                      <button onClick={() => handleAssign(filename, confirmState[filename].requested_ticker, { force: true })}>
                        File into {confirmState[filename].category_folder}
                      </button>
                    )}{' '}
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
      ) : (
        <ul>
          {filteredItems.map((ticker) => (
            <li key={ticker} onClick={() => onSelectTicker({ ticker, status: activeTab.toLowerCase() })}>
              {ticker}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default TickerList

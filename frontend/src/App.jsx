import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import TickerList from './components/TickerList.jsx'
import TickerDetail from './components/TickerDetail.jsx'
import FileSearch from './components/FileSearch.jsx'
import CategorySuffixWarning from './components/CategorySuffixWarning.jsx'
import Sidebar from './components/Sidebar.jsx'
import Login from './components/Login.jsx'
import { api } from './api.js'
import { SLUGS_TO_TAB, TAB_SLUGS } from './tabSlugs.js'

// The list screen: a tab slug from the URL (defaulting to Active for "/"
// or anything unrecognized) drives TickerList, and picking a ticker pushes
// a new URL rather than flipping local state — that's what makes the
// browser's own back/forward buttons work for free, no extra code needed
// beyond just using real routes. Tab navigation itself now lives in
// Sidebar (rendered once at the App level), not here.
function ListView({ onDataChanged, refreshTrigger }) {
  const { tabSlug } = useParams()
  const activeTab = SLUGS_TO_TAB[tabSlug]
  const navigate = useNavigate()

  // An unrecognized slug (a hand-typed or stale URL) redirects to "/"
  // rather than silently showing Active while leaving the bad URL in the
  // address bar.
  if (tabSlug && !activeTab) {
    return <Navigate to="/" replace />
  }

  return (
    <div className="list-view">
      <FileSearch onSelectTicker={({ ticker, status }) => navigate(`/ticker/${status}/${encodeURIComponent(ticker)}`)} />
      <TickerList
        onSelectTicker={({ ticker, status }) => navigate(`/ticker/${status}/${encodeURIComponent(ticker)}`)}
        activeTab={activeTab ?? 'Active'}
        onDataChanged={onDataChanged}
        refreshTrigger={refreshTrigger}
      />
    </div>
  )
}

// The detail screen: `status`/`ticker` come straight from the URL, so a
// direct load or a refresh lands on the right ticker without needing to
// look anything up first. A trailing `*` splat captures an optional
// subfolder path (e.g. "Old Models" or "Old Models/Q1") — clicking into a
// ticker's own subfolder now navigates to a real nested URL instead of
// expanding inline, same as clicking a ticker itself does, so it gets a
// real back/forward-able history entry and a shareable/refreshable link
// too. Each path segment is encoded independently (mirroring how the
// ticker name itself is encoded below) since a folder name can contain
// spaces or other characters that aren't safe raw in a URL segment.
//
// "Back" deliberately navigates up one level (to the parent subfolder, or
// the owning tab once at the ticker's own root) rather than relying on
// browser history (`navigate(-1)`) — arriving here via a pasted link or a
// refresh has no in-app history to go back to, so this keeps the button's
// behavior predictable regardless of how the page was reached. The real
// browser back/forward buttons still work separately, automatically,
// since every level here is a real URL change either way.
function DetailView({ onDataChanged }) {
  const { status, ticker, '*': rawSubfolder } = useParams()
  const navigate = useNavigate()
  const tab = Object.keys(TAB_SLUGS).find((t) => TAB_SLUGS[t] === status) ?? 'Active'
  const subfolderPath = rawSubfolder ? rawSubfolder.split('/').map(decodeURIComponent).join('/') : ''

  function navigateToSubfolder(path) {
    const suffix = path ? `/${path.split('/').map(encodeURIComponent).join('/')}` : ''
    navigate(`/ticker/${status}/${encodeURIComponent(ticker)}${suffix}`)
  }

  function handleBack() {
    if (!subfolderPath) {
      navigate(tab === 'Active' ? '/' : `/${TAB_SLUGS[tab]}`)
      return
    }
    navigateToSubfolder(subfolderPath.split('/').slice(0, -1).join('/'))
  }

  return (
    <TickerDetail
      ticker={ticker}
      status={status}
      subfolderPath={subfolderPath}
      onBack={handleBack}
      onNavigateToSubfolder={navigateToSubfolder}
      onDataChanged={onDataChanged}
    />
  )
}

function App() {
  // Bumped by TickerList/TickerDetail whenever something happens that
  // could introduce a new suffix collision (a ticker gets created, moved,
  // etc.) — CategorySuffixWarning re-checks immediately when this
  // changes, rather than waiting for its own periodic timer. A ticker
  // whose name ends in a "(.SUFFIX)" marker (like the empty-folder-drop
  // case) can collide the moment it's created, so the banner needs to be
  // able to catch that right away, not up to a minute later.
  const [dataChangeCount, setDataChangeCount] = useState(0)
  const bumpDataChangeCount = () => setDataChangeCount((n) => n + 1)
  // Bumped whenever the sidebar's "+ New" upload completes. The upload
  // control now lives in Sidebar (rendered once, outside the routed
  // content) rather than inside TickerList like it used to — so it can no
  // longer call TickerList's own refreshAll directly the way it did when
  // it was a child of it. `null` until the first upload, same reasoning
  // as everywhere else in this app that needs to tell "never happened yet"
  // apart from "happened, with this exact count."
  const [listRefreshTrigger, setListRefreshTrigger] = useState(null)

  // undefined = still checking on first load, null = confirmed signed
  // out, an object ({name, full_name, email}) = signed in. Distinct from
  // every other "hasn't loaded yet" pattern in this app (which use null
  // for that) because here `null` is a real, meaningful outcome (signed
  // out) that has to be told apart from "don't know yet" — showing the
  // sign-in screen a frame too early, before the very first /auth/me
  // response lands, would flash it even for someone who's actually signed
  // in.
  const [user, setUser] = useState(undefined)

  useEffect(() => {
    api
      .getCurrentUser()
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  if (user === undefined) {
    return <div className="app-loading">Loading…</div>
  }

  if (user === null) {
    return <Login />
  }

  return (
    <div className="app">
      <Sidebar user={user} onLoggedOut={() => setUser(null)} onUploaded={() => setListRefreshTrigger((n) => (n ?? 0) + 1)} />
      <div className="app__main">
        <CategorySuffixWarning recheckTrigger={dataChangeCount} />
        <Routes>
          <Route path="/" element={<ListView onDataChanged={bumpDataChangeCount} refreshTrigger={listRefreshTrigger} />} />
          <Route path="/:tabSlug" element={<ListView onDataChanged={bumpDataChangeCount} refreshTrigger={listRefreshTrigger} />} />
          <Route path="/ticker/:status/:ticker" element={<DetailView onDataChanged={bumpDataChangeCount} />} />
          <Route path="/ticker/:status/:ticker/*" element={<DetailView onDataChanged={bumpDataChangeCount} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  )
}

export default App

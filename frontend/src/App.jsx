import { useState } from 'react'
import TickerList from './components/TickerList.jsx'
import TickerDetail from './components/TickerDetail.jsx'
import FileSearch from './components/FileSearch.jsx'
import CategorySuffixWarning from './components/CategorySuffixWarning.jsx'

// Top-level page. Holds the one piece of state that decides what's on
// screen: either the ticker list (+ search box), or a single ticker's
// detail view. There's no routing library — this app is small enough that
// "one flag decides which of two screens to show" is all it needs.
function App() {
  // null = show the list view. { ticker, status } = show that ticker's
  // detail view. Both TickerList (via a click) and FileSearch (via a
  // search result) can set this — either one is "pick a ticker to look at".
  const [selected, setSelected] = useState(null)
  // Which tab (Active/Inactive/Historicals/Needs Review) was showing,
  // lifted up here rather than left as TickerList's own state — TickerList
  // unmounts entirely while a ticker is selected (see below), so anything
  // that needs to survive a round trip into a ticker's detail view and
  // back to the same tab has to live above that unmount, in App.
  const [activeTab, setActiveTab] = useState('Active')
  // Bumped by TickerList/TickerDetail whenever something happens that
  // could introduce a new suffix collision (a ticker gets created, moved,
  // etc.) — CategorySuffixWarning re-checks immediately when this
  // changes, rather than waiting for its own periodic timer. A ticker
  // whose name ends in a "(.SUFFIX)" marker (like the empty-folder-drop
  // case) can collide the moment it's created, so the banner needs to be
  // able to catch that right away, not up to a minute later.
  const [dataChangeCount, setDataChangeCount] = useState(0)
  const bumpDataChangeCount = () => setDataChangeCount((n) => n + 1)

  return (
    <div className="app">
      <h1>Research Tool</h1>
      <CategorySuffixWarning recheckTrigger={dataChangeCount} />
      {selected ? (
        <TickerDetail
          ticker={selected.ticker}
          status={selected.status}
          onBack={() => setSelected(null)}
          onDataChanged={bumpDataChangeCount}
        />
      ) : (
        <>
          <FileSearch onSelectTicker={setSelected} />
          <TickerList
            onSelectTicker={setSelected}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            onDataChanged={bumpDataChangeCount}
          />
        </>
      )}
    </div>
  )
}

export default App

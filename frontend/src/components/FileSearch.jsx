import { useState } from 'react'
import { api } from '../api.js'

// Real cross-ticker file search — separate from the plain name-filter box
// in TickerList (which only narrows the tickers already on screen). This
// searches actual filenames across every ticker via the backend's
// /files/search endpoint, and shows results you can click straight into.
function FileSearch({ onSelectTicker }) {
  const [query, setQuery] = useState('')
  // null = no search run yet (hide results entirely). [] = search ran,
  // found nothing (show "no matches"). This distinction is why we can't
  // just use a plain boolean or check results.length.
  const [results, setResults] = useState(null)
  // The query that was actually searched, frozen at search time — kept
  // separate from `query` (which updates on every keystroke) so the "no
  // matches" message below stays accurate even if you keep typing
  // afterward, instead of silently rewriting itself to whatever's
  // currently in the box despite no new search having run.
  const [searchedQuery, setSearchedQuery] = useState('')

  async function handleSearch(event) {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) {
      setResults(null)
      return
    }
    setSearchedQuery(trimmed)
    try {
      const matches = await api.searchFiles(trimmed)
      setResults(matches)
    } catch (err) {
      setResults([])
    }
  }

  // Clears the query and results together, so backing out doesn't also
  // require hitting Enter on an empty box.
  function handleClear() {
    setQuery('')
    setResults(null)
  }

  return (
    <div className="file-search">
      <form onSubmit={handleSearch}>
        <span className="file-search__input-wrap">
          <input
            type="text"
            placeholder="Search files across all tickers..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="file-search__clear"
              onClick={handleClear}
              aria-label="Clear search"
            >
              &times;
            </button>
          )}
        </span>
        <button type="submit">Search</button>
      </form>

      {results && (
        <div className="search-results">
          {results.length === 0 ? (
            <p>No files matched "{searchedQuery}".</p>
          ) : (
            <ul>
              {results.map((result, index) => (
                <li key={`${result.status}-${result.ticker}-${result.filename}-${index}`}>
                  {result.filename} —{' '}
                  {result.ticker ? (
                    // Clicking jumps straight to that ticker's detail
                    // page, passing along the status the search already
                    // told us (active/inactive) so TickerDetail doesn't
                    // need to look it up again.
                    <button onClick={() => onSelectTicker({ ticker: result.ticker, status: result.status })}>
                      {result.ticker} ({result.status})
                    </button>
                  ) : (
                    // Needs Review files aren't filed under any ticker,
                    // so there's nowhere to click through to — the user
                    // has to go assign it a ticker from the Needs Review
                    // tab instead.
                    <em>Needs Review</em>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

export default FileSearch

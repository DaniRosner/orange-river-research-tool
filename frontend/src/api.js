// Thin wrapper around the backend's HTTP API. Every function here just
// builds a request and returns parsed JSON — no business logic lives in
// this file; that all happens on the backend (see backend/app/routers/).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// Shared fetch helper: builds the full URL, throws on any non-2xx response
// (so callers can just `await` and `catch`), and parses the JSON body.
async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options)
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

export const api = {
  getActiveTickers: () => request('/tickers/active'),
  getInactiveTickers: () => request('/tickers/inactive'),
  getHistoricalsTickers: () => request('/tickers/historicals'),
  getNeedsReview: () => request('/tickers/needs-review'),
  // {suffix: [folderPath, folderPath, ...]} for any suffix claimed by more
  // than one theme folder — normally empty. See
  // category_routing.find_duplicate_category_suffixes() for why this
  // matters enough to surface as a warning.
  getCategorySuffixWarnings: () => request('/tickers/category-suffix-warnings'),
  getFilesForTicker: (ticker) => request(`/files/ticker/${encodeURIComponent(ticker)}`),
  searchFiles: (query) => request(`/files/search?q=${encodeURIComponent(query)}`),

  // Uploads one file. `overrideTicker`/`targetStatus`/`onDuplicate` are
  // only set on a *resubmission* — the backend may respond asking for one
  // of these before it will actually file the upload (see
  // UploadButton.jsx for how the confirmation dialogs drive this):
  //   - overrideTicker: "yes, file this under ticker X" (used to accept a
  //     suggestion, or together with targetStatus to confirm a brand-new
  //     ticker).
  //   - targetStatus: "active" or "inactive" — only meaningful together
  //     with overrideTicker, for a ticker that doesn't exist yet.
  //   - onDuplicate: "replace" or "keep_both" — how to handle a filename
  //     that already exists where this file is about to be filed.
  //   - relativePath: places the file inside a subfolder of the ticker
  //     folder (e.g. "Old Models") instead of directly inside it — used
  //     when uploading a whole dropped folder that has subfolders.
  //   - confirmCategory: resubmits a suffix-typo `confirm_needed` response
  //     that included a `category_folder`, choosing to file into that
  //     theme folder instead of correcting the typo or creating a new
  //     ticker.
  uploadFile: (file, { overrideTicker, targetStatus, onDuplicate, relativePath, confirmCategory } = {}) => {
    const formData = new FormData()
    formData.append('file', file)
    if (overrideTicker) {
      formData.append('override_ticker', overrideTicker)
    }
    if (targetStatus) {
      formData.append('target_status', targetStatus)
    }
    if (onDuplicate) {
      formData.append('on_duplicate', onDuplicate)
    }
    if (relativePath) {
      formData.append('relative_path', relativePath)
    }
    if (confirmCategory) {
      formData.append('confirm_category', 'true')
    }
    return request('/files/upload', { method: 'POST', body: formData })
  },

  // Resolves a candidate ticker name (e.g. the name of a folder someone
  // just dragged in) against real tickers, without uploading or moving
  // anything — see backend/app/services/ticker_registry.py's
  // resolve_ticker() for the possible `kind`s returned.
  resolveTicker: (name) => request(`/tickers/resolve?name=${encodeURIComponent(name)}`),

  // Creates an empty ticker folder — no files. Used when a dropped folder
  // turns out to have nothing inside it, so the resolved ticker still
  // gets created rather than the drag being a silent no-op. A no-op
  // itself if the ticker already exists.
  createTicker: (ticker, targetStatus) =>
    request(`/tickers/create?ticker=${encodeURIComponent(ticker)}&target_status=${targetStatus}`, {
      method: 'POST',
    }),

  // Moves a whole ticker folder between Active and Inactive in Dropbox.
  moveTicker: (ticker, targetStatus) =>
    request(`/tickers/${encodeURIComponent(ticker)}/move?target_status=${targetStatus}`, {
      method: 'POST',
    }),

  // Renames a ticker or theme folder in place (same status, different
  // name) — e.g. resolving a suffix collision without leaving the app.
  renameTicker: (ticker, newName) =>
    request(`/tickers/${encodeURIComponent(ticker)}/rename?new_name=${encodeURIComponent(newName)}`, {
      method: 'POST',
    }),

  // Manually files a Needs Review item under a ticker. `force=true` skips
  // the backend's typo-suggestion check (used after the user has already
  // seen and dismissed/confirmed past a "did you mean...?" prompt).
  // `targetStatus` ("active"/"inactive") is only needed when confirming a
  // brand-new ticker that doesn't exist yet. `skipCategory` is the
  // opposite case — used when the user explicitly chose "create a new
  // ticker anyway" over a theme-folder option a suffix-typo confirmation
  // offered, so a suffix match can't silently steer that choice back into
  // the theme folder instead.
  assignNeedsReviewFile: (filename, ticker, { force = false, targetStatus, skipCategory = false } = {}) => {
    const params = new URLSearchParams({ ticker })
    if (force) params.set('force', 'true')
    if (targetStatus) params.set('target_status', targetStatus)
    if (skipCategory) params.set('skip_category', 'true')
    return request(`/files/needs-review/${encodeURIComponent(filename)}/assign?${params}`, { method: 'POST' })
  },

  // Permanently deletes things. All three are real, immediate deletes —
  // components calling these are expected to have already confirmed with
  // the user (see the inline confirm dialogs in TickerDetail.jsx and
  // TickerList.jsx) before ever reaching these calls.
  deleteTickerFile: (ticker, filename, relativePath) => {
    const query = relativePath ? `?relative_path=${encodeURIComponent(relativePath)}` : ''
    return request(`/files/ticker/${encodeURIComponent(ticker)}/${encodeURIComponent(filename)}${query}`, {
      method: 'DELETE',
    })
  },
  deleteNeedsReviewFile: (filename) =>
    request(`/files/needs-review/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  deleteTicker: (ticker) => request(`/tickers/${encodeURIComponent(ticker)}`, { method: 'DELETE' }),
}

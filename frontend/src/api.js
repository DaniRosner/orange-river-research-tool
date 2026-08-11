// Thin wrapper around the backend's HTTP API. Every function here just
// builds a request and returns parsed JSON — no business logic lives in
// this file; that all happens on the backend (see backend/app/routers/).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// Shared fetch helper: builds the full URL, throws on any non-2xx response
// (so callers can just `await` and `catch`), and parses the JSON body.
// `credentials: 'include'` is what makes the session cookie set by
// /auth/callback actually go out on every one of these — without it, the
// backend would see every request as signed-out regardless of whether the
// browser actually has a valid session cookie. The thrown error carries
// `.status` (not just a message) so callers — specifically App.jsx —
// can tell "not signed in" (401) apart from a real failure.
async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, credentials: 'include' })
  if (!response.ok) {
    const error = new Error(`API request failed: ${response.status} ${response.statusText}`)
    error.status = response.status
    throw error
  }
  return response.json()
}

export const api = {
  // The current signed-in user ({name, full_name, email}), or a 401 (see
  // `.status` on the thrown error) if no one's signed in.
  getCurrentUser: () => request('/auth/me'),
  // Full-page redirect, not a fetch — signing in is a real handoff to
  // Dropbox's own page, not something that can happen over XHR/fetch.
  goToLogin: () => {
    window.location.href = `${API_BASE_URL}/auth/login`
  },
  logout: () => request('/auth/logout', { method: 'POST' }),

  getActiveTickers: () => request('/tickers/active'),
  getInactiveTickers: () => request('/tickers/inactive'),
  getHistoricalsTickers: () => request('/tickers/historicals'),
  getNeedsReview: () => request('/tickers/needs-review'),
  // {suffix: [folderPath, folderPath, ...]} for any suffix claimed by more
  // than one theme folder — normally empty. See
  // category_routing.find_duplicate_category_suffixes() for why this
  // matters enough to surface as a warning.
  getCategorySuffixWarnings: () => request('/tickers/category-suffix-warnings'),
  // {tickerName: {action, user_name, user_email, timestamp, detail}} for
  // every ticker with logged activity (created/renamed/moved/deleted
  // through this app) — see backend/app/services/activity_log.py.
  getTickerActivity: () => request('/tickers/activity'),
  // {files: [...], folders: [...]} — `folders` includes empty subfolders
  // (e.g. from createSubfolder below) that have no file to be inferred
  // from otherwise.
  getFilesForTicker: (ticker) => request(`/files/ticker/${encodeURIComponent(ticker)}`),
  searchFiles: (query) => request(`/files/search?q=${encodeURIComponent(query)}`),

  // Creates an empty subfolder inside a ticker — at its root, or nested
  // inside an existing subfolder if `relativePath` is given (e.g. from
  // wherever the user is currently browsing — see TickerDetail.jsx's
  // subfolderPath).
  createSubfolder: (ticker, name, relativePath) => {
    const params = new URLSearchParams({ name })
    if (relativePath) params.set('relative_path', relativePath)
    return request(`/files/ticker/${encodeURIComponent(ticker)}/folder?${params}`, { method: 'POST' })
  },

  // Deletes a subfolder — and everything inside it — from a ticker.
  // `relativePath` is the folder's own full path relative to the ticker
  // root (e.g. "Old Filings", or "Old Filings/2023"), not just its name.
  deleteSubfolder: (ticker, relativePath) => {
    const params = new URLSearchParams({ relative_path: relativePath })
    return request(`/files/ticker/${encodeURIComponent(ticker)}/folder?${params}`, { method: 'DELETE' })
  },

  // Not a fetch — a plain URL for an <img src>, so the browser handles the
  // request/caching itself. `ticker: null` means the file is still in
  // Needs Review (a different, ticker-less endpoint). The session cookie
  // still goes out on this request same as any other — it's SameSite=None
  // (see backend/app/main.py), which covers image loads too, not just
  // fetch() calls.
  thumbnailUrl: (ticker, filename, relativePath) => {
    if (ticker == null) {
      return `${API_BASE_URL}/files/needs-review/${encodeURIComponent(filename)}/thumbnail`
    }
    const query = relativePath ? `?relative_path=${encodeURIComponent(relativePath)}` : ''
    return `${API_BASE_URL}/files/ticker/${encodeURIComponent(ticker)}/${encodeURIComponent(filename)}/thumbnail${query}`
  },

  // Resolves to {url}, a Dropbox web link that opens this file in
  // Dropbox's own preview UI (creating a shared link for it first if one
  // doesn't already exist). `ticker: null` means the file is still in
  // Needs Review.
  getOpenLink: (ticker, filename, relativePath) => {
    if (ticker == null) {
      return request(`/files/needs-review/${encodeURIComponent(filename)}/open-link`)
    }
    const query = relativePath ? `?relative_path=${encodeURIComponent(relativePath)}` : ''
    return request(`/files/ticker/${encodeURIComponent(ticker)}/${encodeURIComponent(filename)}/open-link${query}`)
  },

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
  //     when uploading a whole dropped folder that has subfolders, or
  //     answering a `choose_subfolder` pause for a single file. "" is a
  //     real, deliberate answer here ("file it at the root"), not "no
  //     answer" — sent explicitly rather than omitted, so the backend can
  //     tell the two apart (see files.py's `_maybe_ask_subfolder`).
  uploadFile: (file, { overrideTicker, targetStatus, onDuplicate, relativePath } = {}) => {
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
    if (relativePath !== undefined) {
      formData.append('relative_path', relativePath)
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

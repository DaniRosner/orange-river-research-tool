import { useRef, useState } from 'react'
import { api } from '../api.js'
import { useAutoDismiss } from '../useAutoDismiss.js'

// ---------- Folder-reading helpers ----------
// Dropping a folder onto the browser doesn't hand you its files directly
// — you get filesystem "entries" that have to be walked recursively via
// this API (https://developer.mozilla.org/docs/Web/API/FileSystemEntry).
// These turn that into a flat list of { file, relativePath } pairs, where
// relativePath is the subfolder path *inside* the dropped folder (e.g.
// "Old Models" for a nested file, "" for one directly in the folder's
// root) — never including the dropped folder's own name, since that
// becomes the ticker, not part of any file's path.

// Same junk files the backend already hides from every listing (see
// dropbox_client.py's _JUNK_NAMES/_JUNK_SUFFIXES) — filtered here too so a
// real folder dragged straight out of Finder (which silently creates
// ".DS_Store" the moment you view it) never even gets queued for upload,
// rather than uploading it pointlessly and then hiding it everywhere.
const JUNK_NAMES = new Set(['.ds_store'])
const JUNK_SUFFIXES = ['.lnk']

// Must match ROOT_RELATIVE_PATH in backend/app/routers/files.py — see its
// comment there for why a real empty string can't be used for this.
const ROOT_RELATIVE_PATH = '.'

function isJunkFile(name) {
  const lower = name.toLowerCase()
  return JUNK_NAMES.has(lower) || JUNK_SUFFIXES.some((suffix) => lower.endsWith(suffix))
}

function readAllEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = []
    function readBatch() {
      // readEntries only returns a batch at a time for large folders —
      // an empty batch means there's nothing left.
      reader.readEntries((entries) => {
        if (entries.length === 0) {
          resolve(all)
        } else {
          all.push(...entries)
          readBatch()
        }
      }, reject)
    }
    readBatch()
  })
}

async function readEntry(entry, parentPath, results) {
  if (entry.isFile) {
    // Skip junk files (".DS_Store" etc.) before ever reading their
    // content — no point even opening them just to throw the result away.
    if (isJunkFile(entry.name)) return
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
    // Some drag sources hand back phantom entries with no real content —
    // e.g. dragging a file out of the browser's own native "Open File"
    // dialog rather than from Finder directly can produce one of these.
    // Silently skip rather than queuing a fake upload for it — with no
    // name, there's nothing meaningful the backend could even do with it.
    if (!file || !file.name) return
    results.push({ file, relativePath: parentPath })
  } else if (entry.isDirectory) {
    const childPath = parentPath ? `${parentPath}/${entry.name}` : entry.name
    const children = await readAllEntries(entry.createReader())
    await Promise.all(children.map((child) => readEntry(child, childPath, results)))
  }
}

async function readDroppedFolder(folderEntry) {
  const results = []
  // Start from the folder's *children*, not the folder itself — so its
  // own name never ends up baked into any file's relativePath.
  const children = await readAllEntries(folderEntry.createReader())
  await Promise.all(children.map((child) => readEntry(child, '', results)))
  return results
}

// Reads a drop event's contents into an ordered list of "batches" — each
// top-level folder becomes its own batch (its ticker resolved once, up
// front, same as a single folder drop always worked), and each top-level
// loose file becomes its own single-file batch. Dropping several folders,
// several loose files, or a mix of both now all queue and run one after
// another instead of only the first folder found winning and everything
// else being silently dropped — see startBatchQueue()/advanceBatchQueue().
async function readDroppedItems(dataTransfer) {
  const entries = Array.from(dataTransfer.items)
    .map((item) => item.webkitGetAsEntry())
    .filter(Boolean)

  const batches = []
  for (const entry of entries) {
    // A phantom entry with no name/content is the same "not really
    // usable" signal whether it's a folder or a file — skip it rather
    // than queuing a batch with nothing to actually upload.
    if (entry.isDirectory && entry.name) {
      const items = await readDroppedFolder(entry)
      batches.push({ type: 'folder', folderName: entry.name, items })
    } else if (entry.isFile) {
      const items = []
      await readEntry(entry, '', items)
      if (items.length > 0) {
        batches.push({ type: 'file', file: items[0].file, relativePath: items[0].relativePath })
      }
    }
  }
  return batches
}

// Handles the upload control: click "Upload" to reveal a drop zone (for
// loose files or a whole folder), click again inside it to browse for
// loose files, or drag either kind onto it directly. Dropping/picking a
// *folder* resolves its name to a ticker once, up front, then uploads
// everything inside it under that ticker, preserving any subfolder
// structure. Loose files instead go through the normal per-file sorting
// logic (see files.py's upload endpoint) one at a time. Dropping several
// folders, several loose files, or a mix of both queues them as separate
// batches and runs them one after another — see startBatchQueue().
//
// Three kinds of "the backend needs an answer before it'll file this" can
// come back:
//   - "confirm_needed": the ticker isn't real, but looks like a typo of
//     one that is — show suggestions (or "make a new ticker").
//   - "new_ticker_status": doesn't match anything, but looks like (or, for
//     a dropped folder, simply *is*) a genuinely new ticker — ask whether
//     it's Active or Inactive rather than assuming.
//   - "duplicate_needs_confirmation": a file with this name already
//     exists where this one's about to go — replace it, or keep both?
// Whichever one it is, the batch pauses until the user answers — on the
// one file it's about, or (for a folder) before any of its files are
// even queued.
function UploadButton({ onUploaded }) {
  const fileInputRef = useRef(null)

  // Whether the drop zone panel is showing at all.
  const [isOpen, setIsOpen] = useState(false)
  // Whether something is currently being dragged over the drop zone.
  const [isDragActive, setIsDragActive] = useState(false)

  // Whole batches (a folder, or a single loose file — see
  // readDroppedItems()) still waiting after the one currently running.
  // Distinct from `queue` below, which is the files *within* whichever
  // batch is currently running.
  //
  // A ref, deliberately NOT useState: advanceBatchQueue() is reached deep
  // inside an async chain (submitUpload's promise, itself called from
  // handleFolder's own await) — every function in that chain closed over
  // whatever render was active when the drop started, so a useState value
  // read there would still see this render's original [] even after
  // setBatchQueue(rest) "updated" it, since that update only affects
  // *future* renders' closures, not the ones already mid-flight. This
  // was a real bug: two loose files dropped together silently uploaded
  // only the first, because advanceBatchQueue() read a stale empty queue
  // and finished the whole batch early. A ref's .current is one shared,
  // mutable value every closure reads live, with no such staleness.
  const batchQueueRef = useRef([])
  // Items ({file, relativePath}) still waiting after the one currently
  // showing (if any) is resolved.
  const [queue, setQueue] = useState([])
  // The item a confirmation dialog (if any) is currently about — null
  // while resolving a *folder* as a whole, before any of its items are
  // queued (see `pendingSubject` for what to display in that case).
  const [currentItem, setCurrentItem] = useState(null)
  // What to call the thing a dialog is currently asking about — a single
  // file's name, or a whole dropped folder's name (resolved before any
  // `currentItem` exists).
  const [pendingSubject, setPendingSubject] = useState('')
  // Whatever's already been decided so far for the current item/folder
  // (e.g. a ticker chosen in response to confirm_needed) — carried
  // forward if a *second* question (e.g. a duplicate) comes up too.
  const [pendingOptions, setPendingOptions] = useState({})
  // For a folder batch specifically: the ticker/status resolved ONCE for
  // the whole folder (see startFolderQueue), which every file in it needs
  // to keep using — set for the duration of the batch, cleared when the
  // batch finishes. Kept separate from `pendingOptions`, which is per-item
  // and shouldn't leak into the next file (e.g. one file's "replace the
  // duplicate" answer has nothing to do with the next file's upload).
  //
  // A ref, deliberately NOT useState — same reasoning as batchQueueRef
  // above. startFolderQueue calls setFolderBaseOptions(baseOptions) and
  // then, in the same synchronous call, submitUpload(first, ...) — but
  // submitUpload is a closure from the same render as startFolderQueue,
  // so a useState read there would still see this render's *original*
  // value (null), not the update that was just "set." This was a real
  // bug: every file in a dropped folder uploaded with relativePath
  // unset, flattening any subfolder structure (e.g. NEWFOLDERCO's own
  // "Old Models" folder) straight to the ticker's root every time.
  const folderBaseOptionsRef = useRef(null)
  // Whichever confirmation dialog is currently showing, plus the data
  // needed to render/resolve it. `kind` is 'ticker', 'new_ticker_status',
  // or 'duplicate'. `forItems`, when set, means resolving this continues
  // a folder batch rather than a single already-queued item.
  const [confirmData, setConfirmData] = useState(null)
  // Running log of "here's what happened to each file", auto-clearing a
  // few seconds after the last entry (see useAutoDismiss) — growing the
  // array restarts the clock, so it won't clear mid-batch.
  const [log, setLog] = useAutoDismiss([])
  // A suffix-collision warning (see category_routing.check_suffix_collision_for())
  // to show as an immediate pop-up, separate from the log — the upload or
  // ticker-creation it's attached to already succeeded, this is purely an
  // extra "heads up" that deserves to be hard to miss right when it
  // happens, not buried in a log line that auto-clears in a few seconds.
  // null = nothing to show. Stays up until explicitly dismissed.
  const [suffixWarning, setSuffixWarning] = useState(null)
  // Free-text entry for the "New folder" choice in the choose_subfolder
  // dialog — a name typed here doesn't need to exist yet; Dropbox creates
  // any missing parent folder automatically on upload (see
  // dropbox_client.upload_file()).
  const [newFolderName, setNewFolderName] = useState('')

  function logResult(text) {
    setLog((prev) => [...prev, text])
  }

  function finishBatch() {
    setCurrentItem(null)
    setPendingSubject('')
    setConfirmData(null)
    folderBaseOptionsRef.current = null
    onUploaded()
  }

  // Pulls the next item off the current batch's queue and uploads it, or
  // — if it's empty — the current batch (a folder, or the file-picker's
  // flat multi-file selection) is done, so move on to whatever's next in
  // batchQueue (see advanceBatchQueue). Continues with `folderBaseOptionsRef`
  // if this batch came from a folder (every file in it needs the same
  // resolved ticker/status — see startFolderQueue), otherwise `{}`, same
  // as always for an ordinary loose-file batch where each file is
  // resolved independently.
  function processNext(remaining) {
    if (remaining.length === 0) {
      advanceBatchQueue()
      return
    }
    const [next, ...rest] = remaining
    setQueue(rest)
    submitUpload(next, folderBaseOptionsRef.current || {}, rest)
  }

  // Uploads a single item ({file, relativePath}) with whatever options
  // have been decided so far. `rest` is "the items still waiting after
  // this one" — passed through so that if this call pauses on a
  // confirmation, we know what to continue with once it's answered.
  async function submitUpload(item, options, rest) {
    setCurrentItem(item)
    setPendingSubject(item.file.name)
    setPendingOptions(options)
    try {
      // A folder batch's items already carry the dropped folder's own
      // structure in `relativePath` (possibly "" for one sitting at that
      // folder's root) — that always wins and is sent as-is, except "" is
      // swapped for ROOT_RELATIVE_PATH (see api.js) since an empty string
      // form field can't survive as a deliberate "root" answer (FastAPI
      // coerces it back to "no answer" — indistinguishable from a loose
      // file that hasn't answered choose_subfolder yet). Loose files
      // instead rely purely on `options.relativePath`, set only once the
      // user has actually answered that question (see
      // handleSubfolderChoice) — `folderBaseOptionsRef` being set is
      // exactly how "this is a folder batch" is distinguished from "this
      // is a loose file" here.
      const apiOptions = folderBaseOptionsRef.current
        ? { ...options, relativePath: item.relativePath || ROOT_RELATIVE_PATH }
        : options
      const result = await api.uploadFile(item.file, apiOptions)

      if (result.status === 'confirm_needed') {
        setConfirmData({ kind: 'ticker', ...result })
        return
      }

      if (result.status === 'new_ticker_needs_status') {
        setConfirmData({ kind: 'new_ticker_status', ...result })
        return
      }

      if (result.status === 'duplicate_needs_confirmation') {
        setConfirmData({ kind: 'duplicate', ...result })
        return
      }

      if (result.status === 'ambiguous_mention') {
        setConfirmData({ kind: 'ambiguous_mention', ...result })
        return
      }

      if (result.status === 'choose_subfolder') {
        setConfirmData({ kind: 'choose_subfolder', ...result })
        return
      }

      setConfirmData(null)
      // The upload itself succeeded either way — this doesn't pause
      // anything, it's an additional pop-up on top of the normal result.
      if (result.suffix_warning) {
        setSuffixWarning(result.suffix_warning)
      }

      let message
      if (result.note) {
        // Content was byte-identical to a file already there (checked via
        // content hash before ever uploading) — nothing new was created.
        // See files.py's _resolve_upload. Plain, non-technical wording on
        // purpose: this is a routine "already got it," not an error.
        // `category_folder` (e.g. "Busted Biotechs (.BB)") is the real
        // folder name for a category-routed upload — `ticker` there is
        // just the suffix stripped off for display (e.g. "KBS"), not a
        // real folder, so it'd be misleading here.
        const where = result.category_folder || result.ticker
        message = where
          ? `"${item.file.name}" is already saved under ${where}.`
          : `"${item.file.name}" is already in Needs Review.`
      } else {
        const renamed = result.filename && result.filename !== item.file.name
        const displayName = renamed
          ? `"${item.file.name}" (saved as "${result.filename}" — name conflict)`
          : `"${item.file.name}"`
        if (result.status === 'needs_review') {
          message = `${displayName} didn't match a ticker — sent to Needs Review.`
        } else if (result.status === 'filed_category') {
          // Ticker ended with a registered category suffix (e.g. ".BB")
          // and got routed into that themed folder instead of becoming a
          // normal ticker — see category_routing.py.
          message = `${displayName} filed into ${result.category_folder}.`
        } else if (result.status === 'filed_mentioned') {
          // Didn't start with a ticker, but a real existing ticker was
          // found mentioned elsewhere in the name (e.g. "quarterly notes
          // on zbq.pdf") — see find_ticker_mentioned_anywhere().
          message = `${displayName} filed under ${result.ticker} (mentioned in the name, though not at the start).`
        } else {
          message = `${displayName} filed under ${result.ticker}.`
        }
      }
      logResult(message)
      processNext(rest)
    } catch (err) {
      logResult(`"${item.file.name}" failed: ${err.message}`)
      processNext(rest)
    }
  }

  // Kicks off a plain (non-folder) batch of items — each one goes through
  // the normal per-file sorting logic independently.
  function startQueue(items) {
    setLog([])
    if (items.length === 0) return
    const [first, ...rest] = items
    setQueue(rest)
    submitUpload(first, {}, rest)
  }

  // Kicks off a drop's worth of batches (folders and/or loose files, in
  // the order they were dragged — see readDroppedItems()). Each batch
  // runs all the way to completion, including any confirmation dialogs it
  // needs, before the next one starts (see advanceBatchQueue) — so
  // dropping three folders means answering up to three "which status?"
  // questions in turn, not all at once.
  function startBatchQueue(batches) {
    setLog([])
    if (batches.length === 0) return
    const [first, ...rest] = batches
    batchQueueRef.current = rest
    runBatch(first)
  }

  // Runs one batch: a whole folder (resolved once via handleFolder, same
  // as a lone dropped folder always worked) or a single loose file (the
  // normal per-file submitUpload path). Explicitly resets
  // folderBaseOptionsRef/queue first — without this, a loose-file batch
  // running right after a folder batch would incorrectly inherit the
  // previous folder's resolved ticker and its relativePath-forcing (see
  // submitUpload).
  function runBatch(batch) {
    if (batch.type === 'folder') {
      handleFolder(batch.folderName, batch.items)
    } else {
      folderBaseOptionsRef.current = null
      setQueue([])
      submitUpload({ file: batch.file, relativePath: batch.relativePath }, {}, [])
    }
  }

  // Called whenever a batch (a whole folder, or a single loose file) has
  // completely finished — every file inside it uploaded (or failed) and
  // any confirmation dialogs along the way resolved. Moves on to the next
  // queued batch, or finishes the whole drop if that was the last one.
  function advanceBatchQueue() {
    if (batchQueueRef.current.length === 0) {
      finishBatch()
      return
    }
    const [next, ...rest] = batchQueueRef.current
    batchQueueRef.current = rest
    runBatch(next)
  }

  // Starts uploading a folder's contents once its ticker is resolved
  // (matched, or confirmed by the user) — `baseOptions` already has
  // `overrideTicker` (and `targetStatus`, for a brand-new ticker) set, so
  // every file in the folder skips per-file ticker parsing entirely.
  function startFolderQueue(items, baseOptions) {
    if (items.length === 0) {
      // Nothing to upload, but if this resolved to a genuinely new ticker
      // (targetStatus only ever gets set for that case — an already-real
      // ticker never needs one), still create the empty folder rather
      // than silently doing nothing. Dragging an already-real ticker's
      // folder with nothing in it stays a true no-op, since there's
      // nothing to create.
      if (baseOptions.overrideTicker && baseOptions.targetStatus) {
        api
          .createTicker(baseOptions.overrideTicker, baseOptions.targetStatus)
          .then((result) => {
            logResult(`Created an empty ticker folder for "${baseOptions.overrideTicker}" — the dropped folder had no files in it.`)
            if (result.suffix_warning) setSuffixWarning(result.suffix_warning)
          })
          .catch((err) => logResult(`Couldn't create "${baseOptions.overrideTicker}": ${err.message}`))
          .finally(advanceBatchQueue)
      } else {
        logResult('That folder was empty — nothing to upload.')
        advanceBatchQueue()
      }
      return
    }
    folderBaseOptionsRef.current = baseOptions
    const [first, ...rest] = items
    setQueue(rest)
    submitUpload(first, baseOptions, rest)
  }

  // Resolves a dropped/picked folder's name to a ticker before uploading
  // anything inside it — one question for the whole folder, not one per
  // file inside it. Called once per folder batch (see runBatch) — doesn't
  // clear the log itself since that'd wipe out earlier batches' results
  // when this is the 2nd+ folder in a multi-folder drop; startBatchQueue
  // clears it once, up front, for the whole drop instead.
  async function handleFolder(folderName, items) {
    setPendingSubject(folderName)
    setCurrentItem(null)
    setPendingOptions({})
    try {
      const resolution = await api.resolveTicker(folderName)

      if (resolution.kind === 'matched') {
        startFolderQueue(items, { overrideTicker: resolution.ticker })
      } else if (resolution.kind === 'confirm_needed') {
        setConfirmData({
          kind: 'ticker',
          parsed_ticker: folderName,
          suggestions: resolution.suggestions,
          forItems: items,
        })
      } else {
        // "new_ticker_needs_status" or "not_a_ticker" — a dragged
        // folder's name is always a deliberate choice, like manually
        // typing a ticker, so even a lowercase/unmatched name still gets
        // asked rather than silently rejected the way an automatically
        // *parsed filename* would be.
        setConfirmData({ kind: 'new_ticker_status', parsed_ticker: folderName, forItems: items })
      }
    } catch (err) {
      logResult(`Couldn't resolve folder "${folderName}": ${err.message}`)
      advanceBatchQueue()
    }
  }

  function handleFileInputChange(event) {
    const files = Array.from(event.target.files).filter((file) => !isJunkFile(file.name))
    event.target.value = ''
    if (files.length === 0) return
    // Collapse the picker now that a selection's been made — the log and
    // any confirmation dialogs below it stay visible regardless.
    setIsOpen(false)
    startQueue(files.map((file) => ({ file, relativePath: '' })))
  }

  async function handleDrop(event) {
    event.preventDefault()
    setIsDragActive(false)
    // A real drop happened, so close the picker regardless of whether we
    // can actually read anything useful out of it below — e.g. dragging
    // a file out of the native "Open File" dialog itself (rather than
    // using its own Open button) doesn't carry readable file data the
    // normal way, and previously left the panel stuck open with nothing
    // visibly happening.
    setIsOpen(false)
    try {
      const batches = await readDroppedItems(event.dataTransfer)
      if (batches.length === 0) {
        logResult("Couldn't read anything from that drop — try dragging directly from Finder instead.")
        return
      }
      startBatchQueue(batches)
    } catch (err) {
      logResult(`Couldn't read what was dropped: ${err.message}`)
    }
  }

  function handleDragOver(event) {
    event.preventDefault()
  }

  function handleDragEnter(event) {
    event.preventDefault()
    setIsDragActive(true)
  }

  function handleDragLeave(event) {
    event.preventDefault()
    setIsDragActive(false)
  }

  // Continues whatever a confirm dialog was resolving — a folder batch
  // that hadn't started queueing yet, or the single item already in
  // progress. If neither is actually available, something's gone wrong
  // with our own state tracking; rather than the dialog just silently not
  // responding to clicks (confusing — looks "stuck"), clear it and say so.
  //
  // Clears confirmData immediately, before the (async) submit call below —
  // otherwise the same dialog just sits there, visibly unchanged, for the
  // whole network round-trip after a click, which reads as unresponsive.
  // `dataAtClick` keeps the choice that was just clicked working correctly
  // even though state clears out from under it — setConfirmData(null)
  // doesn't affect the local `confirmData` this function already closed
  // over.
  function resumeAfterConfirm(options) {
    const dataAtClick = confirmData
    setConfirmData(null)
    if (dataAtClick.forItems) {
      startFolderQueue(dataAtClick.forItems, options)
    } else if (currentItem) {
      submitUpload(currentItem, options, queue)
    } else {
      logResult(`Something went wrong resuming "${pendingSubject}" — please try uploading it again.`)
    }
  }

  // User answered the "did you mean...?" ticker dialog.
  function handleTickerChoice(ticker) {
    resumeAfterConfirm({ ...pendingOptions, overrideTicker: ticker })
  }

  // User answered the "Active or Inactive?" dialog for a brand-new ticker.
  function handleNewTickerStatusChoice(targetStatus) {
    resumeAfterConfirm({ ...pendingOptions, overrideTicker: confirmData.parsed_ticker, targetStatus })
  }

  // User answered the "this file already exists" dialog. Only ever comes
  // up for an individual file (a folder-resolution question is always
  // ticker/status, never a duplicate), so `forItems` is never set here in
  // practice — resumeAfterConfirm's fallback still covers it defensively.
  function handleDuplicateChoice(onDuplicate) {
    resumeAfterConfirm({ ...pendingOptions, onDuplicate })
  }

  // User answered the "where in this folder?" dialog — an existing
  // subfolder's name, "" for the root, or a new name typed into "New
  // folder". Only ever comes up for a single already-queued item (see
  // _maybe_ask_subfolder's docstring for why a folder batch never reaches
  // this), so `forItems` is never set here in practice — resumeAfterConfirm's
  // fallback still covers it defensively, same as handleDuplicateChoice.
  function handleSubfolderChoice(relativePath) {
    setNewFolderName('')
    resumeAfterConfirm({ ...pendingOptions, relativePath })
  }

  // User backed out of whichever dialog is showing. Cancelling a whole
  // folder's ticker/status question skips just that folder and moves on
  // to the next queued batch (if any) — same idea as cancelling a single
  // file skipping just that file, not aborting every other file still
  // waiting in the queue.
  function handleCancelConfirm() {
    logResult(`"${pendingSubject}" upload cancelled.`)
    setNewFolderName('')
    if (confirmData.forItems) {
      advanceBatchQueue()
    } else {
      setConfirmData(null)
      processNext(queue)
    }
  }

  return (
    <div className="upload-control">
      <button className="upload-toggle" onClick={() => setIsOpen((open) => !open)}>
        Upload
      </button>

      <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleFileInputChange} />

      {/* Everything below floats out over the main content instead of
          being laid out inline — the toggle button lives in the sidebar
          now (see Sidebar.jsx), which is nowhere near wide enough for a
          dropzone or a multi-button confirm dialog. Always rendered (even
          empty) so it can be positioned relative to .upload-control
          without an extra conditional wrapper. */}
      <div className="upload-panel">
      {isOpen && (
        <div
          className={`dropzone${isDragActive ? ' is-active' : ''}`}
          role="button"
          tabIndex={0}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              fileInputRef.current.click()
            }
          }}
        >
          {isDragActive ? (
            'Drop to upload'
          ) : (
            <>
              Drag files or a whole folder here
              <br />
              <span className="dropzone__subtext">(or click to browse — files only, no folders)</span>
            </>
          )}
        </div>
      )}

      {log.length > 0 && (
        <ul className="upload-log">
          {log.map((entry, index) => (
            <li key={index}>{entry}</li>
          ))}
        </ul>
      )}

      {confirmData && confirmData.kind === 'ticker' && (
        <div className="confirm-dialog">
          {/* Same wording whether this came from a file or a dropped
              folder — only one confirmation is ever shown at a time (the
              queue pauses on it), so there's no ambiguity to resolve by
              also naming the file/folder here. */}
          <p>"{confirmData.parsed_ticker}" isn't a recognized ticker. Did you mean:</p>
          <ul>
            {confirmData.suggestions.map((suggestion) => (
              <li key={suggestion}>
                <button onClick={() => handleTickerChoice(suggestion)}>{suggestion}</button>
              </li>
            ))}
          </ul>
          <button onClick={() => handleTickerChoice(confirmData.parsed_ticker)}>
            No, create new ticker "{confirmData.parsed_ticker}"
          </button>
          <button onClick={handleCancelConfirm}>Cancel</button>
        </div>
      )}

      {confirmData && confirmData.kind === 'new_ticker_status' && (
        <div className="confirm-dialog">
          <p>
            There's no ticker named "{confirmData.parsed_ticker}" yet — create a new ticker folder for it. Which
            status?
          </p>
          <button onClick={() => handleNewTickerStatusChoice('active')}>Active</button>
          <button onClick={() => handleNewTickerStatusChoice('inactive')}>Inactive</button>
          <button onClick={() => handleNewTickerStatusChoice('historicals')}>Historicals</button>
          <button onClick={handleCancelConfirm}>Cancel</button>
        </div>
      )}

      {confirmData && confirmData.kind === 'duplicate' && (
        <div className="confirm-dialog">
          <p>
            A different file named "{confirmData.filename}" already exists
            {/* category_folder (e.g. "Busted Biotechs (.BB)") is the real
                folder name for a category-routed upload — `ticker` there
                is just the suffix stripped off for display elsewhere
                (e.g. "KBS"), not a real folder, so it'd be misleading
                here. Fall back to `ticker` for an ordinary ticker-folder
                duplicate, where it IS the real folder name. */}
            {confirmData.category_folder
              ? ` under ${confirmData.category_folder}`
              : confirmData.ticker
                ? ` under ${confirmData.ticker}`
                : ''}
            .
          </p>
          <button onClick={() => handleDuplicateChoice('replace')}>Replace it</button>
          <button onClick={() => handleDuplicateChoice('keep_both')}>Keep both</button>
          <button onClick={handleCancelConfirm}>Cancel</button>
        </div>
      )}

      {confirmData && confirmData.kind === 'ambiguous_mention' && (
        <div className="confirm-dialog">
          {/* Every candidate here was written in all-caps in the filename
              itself (see find_ambiguous_uppercase_mentions()) — a
              deliberate-looking reference to more than one real ticker,
              not a guess at what "ZBQ" or "ZPAX" might mean. */}
          <p>"{confirmData.filename}" mentions more than one real ticker — where should it go?</p>
          <ul>
            {confirmData.candidates.map((candidate) => (
              <li key={candidate}>
                <button onClick={() => handleTickerChoice(candidate)}>{candidate}</button>
              </li>
            ))}
          </ul>
          <button onClick={handleCancelConfirm}>Cancel</button>
        </div>
      )}

      {confirmData && confirmData.kind === 'choose_subfolder' && (
        <div className="confirm-dialog">
          <p>
            "{pendingSubject}" — where should this go in {confirmData.folder_label}?
          </p>
          <button onClick={() => handleSubfolderChoice(ROOT_RELATIVE_PATH)}>
            Directly in {confirmData.folder_label}
          </button>
          <ul>
            {confirmData.subfolders.map((name) => (
              <li key={name}>
                <button onClick={() => handleSubfolderChoice(name)}>{name}</button>
              </li>
            ))}
          </ul>
          <input
            type="text"
            placeholder="New folder name"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
          />
          <button
            disabled={!newFolderName.trim() || newFolderName.trim() === ROOT_RELATIVE_PATH}
            onClick={() => handleSubfolderChoice(newFolderName.trim())}
          >
            Create new folder
          </button>
          <button onClick={handleCancelConfirm}>Cancel</button>
        </div>
      )}
      </div>

      {suffixWarning && (
        <div className="suffix-warning-overlay">
          <div className="suffix-warning-modal">
            <p>
              <strong>Heads up:</strong> {suffixWarning}
            </p>
            <button onClick={() => setSuffixWarning(null)}>OK</button>
          </div>
        </div>
      )}
    </div>
  )
}

export default UploadButton

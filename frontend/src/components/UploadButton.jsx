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

// Reads a drop event's contents. If it contains a folder, only the first
// one found is used — dropping multiple folders, or a mix of folders and
// loose files, at once isn't supported. Keeps behavior predictable rather
// than guessing which folder should "win."
async function readDroppedItems(dataTransfer) {
  const entries = Array.from(dataTransfer.items)
    .map((item) => item.webkitGetAsEntry())
    .filter(Boolean)

  // A phantom directory entry with no name is the same "not really
  // usable" signal as a phantom file — ignore it rather than treating an
  // unnamed folder as something to resolve/upload.
  const folderEntry = entries.find((entry) => entry.isDirectory && entry.name)
  if (folderEntry) {
    const items = await readDroppedFolder(folderEntry)
    return { folderName: folderEntry.name, items }
  }

  const items = []
  await Promise.all(entries.filter((entry) => entry.isFile).map((entry) => readEntry(entry, '', items)))
  return { folderName: null, items }
}

// Handles the upload control: click "Upload" to reveal a drop zone (for
// loose files or a whole folder), click again inside it to browse for
// loose files, or drag either kind onto it directly. Dropping/picking a
// *folder* resolves its name to a ticker once, up front, then uploads
// everything inside it under that ticker, preserving any subfolder
// structure. Loose files instead go through the normal per-file sorting
// logic (see files.py's upload endpoint) one at a time.
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
  // to keep using — set for the duration of the batch, cleared in
  // finishBatch(). Kept separate from `pendingOptions`, which is per-item
  // and shouldn't leak into the next file (e.g. one file's "replace the
  // duplicate" answer has nothing to do with the next file's upload).
  const [folderBaseOptions, setFolderBaseOptions] = useState(null)
  // Whichever confirmation dialog is currently showing, plus the data
  // needed to render/resolve it. `kind` is 'ticker', 'new_ticker_status',
  // or 'duplicate'. `forItems`, when set, means resolving this continues
  // a folder batch rather than a single already-queued item.
  const [confirmData, setConfirmData] = useState(null)
  // Running log of "here's what happened to each file", auto-clearing a
  // few seconds after the last entry (see useAutoDismiss) — growing the
  // array restarts the clock, so it won't clear mid-batch.
  const [log, setLog] = useAutoDismiss([])

  function logResult(text) {
    setLog((prev) => [...prev, text])
  }

  function finishBatch() {
    setCurrentItem(null)
    setPendingSubject('')
    setConfirmData(null)
    setFolderBaseOptions(null)
    onUploaded()
  }

  // Pulls the next item off the queue and uploads it, or — if the queue
  // is empty — finishes the batch. Continues with `folderBaseOptions` if
  // this batch came from a folder (every file in it needs the same
  // resolved ticker/status — see startFolderQueue), otherwise `{}`, same
  // as always for an ordinary loose-file batch where each file is
  // resolved independently.
  function processNext(remaining) {
    if (remaining.length === 0) {
      finishBatch()
      return
    }
    const [next, ...rest] = remaining
    setQueue(rest)
    submitUpload(next, folderBaseOptions || {}, rest)
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
      const apiOptions = item.relativePath ? { ...options, relativePath: item.relativePath } : options
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

      setConfirmData(null)

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

  // Starts uploading a folder's contents once its ticker is resolved
  // (matched, or confirmed by the user) — `baseOptions` already has
  // `overrideTicker` (and `targetStatus`, for a brand-new ticker) set, so
  // every file in the folder skips per-file ticker parsing entirely.
  function startFolderQueue(items, baseOptions) {
    if (items.length === 0) {
      logResult('That folder was empty — nothing to upload.')
      finishBatch()
      return
    }
    setFolderBaseOptions(baseOptions)
    const [first, ...rest] = items
    setQueue(rest)
    submitUpload(first, baseOptions, rest)
  }

  // Resolves a dropped/picked folder's name to a ticker before uploading
  // anything inside it — one question for the whole folder, not one per
  // file inside it.
  async function handleFolder(folderName, items) {
    setLog([])
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
      finishBatch()
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
      const { folderName, items } = await readDroppedItems(event.dataTransfer)
      if (folderName) {
        handleFolder(folderName, items)
      } else if (items.length > 0) {
        startQueue(items)
      } else {
        logResult("Couldn't read anything from that drop — try dragging directly from Finder instead.")
      }
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
  function resumeAfterConfirm(options) {
    if (confirmData.forItems) {
      startFolderQueue(confirmData.forItems, options)
    } else if (currentItem) {
      submitUpload(currentItem, options, queue)
    } else {
      logResult(`Something went wrong resuming "${pendingSubject}" — please try uploading it again.`)
      setConfirmData(null)
    }
  }

  // User answered the "did you mean...?" ticker dialog.
  function handleTickerChoice(ticker) {
    resumeAfterConfirm({ ...pendingOptions, overrideTicker: ticker })
  }

  // User answered a suffix-typo dialog by choosing the theme folder it
  // also matched, instead of a typo suggestion or a brand-new ticker.
  function handleCategoryChoice() {
    resumeAfterConfirm({ ...pendingOptions, confirmCategory: true })
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

  // User backed out of whichever dialog is showing.
  function handleCancelConfirm() {
    logResult(`"${pendingSubject}" upload cancelled.`)
    if (confirmData.forItems) {
      finishBatch()
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

      <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleFileInputChange} />

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
          {confirmData.category_folder && (
            // The candidate also carries a suffix matching a theme
            // folder — offer that as its own choice instead of only
            // ever framing this as a typo.
            <button onClick={handleCategoryChoice}>File into {confirmData.category_folder}</button>
          )}{' '}
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
    </div>
  )
}

export default UploadButton

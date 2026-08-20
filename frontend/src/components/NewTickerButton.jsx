import { useState } from 'react'
import { api } from '../api.js'

// A standalone "create a brand-new, empty ticker folder" entry point —
// separate from the implicit ticker creation that already happens as a
// side effect of an upload (see UploadButton's new_ticker_status confirm
// dialog). Lives in the sidebar next to Upload, since like Upload it's a
// global action, not tied to whichever tab happens to be open — the user
// picks the status explicitly rather than it being inferred from context.
//
// A centered popup (see .modal-overlay/.modal in index.css), not a panel
// anchored under the button — the button lives in the narrow sidebar,
// nowhere near wide enough for a name field plus three status buttons to
// sit comfortably.
function NewTickerButton({ onCreated }) {
  const [isOpen, setIsOpen] = useState(false)
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')

  function close() {
    setIsOpen(false)
    setName('')
    setMessage('')
  }

  async function handleCreate(status) {
    const ticker = name.trim()
    if (!ticker) return
    try {
      const result = await api.createTicker(ticker, status)
      if (result.status === 'already_exists') {
        setMessage(`"${ticker}" already exists.`)
        return
      }
      close()
      onCreated()
    } catch (err) {
      setMessage(`Failed to create "${ticker}": ${err.message}`)
    }
  }

  return (
    <>
      <button className="upload-toggle" onClick={() => setIsOpen(true)}>
        New ticker
      </button>
      {isOpen && (
        <div className="modal-overlay" onClick={close}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>New ticker</h3>
            <input
              type="text"
              placeholder="Ticker name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
            <p>Which status?</p>
            <div className="modal__status-choices">
              <button onClick={() => handleCreate('active')}>Active</button>
              <button onClick={() => handleCreate('inactive')}>Inactive</button>
              <button onClick={() => handleCreate('historicals')}>Historicals</button>
            </div>
            {message && <p>{message}</p>}
            <button className="modal__cancel" onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </>
  )
}

export default NewTickerButton

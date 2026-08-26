import { useEffect, useRef } from 'react'

// A small "⋯" options menu for a single list row (a file, currently) —
// used by both TickerDetail's file list and TickerList's Needs Review
// list, which both need a per-row "Delete" tucked out of the way instead
// of sitting inline next to the filename. Deliberately dumb: the parent
// owns open/closed state (same pattern as every other confirm/menu state
// in this app, e.g. confirmDeleteFile) rather than this managing its own
// — keeps only one place responsible for "which row's menu is open."
function RowMenu({ open, onToggle, children }) {
  const ref = useRef(null)

  // Close on a click (left or right) anywhere outside this menu's own DOM
  // — otherwise a menu opened via right-click (see TickerDetail.jsx/
  // TickerList.jsx's onContextMenu handlers) would just sit open until the
  // user happened to click one of its own items. Deliberately `mousedown`,
  // not `contextmenu` — right-clicking a *different* row's card already
  // opens that row's own menu via its own onContextMenu handler (which
  // implicitly closes this one, since all rows of one kind share a single
  // "which key is open" state value); adding a second contextmenu listener
  // here would race that state update and could close the newly-opened
  // menu instead. `onToggle`'s own closure already resolves to "close"
  // whenever this is the row that's actually open (every call site builds
  // it as `() => setXMenu(xMenu === key ? null : key)`, and that
  // comparison was evaluated at render time against the state that made
  // `open` true here), so no separate "close" callback is needed.
  useEffect(() => {
    if (!open) return
    function handleOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) {
        onToggle()
      }
    }
    document.addEventListener('mousedown', handleOutside)
    return () => document.removeEventListener('mousedown', handleOutside)
  }, [open, onToggle])

  return (
    <span className="row-menu" ref={ref}>
      <button type="button" className="row-menu__trigger" onClick={onToggle} aria-label="Options" aria-expanded={open}>
        ⋮
      </button>
      {open && <div className="row-menu__items">{children}</div>}
    </span>
  )
}

export default RowMenu

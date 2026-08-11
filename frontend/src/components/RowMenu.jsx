// A small "⋯" options menu for a single list row (a file, currently) —
// used by both TickerDetail's file list and TickerList's Needs Review
// list, which both need a per-row "Delete" tucked out of the way instead
// of sitting inline next to the filename. Deliberately dumb: the parent
// owns open/closed state (same pattern as every other confirm/menu state
// in this app, e.g. confirmDeleteFile) rather than this managing its own
// — keeps only one place responsible for "which row's menu is open."
function RowMenu({ open, onToggle, children }) {
  return (
    <span className="row-menu">
      <button type="button" className="row-menu__trigger" onClick={onToggle} aria-label="Options" aria-expanded={open}>
        ⋮
      </button>
      {open && <div className="row-menu__items">{children}</div>}
    </span>
  )
}

export default RowMenu

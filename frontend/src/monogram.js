// A short "monogram" label for a ticker or theme folder that doesn't have
// a real company logo (see tickerLogos in TickerList.jsx) — shown instead
// of the plain folder icon so a folder without one still looks deliberate
// rather than blank.
//
// Mirrors the same ticker-vs-theme-folder distinction the backend uses
// (see docs/sorting-behavior-notes.md #1 and #4, ticker_registry.py): a
// real ticker's own exchange suffix is a bare dot (e.g. "ZRE.TO"), while a
// theme folder's suffix marker is parenthesized (e.g. "Busted Biotechs
// (.BB)") and stripped before computing initials — the two never overlap,
// so this can tell them apart the same way the backend does, purely from
// the string shape.
const THEME_FOLDER_MARKER = /\s*\(\.\w+\)\s*$/
const BARE_TICKER = /^[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?$/

export function tickerMonogram(name) {
  if (BARE_TICKER.test(name)) {
    // A real ticker (or a single-word folder name) — the whole symbol, not
    // just its initials. The exchange suffix, if any, isn't part of the
    // company's own identity visually, so it's dropped same as before.
    return name.split('.')[0].toUpperCase()
  }
  // A theme folder (or any other multi-word name) — initials only, same
  // as before this change; there's no single "symbol" to show in full for
  // something like "Busted Biotechs (.BB)".
  const words = name.replace(THEME_FOLDER_MARKER, '').trim().split(/\s+/).filter(Boolean)
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase()
  }
  return (words[0] || name).slice(0, 2).toUpperCase()
}

// A ticker's full symbol can be much longer than a theme folder's fixed
// 2-letter initials (a real ticker can run 4-5+ characters), so the
// monogram box needs to shrink its font to keep longer symbols from
// overflowing a box sized for two characters. `base` is the font-size (in
// px) a 1-2 character monogram should render at.
export function monogramFontSize(text, base) {
  if (text.length <= 2) return base
  if (text.length <= 4) return Math.round(base * 0.72)
  if (text.length <= 6) return Math.round(base * 0.56)
  return Math.round(base * 0.44)
}

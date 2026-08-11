import { useState } from 'react'
import { api } from '../api.js'

// Extensions the backend can actually generate a real preview for — see
// backend/app/services/thumbnails.py for the full reasoning (PDF/Word/
// PowerPoint-family via a rendered-preview-to-JPEG pipeline, plain images
// via Dropbox's own thumbnail API). Kept in sync with that file's own
// extension sets. Checked here first so a file type we already know has
// no preview (Excel/CSV, most commonly) never even attempts the request.
const PREVIEWABLE_EXTENSIONS = new Set([
  '.pdf',
  '.doc',
  '.docm',
  '.docx',
  '.ppt',
  '.pptm',
  '.pptx',
  '.rtf',
  '.odt',
  '.odp',
  '.pps',
  '.ppsm',
  '.ppsx',
  '.jpg',
  '.jpeg',
  '.png',
  '.tiff',
  '.tif',
  '.gif',
  '.webp',
  '.ppm',
  '.bmp',
])

function extensionOf(filename) {
  const dot = filename.lastIndexOf('.')
  return dot === -1 ? '' : filename.slice(dot).toLowerCase()
}

// A real preview image for a file, or a plain generic icon when there
// isn't one — either because the file type genuinely has none (see
// thumbnails.py — Excel/CSV are the big one) or because generating one
// failed for this particular file (corrupt, empty, too large). Both cases
// look identical to this component: the backend just 404s either way, and
// `failed` catches it the same way regardless of which reason it was.
//
// `size`: 'small' (default, a compact list-row icon — Needs Review) or
// 'large' (a real preview-card image — the ticker file grid).
function FileThumbnail({ ticker, filename, relativePath, size = 'small' }) {
  const [failed, setFailed] = useState(false)
  const previewable = PREVIEWABLE_EXTENSIONS.has(extensionOf(filename))
  const sizeClass = size === 'large' ? 'file-thumbnail--large' : 'file-thumbnail--small'

  if (!previewable || failed) {
    return (
      <span className={`file-thumbnail file-thumbnail--fallback ${sizeClass}`} aria-hidden="true">
        📄
      </span>
    )
  }

  return (
    <img
      className={`file-thumbnail ${sizeClass}`}
      src={api.thumbnailUrl(ticker, filename, relativePath)}
      alt=""
      onError={() => setFailed(true)}
    />
  )
}

export default FileThumbnail

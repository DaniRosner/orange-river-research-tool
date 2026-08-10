import { useEffect, useRef, useState } from 'react'

// A piece of state that automatically resets to `emptyValue` a few
// seconds after being set to something else — used for transient status
// messages ("Moved to Inactive.", "Assigned to ZBQ.", the upload log) so
// they don't linger on screen forever. Works as a drop-in replacement for
// useState; the only extra behavior is the auto-clear. If the value keeps
// changing (e.g. a multi-file upload log growing as each file finishes),
// the clock restarts each time rather than firing mid-batch.
export function useAutoDismiss(emptyValue, timeoutMs = 7000) {
  const [value, setValue] = useState(emptyValue)
  const timerRef = useRef(null)

  useEffect(() => {
    const isEmpty = Array.isArray(emptyValue) ? value.length === 0 : value === emptyValue
    if (!isEmpty) {
      timerRef.current = setTimeout(() => setValue(emptyValue), timeoutMs)
      return () => clearTimeout(timerRef.current)
    }
  }, [value, emptyValue, timeoutMs])

  return [value, setValue]
}

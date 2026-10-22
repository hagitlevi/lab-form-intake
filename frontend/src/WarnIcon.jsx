import { useState } from 'react'

// Small warning icon (not orange: field stays safe); click toggles the explanation.
export default function WarnIcon({ reasons }) {
  const [open, setOpen] = useState(false)
  if (!reasons?.length) return null
  return (
    <span className="warnicon" onClick={(e) => e.stopPropagation()}>
      <button type="button" className="warnicon-btn" aria-label="אזהרה" aria-expanded={open}
        onClick={() => setOpen(!open)} onBlur={() => setOpen(false)}>⚠</button>
      {open && <span className="warnicon-pop" role="note">{reasons.map((r, i) => <span key={i}>{r}</span>)}</span>}
    </span>
  )
}

import { useRef, useState } from 'react'

// Field editing shared by the review table and the single-form screen: same PATCH calls,
// same server validation message, same "second Enter confirms anyway" override.

export const SHARED = ['first_name', 'last_name', 'national_id', 'received_date', 'sender']

export async function call(url, method = 'POST', body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => ({}))
  return { ok: res.ok, status: res.status, json }
}

export const errText = (r) => (typeof r.json.detail === 'string' ? r.json.detail : `שגיאה ${r.status}`)

// PATCH body for one shared field or one commitment/central item
export const patchBody = (name, itemIndex, value) =>
  name === 'commitment_number' ? { commitment_numbers: { [itemIndex]: value } }
    : name === 'central_number' ? { central_numbers: { [itemIndex]: value } }
      : { [name]: value }

// returns an error text, or null on success
export async function patchField(formId, body) {
  const r = await call(`/api/forms/${formId}`, 'PATCH', body)
  return r.ok ? null : errText(r)
}

// Split a field's reasons: warnings (shown as the ⚠ icon) vs. review reasons (orange).
// Prefix of ID_WARNING in backend/pipeline/confidence.py - keep in sync.
export function splitReasons(reasons) {
  const out = { review: [], warnings: [] }
  for (const r of reasons || []) (r.startsWith('מספר הזהות אינו תקין') ? out.warnings : out.review).push(r)
  return out
}

export const rowReview = (r) =>
  SHARED.some((n) => r[n].state === 'review') ||
  r.commitment_number.state === 'review' ||
  r.central_numbers.some((c) => c.state === 'review')

// Draft/commit state for one editable value. onSave(value|null) resolves to an error text or null.
// commit() resolves true when the edit is finished (saved or unchanged), false on a server error
// (the message stays in `err`). A second Enter on an unchanged value is forced through to the server.
export function useFieldEdit(value, onSave) {
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  const forced = useRef(false)

  const commit = async (confirmEmpty) => {
    const next = confirmEmpty ? '' : draft.trim()
    if (!confirmEmpty && next === value && !forced.current) return true
    forced.current = false
    const e = await onSave(next === '' ? null : next)
    if (e) { setErr(e); return false }
    setErr('')
    return true
  }

  const inputProps = (close) => ({
    value: draft,
    onChange: (e) => setDraft(e.target.value),
    onBlur: async () => { if (await commit(false)) close() },
    onKeyDown: (e) => {
      if (e.key === 'Enter') { forced.current = true; e.target.blur() }
      if (e.key === 'Escape') { setErr(''); close() }
    },
  })

  return { draft, setDraft, err, setErr, commit, inputProps }
}

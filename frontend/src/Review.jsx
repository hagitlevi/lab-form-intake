import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from './App.jsx'

const SHARED = ['first_name', 'last_name', 'national_id', 'received_date', 'sender']
const HEADERS = ["עמ'", 'שם פרטי', 'שם משפחה', 'ת״ז', 'מספר מרכזי', 'תאריך קבלה', 'גורם שולח', 'מספר התחייבות', '']
const NUMERIC = new Set(['national_id', 'central_numbers', 'received_date', 'commitment_number'])

async function call(url, method = 'POST', body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => ({}))
  return { ok: res.ok, status: res.status, json }
}

const errText = (r) => (typeof r.json.detail === 'string' ? r.json.detail : `שגיאה ${r.status}`)

function Cell({ field, numeric, onSave, listId }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  const forced = useRef(false)
  const value = field.value ?? ''
  const review = field.state === 'review'

  const commit = async (confirmEmpty) => {
    const next = confirmEmpty ? '' : draft.trim()
    if (!confirmEmpty && next === value && !forced.current) return setEditing(false)
    forced.current = false
    const e = await onSave(next === '' ? null : next)
    if (e) setErr(e)
    else { setErr(''); setEditing(false) }
  }

  const shown = review && value === '' ? <span className="empty">ריק</span> : numeric ? value.split(' ').map((v, i) => <span key={i} className="num">{v}</span>) : value
  return (
    <div className={'cellbox' + (review ? ' review' : '')} data-review={review ? '1' : undefined}
      onClick={() => { if (!editing) { setDraft(value); setEditing(true) } }}>
      {editing ? (
        <input autoFocus value={draft} dir={numeric ? 'ltr' : undefined} list={listId}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => commit(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { forced.current = true; e.target.blur() }
            if (e.key === 'Escape') { setErr(''); setEditing(false) }
          }} />
      ) : (
        <>
          {shown}
          {field.state === 'corrected' && <span className="mark">תוקן</span>}
        </>
      )}
      {review && field.reasons?.map((r, i) => <span key={i} className="reason">לבדיקה · {r}</span>)}
      {review && !editing && field.reasons?.length > 0 && <div className="hint">Enter נוסף לאישור בכל זאת</div>}
      {field.state === 'corrected' && field.reasons?.map((r, i) => <div key={i} className="warn">{r}</div>)}
      {err && <div className="err">{err}</div>}
      {review && value === '' && !editing && (
        <button className="btn btn-ghost btn-sm confirm" onClick={(e) => { e.stopPropagation(); commit(true) }}>אישור כריק</button>
      )}
    </div>
  )
}

function centralField(list) {
  const states = list.map((c) => c.state)
  return {
    value: list.map((c) => c.value).filter(Boolean).join(' '),
    state: states.includes('review') ? 'review' : states.includes('corrected') ? 'corrected' : 'safe',
    reasons: list.flatMap((c) => c.reasons || []),
  }
}

function pagesLabel(pages) {
  const byFile = new Map()
  for (const p of pages) {
    const r = byFile.get(p.filename) || [p.page_index, p.page_index]
    byFile.set(p.filename, [Math.min(r[0], p.page_index), Math.max(r[1], p.page_index)])
  }
  return [...byFile].map(([name, [a, b]]) => (
    <div key={name}>{name} עמ׳ <span className="num">{a === b ? a : `${a}–${b}`}</span></div>
  ))
}

const rowReview = (r) =>
  SHARED.some((n) => r[n].state === 'review') ||
  r.commitment_number.state === 'review' ||
  r.central_numbers.some((c) => c.state === 'review')

// distinct fields in review (shared fields repeat on every commitment row of a form)
function countReview(rows) {
  const keys = new Set()
  for (const r of rows) {
    for (const n of SHARED) if (r[n].state === 'review') keys.add(`${r.form_id}-${n}`)
    if (r.commitment_number.state === 'review') keys.add(`${r.form_id}-c${r.item_index}`)
    r.central_numbers.forEach((c, i) => { if (c.state === 'review') keys.add(`${r.form_id}-m${i}`) })
  }
  return keys.size
}

export default function Review({ id }) {
  const [batch, setBatch] = useState(null)
  const [loadErr, setLoadErr] = useState('')
  const [onlyReview, setOnlyReview] = useState(false)
  const [senders, setSenders] = useState([])
  const [busyForm, setBusyForm] = useState(null)
  const [formErr, setFormErr] = useState({})
  const [approveMsg, setApproveMsg] = useState('')

  const load = useCallback(async () => {
    const r = await call(`/api/batches/${id}`, 'GET')
    if (r.ok) { setBatch(r.json); setLoadErr('') } else setLoadErr(errText(r))
  }, [id])

  useEffect(() => {
    load()
    fetch('/api/senders').then((r) => r.json()).then(setSenders).catch(() => {})
  }, [load])

  const status = batch?.status
  useEffect(() => {
    if (status && status !== 'processing') return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [status, load])

  const jump = () => document.querySelector('[data-review="1"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' })

  const patch = (formId, body) => async (value) => {
    const r = await call(`/api/forms/${formId}`, 'PATCH', body(value))
    if (!r.ok) return errText(r)
    await load()
    return null
  }

  const formAction = async (formId, path) => {
    setBusyForm(formId)
    const r = await call(`/api/forms/${formId}/${path}`)
    setFormErr((e) => ({ ...e, [formId]: r.ok ? '' : errText(r) }))
    setBusyForm(null)
    load()
  }

  const approve = async () => {
    const r = await call(`/api/batches/${id}/approve`)
    if (r.status === 409) {
      setApproveMsg(`נותרו ${r.json.detail?.count ?? ''} שדות לבדיקה`)
      await load()
      jump()
    } else if (r.ok) { setApproveMsg(''); load() } else setApproveMsg(errText(r))
  }

  if (loadErr && !batch) return <div className="notice bad">{loadErr}</div>
  if (!batch) return <div className="notice">טוען…</div>

  const rows = batch.rows
  const formNumber = new Map()
  for (const r of rows) if (!formNumber.has(r.form_id)) formNumber.set(r.form_id, formNumber.size + 1)
  const formIds = [...formNumber.keys()]
  const reviewCount = countReview(rows)
  const visible = onlyReview ? rows.filter(rowReview) : rows

  const pagesPhase = batch.pages_done < batch.total_pages || batch.total_forms === 0
  const [done, total] = pagesPhase ? [batch.pages_done, batch.total_pages] : [batch.forms_done, batch.total_forms]
  const processing = batch.status === 'processing'
  const approved = batch.status === 'approved'
  const noForms = !processing && rows.length === 0

  const pageErrors = rows.flatMap((r, i) =>
    i && rows[i - 1].form_id === r.form_id ? [] : r.pages.filter((p) => p.error).map((p) => ({ ...p, form: formNumber.get(r.form_id) })))

  return (
    <>
      <div className="page-head">
        <div>
          <h1>בדיקת ערימה</h1>
          <span><span className="num">{formIds.length}</span> טפסים · <span className="num">{rows.length}</span> רשומות</span>
        </div>
        <div>
          {approveMsg && <span className="rowerr">{approveMsg}</span>}
          <button className="btn btn-primary" disabled={approved || processing} onClick={approve}>
            אישור ושמירה של <span className="num">{rows.length}</span> רשומות
          </button>
        </div>
      </div>

      <div className="card progress">
        {processing ? (
          <>
            <div>{pagesPhase ? 'עמודים' : 'טפסים'}: <span className="num">{done}</span> מתוך <span className="num">{total}</span></div>
            <div className="bar"><div style={{ width: total ? `${(100 * done) / total}%` : '0%' }} /></div>
          </>
        ) : batch.error ? (
          <div>העיבוד נכשל</div>
        ) : noForms ? (
          <div>לא נמצאו טפסים בקובץ</div>
        ) : (
          <div>
            {approved ? 'האצווה אושרה ונשמרה' : 'העיבוד הסתיים'} · <span className="num">{batch.forms_done}</span> טפסים
          </div>
        )}
      </div>

      {!processing && batch.error && <div className="notice bad">{batch.error}</div>}
      {!processing && !batch.error && batch.status === 'interrupted' && (
        <div className="notice bad">העיבוד הופסק לפני שהסתיים. ייתכן שחלק מהטפסים חסרים.</div>
      )}
      {batch.skipped_pages.length > 0 && (
        <div className="notice">
          דפים ריקים שדולגו:{' '}
          {batch.skipped_pages.map((s, i) => (
            <span key={i}>{i ? ', ' : ''}{s.filename} עמ׳ <span className="num">{s.page_index}</span></span>
          ))}
        </div>
      )}
      {pageErrors.map((p) => (
        <div key={p.id} className="notice bad">
          טופס <span className="num">{p.form}</span>, {p.filename} עמ׳ <span className="num">{p.page_index}</span>: {p.error}
        </div>
      ))}

      {reviewCount > 0 && (
        <div className="banner" role="status">
          <div>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#B35C00" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 2.5 20h19z" /><path d="M12 10v4" /><path d="M12 17.5h.01" /></svg>
            <span><b><span className="num">{reviewCount}</span> שדות לבדיקה.</b> כל שאר השדות זוהו בביטחון גבוה.</span>
          </div>
          <button className="btn btn-ghost" onClick={jump}>לשדה הראשון לבדיקה</button>
        </div>
      )}

      <div className="toolbar">
        <div className="seg-group" role="group" aria-label="סינון">
          <button className={'seg' + (onlyReview ? '' : ' on')} onClick={() => setOnlyReview(false)}>הכל (<span className="num">{rows.length}</span>)</button>
          <button className={'seg' + (onlyReview ? ' on' : '')} onClick={() => setOnlyReview(true)}>רק לבדיקה (<span className="num">{rows.filter(rowReview).length}</span>)</button>
        </div>
        <span className="toolbar-hint">לחיצה על תא פותחת עריכה</span>
      </div>

      <datalist id="senders">{senders.map((s) => <option key={s} value={s} />)}</datalist>
      <div className="table-wrap">
      <table className="tbl">
        <colgroup>
          {[6, 10, 10, 12, 12, 12, 14, 14, 10].map((w, i) => <col key={i} style={{ width: `${w}%` }} />)}
        </colgroup>
        <thead><tr>{HEADERS.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>
          {visible.map((r, i) => {
            const first = i === 0 || visible[i - 1].form_id !== r.form_id
            const fid = r.form_id
            const prev = formIds[formIds.indexOf(fid) - 1]
            const sharedCell = (n) => (
              <td className="cell" key={n}>
                <Cell field={r[n]} numeric={NUMERIC.has(n)} listId={n === 'sender' ? 'senders' : undefined}
                  onSave={patch(fid, (v) => ({ [n]: v }))} />
              </td>
            )
            return (
              <tr key={`${fid}-${r.item_index}`}>
                <td>
                  {first && (
                    <>
                      <div>טופס <span className="num">{formNumber.get(fid)}</span></div>
                      {r.probable_continuation && prev && (
                        <div className="tag">
                          ייתכן שזה המשך של טופס <span className="num">{formNumber.get(prev)}</span>
                          <button className="btn btn-ghost btn-sm" disabled={busyForm === fid} onClick={() => formAction(fid, 'merge-with-previous')}>
                            {busyForm === fid ? 'מצרף…' : 'צירוף'}
                          </button>
                          <button className="btn btn-ghost btn-sm" disabled={busyForm === fid} onClick={() => formAction(fid, 'mark-separate')}>זה טופס נפרד</button>
                        </div>
                      )}
                      {r.split_by_ai && <div className="tag">פוצל לבדיקה</div>}
                      {formErr[fid] && <div className="rowerr">{formErr[fid]}</div>}
                      {r.error && <div className="rowerr">{r.error}</div>}
                    </>
                  )}
                  <div className="pages">{pagesLabel(r.pages)}</div>
                </td>
                {sharedCell('first_name')}
                {sharedCell('last_name')}
                {sharedCell('national_id')}
                <td className="cell">
                  <Cell field={centralField(r.central_numbers)} numeric
                    onSave={patch(fid, (v) => ({ central_numbers: v ? v.split(/[\s,]+/).filter(Boolean) : [] }))} />
                </td>
                {sharedCell('received_date')}
                {sharedCell('sender')}
                <td className="cell">
                  <Cell field={r.commitment_number} numeric
                    onSave={patch(fid, (v) => ({ commitment_numbers: { [r.item_index]: v } }))} />
                </td>
                <td><Link to={`/forms/${fid}`}>טופס</Link></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </>
  )
}

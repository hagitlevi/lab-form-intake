import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, navigate } from './App.jsx'
import WarnIcon from './WarnIcon.jsx'
import { SHARED, call, errText, patchBody, patchField, rowReview, splitReasons, toIL, useFieldEdit } from './fieldEdit.js'

const LABELS = {
  first_name: 'שם פרטי', last_name: 'שם משפחה', national_id: 'ת״ז', received_date: 'תאריך קבלה',
  sender: 'גורם שולח', central_number: 'מספר מרכזי', commitment_number: 'מספר התחייבות',
}
const NUM = new Set(['national_id', 'received_date', 'central_number', 'commitment_number'])
const keyOf = (f) => `${f.name}:${f.item_index}`
const isReview = (f) => f.state === 'review'
const LIST_BODY = { central_number: 'central_numbers', commitment_number: 'commitment_numbers' }

function orderedItems(fields) {
  const get = (name) => fields.filter((f) => f.name === name).sort((a, b) => a.item_index - b.item_index)
  const shared = SHARED.map((n) => get(n)[0] ?? { name: n, item_index: 0, value: null, state: 'safe', reasons: [], bbox: null, page_id: null })
  return [...shared, ...get('central_number'), ...get('commitment_number')]
}

function Field({ f, onSave, onSelect, onRemove, senders, reg }) {
  const value = f.name === 'received_date' ? toIL(f.value ?? '') : (f.value ?? '')
  const review = isReview(f)
  const reasons = splitReasons(f.reasons)
  const num = NUM.has(f.name)
  const latest = useRef(value)
  latest.current = value
  const { setDraft, err, commit, inputProps } = useFieldEdit(value, onSave)
  const [selErr, setSelErr] = useState('')
  useEffect(() => { setDraft(value) }, [value, setDraft])

  const id = `f-${keyOf(f)}`
  const props = inputProps(() => setDraft(latest.current))
  const cls = 'inp' + (num ? ' num' : '') + (review ? ' inp-flag' : '')
  return (
    <div className={'field' + (review ? ' flagged' : '')} data-review={review ? '1' : undefined}>
      <label htmlFor={id}>{LABELS[f.name]}</label>
      <div className="field-row">
        {f.name === 'sender' ? (
          <select id={id} ref={reg} className={cls} value={value} onFocus={onSelect}
            onChange={async (e) => setSelErr((await onSave(e.target.value || null)) || '')}>
            <option value="">—</option>
            {value && !senders.includes(value) && <option value={value}>{value}</option>}
            {senders.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        ) : (
          <input id={id} ref={reg} className={cls} dir={num ? 'ltr' : undefined} onFocus={onSelect} {...props} />
        )}
        {onRemove && <button className="btn btn-ghost btn-sm" onClick={onRemove}>הסרה</button>}
      </div>
      {review && reasons.review.map((r, i) => <span key={i} className="reason">לבדיקה · {r}</span>)}
      {review && reasons.review.length > 0 && f.name !== 'sender' && <span className="hint">Enter נוסף לאישור בכל זאת</span>}
      <WarnIcon reasons={reasons.warnings} />
      {reasons.debug.map((d, i) => <span key={i} className="debug" dir="ltr">{d}</span>)}
      {review && value === '' && (
        <button className="btn btn-ghost btn-sm confirm" onClick={async () => { await commit(true) }}>אישור כריק</button>
      )}
      {(err || selErr) && <span className="err">{err || selErr}</span>}
    </div>
  )
}

function NewItem({ label, onSave, onDone }) {
  const [v, setV] = useState('')
  const [err, setErr] = useState('')
  const closed = useRef(false)
  const save = async () => {
    if (closed.current) return
    const t = v.trim()
    if (!t) return onDone()
    const e = await onSave(t)
    if (e) setErr(e)
    else { closed.current = true; onDone() }
  }
  return (
    <div className="field">
      <label>{label}</label>
      <input className="inp num" dir="ltr" autoFocus value={v} onChange={(e) => setV(e.target.value)} onBlur={save}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.target.blur()
          if (e.key === 'Escape') { closed.current = true; onDone() }
        }} />
      {err && <span className="err">{err}</span>}
    </div>
  )
}

const Icon = ({ children }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)

function PageViewer({ pageIds, pageId, setPageId, field, nonce }) {
  const [zoom, setZoom] = useState(1)
  const [rot, setRot] = useState(0)
  const [nats, setNats] = useState({})
  const [cont, setCont] = useState({ w: 0, h: 0 })
  const [centerReq, setCenterReq] = useState(0)
  const box = useRef(null)

  useEffect(() => {
    const el = box.current
    const ro = new ResizeObserver(() => setCont({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // selecting a field that has a bbox zooms in (at least 2x) and centers it
  useEffect(() => {
    if (!nonce) return
    if (field?.bbox) setZoom((z) => Math.max(z, 2))
    setCenterReq((c) => c + 1)
  }, [nonce]) // eslint-disable-line react-hooks/exhaustive-deps

  const nat = nats[pageId]
  const sideways = rot % 180 !== 0
  const rw = nat ? (sideways ? nat.h : nat.w) : 0
  const rh = nat ? (sideways ? nat.w : nat.h) : 0
  const scale = nat && cont.w ? Math.min(cont.w / rw, cont.h / rh) * zoom : 0
  const W = nat ? nat.w * scale : 0
  const H = nat ? nat.h * scale : 0
  const ow = sideways ? H : W
  const oh = sideways ? W : H
  const b = field?.bbox && field.page_id === pageId && nat ? field.bbox : null

  useEffect(() => {
    const el = box.current
    if (!el || !scale || !field?.bbox || field.page_id !== pageId) return
    const [x, y, w, h] = field.bbox
    const a = (rot * Math.PI) / 180
    const dx = (x + w / 2) * scale - W / 2
    const dy = (y + h / 2) * scale - H / 2
    const cx = ow / 2 + dx * Math.cos(a) - dy * Math.sin(a)
    const cy = oh / 2 + dx * Math.sin(a) + dy * Math.cos(a)
    el.scrollTo({ left: cx - el.clientWidth / 2, top: cy - el.clientHeight / 2, behavior: 'smooth' })
  }, [centerReq, nat]) // eslint-disable-line react-hooks/exhaustive-deps

  const zoomBy = (k) => setZoom((z) => Math.min(8, Math.max(0.25, z * k)))
  return (
    <section className="viewer" aria-label="הטופס הסרוק">
      <div className="viewer-bar">
        {pageIds.length > 1 && (
          <div className="seg-group" role="group" aria-label="עמודים">
            {pageIds.map((p, i) => (
              <button key={p} className={'seg' + (p === pageId ? ' on' : '')} onClick={() => setPageId(p)}>עמוד <span className="num">{i + 1}</span></button>
            ))}
          </div>
        )}
        <div className="viewer-tools">
          <button className="icon-btn" aria-label="הגדלה" onClick={() => zoomBy(1.25)}><Icon><circle cx="11" cy="11" r="6.5" /><path d="M11 8v6M8 11h6M16 16l4 4" /></Icon></button>
          <button className="icon-btn" aria-label="הקטנה" onClick={() => zoomBy(0.8)}><Icon><circle cx="11" cy="11" r="6.5" /><path d="M8 11h6M16 16l4 4" /></Icon></button>
          <button className="btn btn-ghost btn-sm" onClick={() => setZoom(1)}>התאמה</button>
          <button className="icon-btn" aria-label="סיבוב" onClick={() => setRot((r) => (r + 90) % 360)}><Icon><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v4h-4" /></Icon></button>
        </div>
      </div>
      <div className="viewer-scroll" ref={box}>
        {pageId && (
          <div style={{ position: 'relative', flex: 'none', margin: 'auto', width: ow, height: oh }}>
            <div style={{ position: 'absolute', width: W, height: H, left: (ow - W) / 2, top: (oh - H) / 2, transform: `rotate(${rot}deg)` }}>
              <img key={pageId} src={`/api/pages/${pageId}/image`} alt="" draggable={false}
                style={nat ? { width: W, height: H, display: 'block' } : { position: 'absolute', visibility: 'hidden' }}
                onLoad={(e) => setNats((n) => ({ ...n, [pageId]: { w: e.target.naturalWidth, h: e.target.naturalHeight } }))} />
              {nat && (
                <svg viewBox={`0 0 ${nat.w} ${nat.h}`} width={W} height={H} style={{ position: 'absolute', inset: 0 }} aria-hidden="true">
                  {b && <rect data-testid="bbox" x={b[0] - 4} y={b[1] - 4} width={b[2] + 8} height={b[3] + 8} rx="3"
                    fill="rgba(232,137,43,.15)" stroke="#E8892B" strokeWidth="3" vectorEffect="non-scaling-stroke" />}
                </svg>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

export default function FormView({ id }) {
  const [form, setForm] = useState(null)
  const [rows, setRows] = useState([])
  const [senders, setSenders] = useState([])
  const [loadErr, setLoadErr] = useState('')
  const [sel, setSel] = useState(null)
  const [pageId, setPageId] = useState(null)
  const [nonce, setNonce] = useState(0)
  const [adding, setAdding] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const refs = useRef({})
  const inited = useRef(false)

  const load = useCallback(async () => {
    const r = await call(`/api/forms/${id}`, 'GET')
    if (!r.ok) return setLoadErr(errText(r))
    setForm(r.json)
    const b = await call(`/api/batches/${r.json.batch_id}`, 'GET')
    if (b.ok) setRows(b.json.rows)
  }, [id])

  useEffect(() => {
    load()
    fetch('/api/senders').then((r) => r.json()).then(setSenders).catch(() => {})
  }, [load])

  const items = form ? orderedItems(form.fields) : []
  const flagged = items.filter(isReview)
  const select = (f) => {
    setSel(keyOf(f))
    if (f.page_id && form.page_ids.includes(f.page_id)) setPageId(f.page_id)
    setNonce((n) => n + 1)
  }
  const focusItem = (f) => { select(f); refs.current[keyOf(f)]?.focus() }

  useEffect(() => {
    if (!form || inited.current) return
    inited.current = true
    setPageId(form.page_ids[0] ?? null)
    const first = flagged[0] ?? items[0]
    if (!first) return
    select(first)
    if (location.search.includes('focus=1')) setTimeout(() => refs.current[keyOf(first)]?.focus(), 0)
  }) // eslint-disable-line react-hooks/exhaustive-deps

  if (loadErr && !form) return <div className="notice bad">{loadErr}</div>
  if (!form) return <div className="notice">טוען…</div>

  const formIds = [...new Set(rows.map((r) => r.form_id))]
  const reviewForms = new Set(rows.filter(rowReview).map((r) => r.form_id))
  const number = formIds.indexOf(form.id) + 1
  const senderValue = items.find((f) => f.name === 'sender')?.value
  const selField = items.find((f) => keyOf(f) === sel)
  const selIdx = flagged.findIndex((f) => keyOf(f) === sel)
  const approved = form.status === 'approved'

  const save = (f) => async (value) => {
    const e = await patchField(form.id, patchBody(f.name, f.item_index, value))
    if (e) return e
    await load()
    return null
  }
  const addItem = (name) => async (value) => {
    const e = await patchField(form.id, patchBody(name, items.filter((f) => f.name === name).length, value))
    if (e) return e
    await load()
    return null
  }
  const removeItem = async (f) => {
    const rest = items.filter((x) => x.name === f.name && x !== f).map((x) => x.value)
    const e = await patchField(form.id, { [LIST_BODY[f.name]]: rest })
    if (e) setMsg(e)
    else { setMsg(''); await load() }
  }

  const goNext = () => {
    const nxt = flagged[selIdx + 1]
    if (nxt) return focusItem(nxt)
    const start = formIds.indexOf(form.id)
    for (let k = 1; k < formIds.length; k++) {
      const cand = formIds[(start + k) % formIds.length]
      if (reviewForms.has(cand)) return navigate(`/forms/${cand}?focus=1`)
    }
    setMsg('אין עוד שדות לבדיקה')
  }

  const approve = async () => {
    setBusy(true)
    const r = await call(`/api/forms/${form.id}/approve`)
    setBusy(false)
    if (r.status === 409) {
      setMsg(`נותרו ${r.json.detail?.count ?? ''} שדות לבדיקה`)
      await load()
      const first = flagged[0]
      if (first) focusItem(first)
    } else if (r.ok) {
      setMsg('')
      const others = formIds.filter((x) => x !== form.id && reviewForms.has(x))
      navigate(others.length ? `/forms/${others.find((x) => x > form.id) ?? others[0]}?focus=1` : `/batches/${form.batch_id}`)
    } else setMsg(errText(r))
  }

  const formAction = async (path) => {
    setBusy(true)
    const r = await call(`/api/forms/${form.id}/${path}`)
    setBusy(false)
    if (!r.ok) return setMsg(errText(r))
    setMsg('')
    if (r.json.form_id) navigate(`/forms/${r.json.form_id}`)
    else load()
  }

  const renderField = (f) => (
    <Field key={keyOf(f)} f={f} senders={senders} onSave={save(f)} onSelect={() => select(f)}
      reg={(el) => { refs.current[keyOf(f)] = el }}
      onRemove={f.name in LIST_BODY ? () => removeItem(f) : undefined} />
  )
  const rest = items.filter((f) => !isReview(f))

  return (
    <>
      <div className="page-head">
        <div className="form-title">
          <Link to={`/batches/${form.batch_id}`} className="back">
            <Icon><path d="m9 6 6 6-6 6" /></Icon>
            חזרה לטבלה
          </Link>
          <h1>טופס <span className="num">{number}</span> מתוך <span className="num">{formIds.length}</span>{senderValue ? ` · ${senderValue}` : ''}</h1>
        </div>
        <div>
          <span className="toolbar-hint">
            {selIdx >= 0 ? <>שדה <span className="num">{selIdx + 1}</span> מתוך <span className="num">{flagged.length}</span> לבדיקה</>
              : flagged.length ? <><span className="num">{flagged.length}</span> שדות לבדיקה</> : 'אין שדות לבדיקה'}
          </span>
        </div>
      </div>

      <div className="formgrid">
        <aside className="fields" aria-label="שדות הטופס">
          {form.error && <div className="rowerr">{form.error}</div>}
          {flagged.length > 0 && <div className="flagged-group">{flagged.map(renderField)}</div>}
          <div className="field-grid">
            {rest.map(renderField)}
            {adding && <NewItem label={LABELS[adding]} onSave={addItem(adding)} onDone={() => setAdding(null)} />}
          </div>
          <div className="add-row">
            <button className="link-btn" onClick={() => setAdding('central_number')}>+ הוספת מספר מרכזי</button>
            <button className="link-btn" onClick={() => setAdding('commitment_number')}>+ הוספת מספר התחייבות</button>
          </div>
          {form.probable_continuation && (
            <div className="tag">
              ייתכן שזה המשך של הטופס הקודם
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => formAction('merge-with-previous')}>צירוף לטופס הקודם</button>
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => formAction('mark-separate')}>זה טופס נפרד</button>
            </div>
          )}
          {msg && <div className="rowerr">{msg}</div>}
          <div className="form-actions">
            <button className="btn btn-primary" disabled={busy || approved} onClick={approve}>{approved ? 'הטופס אושר' : 'אישור הטופס'}</button>
            <button className="btn btn-ghost" onClick={goNext}>הבא לבדיקה</button>
          </div>
        </aside>
        <PageViewer pageIds={form.page_ids} pageId={pageId} setPageId={setPageId} field={selField} nonce={nonce} />
      </div>
    </>
  )
}

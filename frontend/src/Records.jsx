import { useEffect, useState } from 'react'
import { navigate } from './App.jsx'
import { call, errText } from './fieldEdit.js'

const HEADERS = ['סטטוס', 'שם', 'ת״ז', 'מספר מרכזי', 'תאריך קבלה', 'גורם שולח', 'מספר התחייבות']
const FILTERS = [['', 'הכל'], ['pending', 'ממתינות'], ['approved', 'אושרו']]

const Nums = ({ values }) => {
  const list = values.filter(Boolean)
  return list.length ? list.map((v, i) => <span key={i}><span className="num">{v}</span>{i < list.length - 1 && ', '}</span>) : <span className="empty">—</span>
}

export default function Records() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')

  // search is server-side (debounced); the status filter is applied here so the counts of all three tabs stay visible
  useEffect(() => {
    const t = setTimeout(async () => {
      const r = await call(`/api/records?q=${encodeURIComponent(q.trim())}`, 'GET')
      if (r.ok) { setRows(r.json.rows); setErr('') } else setErr(errText(r))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const count = (s) => rows.filter((r) => !s || r.status === s).length
  const visible = rows && rows.filter((r) => !status || r.status === status)
  const exportUrl = '/api/records/export.csv?' + new URLSearchParams({ q: q.trim(), status })

  return (
    <>
      <div className="page-head">
        <div><h1>רשומות</h1></div>
        <div>
          <a className="btn btn-ghost" href={exportUrl} download>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4v11" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" /><path d="M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1" /></svg>
            ייצוא CSV
          </a>
          <button className="btn btn-ghost" onClick={() => window.print()}>ייצוא PDF</button>
        </div>
      </div>

      <div className="toolbar">
        <div className="search">
          <label htmlFor="search" className="sr-only">חיפוש רשומות</label>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#5B6B7A" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4 4" /></svg>
          <input id="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="חיפוש לפי ת״ז, מספר מרכזי או מספר התחייבות" />
        </div>
        <div className="seg-group" role="group" aria-label="סינון לפי סטטוס">
          {FILTERS.map(([s, label]) => (
            <button key={s} className={'seg' + (status === s ? ' on' : '')} aria-pressed={status === s} onClick={() => setStatus(s)}>
              {label}{rows && <> (<span className="num">{count(s)}</span>)</>}
            </button>
          ))}
        </div>
      </div>

      {err && <div className="notice bad">{err}</div>}
      {!rows && !err && <div className="notice">טוען…</div>}
      {visible && visible.length === 0 && <div className="notice">{q || status ? 'לא נמצאו רשומות תואמות' : 'אין רשומות עדיין'}</div>}
      {visible && visible.length > 0 && (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr>{HEADERS.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>
              {visible.map((r) => (
                <tr key={`${r.form_id}-${r.item_index}`} className="clickable" tabIndex={0}
                  onClick={() => navigate(`/forms/${r.form_id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/forms/${r.form_id}`) }}>
                  <td><span className={'badge ' + (r.status === 'approved' ? 'b-ok' : 'b-wait')}>{r.status === 'approved' ? 'אושרה' : 'ממתינה'}</span></td>
                  <td>{[r.first_name.value, r.last_name.value].filter(Boolean).join(' ') || <span className="empty">—</span>}</td>
                  <td><Nums values={[r.national_id.value]} /></td>
                  <td><Nums values={r.central_numbers.map((c) => c.value)} /></td>
                  <td><Nums values={[r.received_date.value]} /></td>
                  <td>{r.sender.value || <span className="empty">—</span>}</td>
                  <td><Nums values={[r.commitment_number.value]} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

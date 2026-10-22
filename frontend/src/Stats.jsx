import { useEffect, useState } from 'react'
import { call, errText } from './fieldEdit.js'

// Share of fields per sender that came out "safe" (no technician attention needed).
export default function Stats() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    call('/api/stats/senders', 'GET').then((r) => (r.ok ? setRows(r.json) : setErr(errText(r))))
  }, [])

  return (
    <>
      <div className="page-head"><div><h1>סטטיסטיקה</h1><span>אחוז השדות שנקראו בביטחון, לפי גורם שולח</span></div></div>
      {err && <p>{err}</p>}
      {rows && !rows.length && <p>אין עדיין נתונים</p>}
      {rows && rows.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>קריאה מוצלחת לפי גורם שולח</h2>
          <div style={{ padding: '12px 20px', display: 'grid', gap: 12 }}>
            {rows.map((r) => {
              const pct = Math.round((100 * r.safe) / r.fields)
              return (
                <div key={r.sender} style={{ display: 'grid', gridTemplateColumns: '180px 1fr 150px', gap: 12, alignItems: 'center' }}>
                  <b>{r.sender}</b>
                  <div style={{ background: '#E6ECF0', borderRadius: 6, height: 22 }}>
                    <div style={{ width: `${pct}%`, background: '#0B6A85', height: '100%', borderRadius: 6 }} />
                  </div>
                  <span><span className="num">{pct}%</span> · {r.forms} טפסים</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </>
  )
}

import { useRef, useState } from 'react'
import { Link, lastBatch, navigate } from './App.jsx'

const OK = /\.(pdf|jpe?g|png)$/i

export default function Upload() {
  const [files, setFiles] = useState([])
  const [over, setOver] = useState(false)
  const [dragIdx, setDragIdx] = useState(null)
  const [overIdx, setOverIdx] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef()

  const add = (list) => setFiles((f) => [...f, ...[...list].filter((x) => OK.test(x.name))])

  const move = (from, to) => {
    if (from == null || from === to) return
    setFiles((f) => {
      const next = [...f]
      next.splice(to, 0, next.splice(from, 1)[0])
      return next
    })
  }

  const start = async () => {
    setBusy(true)
    setError('')
    const body = new FormData()
    files.forEach((f) => body.append('files', f))
    try {
      const res = await fetch('/api/batches', { method: 'POST', body })
      if (!res.ok) throw new Error(`שגיאה ${res.status}`)
      navigate(`/batches/${(await res.json()).batch_id}`)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const fileIcon = (name) => /\.pdf$/i.test(name)
    ? <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>
    : <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m21 16-5-5-8 8" /></>

  return (
    <>
      <div className="title-block">
        <h1>קליטת טפסים</h1>
        <p className="lead">מעלים את הטפסים שהגיעו. המערכת מחלצת את השדות ומסמנת רק את מה שצריך לבדוק.</p>
      </div>

      <div
        className={'drop' + (over ? ' over' : '')}
        onClick={() => input.current.click()}
        onDragOver={(e) => { e.preventDefault(); if (dragIdx == null) setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (dragIdx == null) add(e.dataTransfer.files) }}
      >
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#0B6A85" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 15V4" /><path d="M7.5 8.5 12 4l4.5 4.5" /><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></svg>
        <div className="drop-title">גוררים לכאן קבצים</div>
        <div className="drop-hint">PDF סרוק, צילומים מהטלפון או כמה קבצים יחד</div>
        <button type="button" className="btn btn-ghost">בחירת קבצים</button>
      </div>
      <input ref={input} type="file" multiple hidden accept=".pdf,.jpg,.jpeg,.png"
        onChange={(e) => { add(e.target.files); e.target.value = '' }} />

      {lastBatch.get() && (
        <div className="notice">
          יש ערימה שעדיין לא אושרה. <Link to={`/batches/${lastBatch.get()}`}>חזרה לערימה</Link>
        </div>
      )}

      {files.length > 0 && (
        <section className="card">
          <h2>{files.length} קבצים נבחרו</h2>
          <ol className="files">
            {files.map((f, i) => (
              <li key={i} draggable
                className={overIdx === i ? 'over' : ''}
                onDragStart={() => setDragIdx(i)}
                onDragOver={(e) => { e.preventDefault(); setOverIdx(i) }}
                onDragEnd={() => { setDragIdx(null); setOverIdx(null) }}
                onDrop={(e) => { e.preventDefault(); move(dragIdx, i); setDragIdx(null); setOverIdx(null) }}>
                <div className="file-info">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#5B6B7A" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{fileIcon(f.name)}</svg>
                  <div>
                    <span className="file-name">{f.name}</span>
                    <span className="file-sub">{/\.pdf$/i.test(f.name) ? 'PDF' : 'תמונה'}</span>
                  </div>
                </div>
                <button className="icon-btn" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="הסרת הקובץ">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}

      {error && <div className="notice bad">{error}</div>}
      <div className="upload-actions">
        <button className="btn btn-primary btn-lg" disabled={!files.length || busy} onClick={start}>
          {busy ? 'מעלה…' : `התחלת עיבוד${files.length ? ` · ${files.length} קבצים` : ''}`}
        </button>
      </div>
    </>
  )
}

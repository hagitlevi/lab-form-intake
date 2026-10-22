import { useEffect, useState } from 'react'
import Upload from './Upload.jsx'
import Review from './Review.jsx'
import FormView from './FormView.jsx'
import Records from './Records.jsx'

export function navigate(path) {
  history.pushState(null, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

// The batch the user is working on, so leaving to "רשומות" and back returns to it.
// Kept in localStorage (per browser); cleared once the batch is approved.
const KEY = 'lastBatch'
export const lastBatch = {
  get() { try { return localStorage.getItem(KEY) } catch { return null } },
  set(id) { try { localStorage.setItem(KEY, String(id)) } catch { /* storage blocked */ } },
  clear(id) { try { if (localStorage.getItem(KEY) === String(id)) localStorage.removeItem(KEY) } catch { /* storage blocked */ } },
}

export function Link({ to, className, children }) {
  return (
    <a href={to} className={className} onClick={(e) => { e.preventDefault(); navigate(to) }}>
      {children}
    </a>
  )
}

export default function App() {
  const [path, setPath] = useState(location.pathname)
  useEffect(() => {
    const on = () => setPath(location.pathname)
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])

  const batch = path.match(/^\/batches\/(\d+)$/)
  const form = path.match(/^\/forms\/(\d+)$/)
  let page = <Upload />
  if (batch) page = <Review key={batch[1]} id={batch[1]} />
  else if (form) page = <FormView key={form[1]} id={form[1]} />
  else if (path === '/records') page = <Records />

  const onRecords = path === '/records'
  const open = lastBatch.get()
  const intakePath = open ? `/batches/${open}` : '/'
  return (
    <>
      <header className="header">
        <div className="header-inner">
          <div className="brand">
            <b>המעבדה המרכזית לנגיפים</b>
            <span>המרכז הרפואי שיבא</span>
          </div>
          <nav className="nav-links" aria-label="ניווט ראשי">
            <Link to={intakePath} className={'nav' + (onRecords ? '' : ' on')}>קליטת טפסים</Link>
            <Link to="/records" className={'nav' + (onRecords ? ' on' : '')}>רשומות</Link>
          </nav>
        </div>
      </header>
      <main className={path === '/' ? 'narrow' : 'wide'}>{page}</main>
    </>
  )
}

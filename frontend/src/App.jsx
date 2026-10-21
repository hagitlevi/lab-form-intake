import { useEffect, useState } from 'react'
import Upload from './Upload.jsx'
import Review from './Review.jsx'

export function navigate(path) {
  history.pushState(null, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
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
  let page = <Upload />
  if (batch) page = <Review key={batch[1]} id={batch[1]} />
  else if (path === '/records' || path.startsWith('/forms/')) page = <div className="notice">בקרוב.</div>

  const onRecords = path === '/records'
  return (
    <>
      <header className="header">
        <div className="header-inner">
          <div className="brand">
            <b>המעבדה המרכזית לנגיפים</b>
            <span>המרכז הרפואי שיבא</span>
          </div>
          <nav className="nav-links" aria-label="ניווט ראשי">
            <Link to="/" className={'nav' + (onRecords ? '' : ' on')}>קליטת טפסים</Link>
            <Link to="/records" className={'nav' + (onRecords ? ' on' : '')}>רשומות</Link>
          </nav>
        </div>
      </header>
      <main className={path === '/' ? 'narrow' : 'wide'}>{page}</main>
    </>
  )
}

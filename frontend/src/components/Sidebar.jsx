import { useLocation, useNavigate } from 'react-router-dom'
import UploadButton from './UploadButton.jsx'
import { api } from '../api.js'
import { TABS, TAB_SLUGS, SLUGS_TO_TAB } from '../tabSlugs.js'
import logo from '../assets/logo.png'

// Which tab should read as "current" in the nav — derived straight from
// the URL rather than passed down as state, so it stays correct on both a
// list route ("/inactive") and a ticker detail route
// ("/ticker/inactive/ZPAX"), the same way Drive's own sidebar keeps a
// folder highlighted while you're browsing inside it.
function activeTabFromPath(pathname) {
  if (pathname.startsWith('/ticker/')) {
    const status = pathname.split('/')[2]
    return Object.keys(TAB_SLUGS).find((tab) => TAB_SLUGS[tab] === status) ?? 'Active'
  }
  return SLUGS_TO_TAB[pathname.slice(1)] ?? 'Active'
}

// Persistent left nav, rendered once at the App level (not per-route) —
// "+ New" and the tab list should stay available no matter what's on
// screen, same as Drive's own sidebar does.
function Sidebar({ user, onUploaded, onLoggedOut }) {
  const location = useLocation()
  const navigate = useNavigate()
  const activeTab = activeTabFromPath(location.pathname)

  async function handleLogout() {
    await api.logout()
    onLoggedOut()
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__title">
        <img src={logo} alt="" className="sidebar__logo" />
        Research Tool
      </div>
      <div className="sidebar__welcome">
        Welcome, {user.name}
        <button className="sidebar__logout" onClick={handleLogout}>
          Sign out
        </button>
      </div>
      <UploadButton onUploaded={onUploaded} />
      <nav className="sidebar__nav">
        {TABS.map((tab) => (
          <button
            key={tab}
            className={`sidebar__nav-item${tab === activeTab ? ' active' : ''}`}
            onClick={() => navigate(tab === 'Active' ? '/' : `/${TAB_SLUGS[tab]}`)}
          >
            {tab}
          </button>
        ))}
      </nav>
    </aside>
  )
}

export default Sidebar

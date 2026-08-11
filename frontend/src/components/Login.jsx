import { api } from '../api.js'
import logo from '../assets/logo.png'

// Shown instead of the whole app when GET /auth/me comes back 401 — see
// App.jsx. There's nothing to fill in here; signing in is a real
// redirect to Dropbox's own consent screen (see api.goToLogin()), not a
// form this app collects anything through.
function Login() {
  return (
    <div className="login-screen">
      <div className="login-card">
        <img src={logo} alt="" className="login-card__logo" />
        <h1>Research Tool</h1>
        <p>Sign in with your Your Firm Dropbox account to continue.</p>
        <button className="login-card__button" onClick={api.goToLogin}>
          Sign in with Dropbox
        </button>
      </div>
    </div>
  )
}

export default Login

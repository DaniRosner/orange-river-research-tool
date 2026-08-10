// Vite/React entry point — mounts the <App> component into the empty
// <div id="root"> in index.html. There's normally no reason to touch this
// file; actual app logic starts in App.jsx.
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

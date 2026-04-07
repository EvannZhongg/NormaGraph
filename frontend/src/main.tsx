import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'sonner'

import App from './App'
import './index.css'
import { useAppStore } from './store/app-store'

const basename = import.meta.env.BASE_URL.replace(/\/$/, '')

function RootApp() {
  const theme = useAppStore((state) => state.theme)

  return (
    <BrowserRouter basename={basename}>
      <App />
      <Toaster richColors position="top-right" theme={theme} />
    </BrowserRouter>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootApp />
  </React.StrictMode>,
)

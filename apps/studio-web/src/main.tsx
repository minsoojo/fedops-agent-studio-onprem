import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './app/App'
import AppErrorBoundary from './app/AppErrorBoundary'
import { StudioProvider } from './app/StudioProvider'
import { installFedOpsFavicon } from './favicon'
import './index.css'

installFedOpsFavicon()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <StudioProvider>
        <App />
      </StudioProvider>
    </AppErrorBoundary>
  </React.StrictMode>,
)

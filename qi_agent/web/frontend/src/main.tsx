import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { readThemeMode, resolveTheme } from './theme'
import './style.css'

const initialTheme = resolveTheme(readThemeMode())
document.documentElement.dataset.theme = initialTheme
document.documentElement.style.colorScheme = initialTheme

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

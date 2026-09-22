import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Latin-only subsets: this portal doesn't need Cyrillic/Greek/Vietnamese
// glyphs, and importing the unscoped 400.css etc. pulls in every subset.
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

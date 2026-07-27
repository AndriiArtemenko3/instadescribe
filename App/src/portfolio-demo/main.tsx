import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/geist'
import '../index.css'
import './demo.css'
import { DemoRoot } from './DemoRoot'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DemoRoot />
  </StrictMode>,
)

import React from 'react'
import {createRoot} from 'react-dom/client'
import {App} from './App'
import '../vendor/harness/packages/client/ui-theme/src/styles/base.css'
import '../vendor/harness/packages/client/ui-theme/src/styles/design-platform.css'
import '../vendor/harness/packages/client/ui-theme/src/styles/scrollbar.css'
import '../vendor/harness/packages/client/ui-theme/src/styles/shiki.css'
import './finance.css'

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)

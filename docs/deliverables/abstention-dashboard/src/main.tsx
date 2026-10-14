import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';

/**
 * 应用入口 —— 挂载 <App/> 到 #root。
 */

const container = document.getElementById('root');
if (container === null) {
  throw new Error('找不到挂载节点 #root');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>
);

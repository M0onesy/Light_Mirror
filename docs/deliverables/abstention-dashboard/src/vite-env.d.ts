/// <reference types="vite/client" />

/**
 * Vite 客户端类型声明 —— 支持 `import.meta.env` 与 JSON 模块导入。
 * （`resolveJsonModule` 已在 tsconfig 开启，此处仅补齐 Vite 特有类型。）
 */

interface ImportMetaEnv {
  readonly DEV: boolean;
  readonly PROD: boolean;
  readonly MODE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/**
 * 跨端跳转工具 —— 看板 → 口径澄清工具的 URL 契约（DESIGN §4.4 / §5）。
 *
 * 约定：两端同源访问，都以「同一静态根」下的相对路径互跳。
 * - 默认相对路径 `../scope-clarifier/index.html`；
 * - 可用环境变量 `VITE_CLARIFIER_URL` 覆盖（用于看板单独部署到其它根路径时）。
 *
 * 使用 `<a target="_blank" rel="noopener">` 打开，避免 `window.open` 被拦截。
 */

/** 口径澄清工具 URL（默认相对路径，可用 VITE_CLARIFIER_URL 覆盖）。 */
export const CLARIFIER_URL: string =
  import.meta.env.VITE_CLARIFIER_URL ?? '../scope-clarifier/index.html';

/** URL 查询参数契约：`filter`（X1..X6）与 `cause`（scope/definition/mention/premise）。 */
export interface ClarifierParams {
  /** 预置筛选（X 码）。 */
  filter?: string;
  /** 根因关键词（本次实际启用 `scope`）。 */
  cause?: string;
}

/**
 * 生成带参的澄清工具 href。
 *
 * @param params 可选查询参数；缺省时返回不带参的澄清工具 URL。
 * @returns 形如 `../scope-clarifier/index.html?filter=X3&cause=scope` 的字符串。
 *
 * 说明：仅做最小 URL 编码，保证 `filter=X3&cause=scope` 字面契约稳定可测
 * （避免 `URLSearchParams` 把空格编码为 `+`）。
 */
export function clarifierHref(params?: ClarifierParams): string {
  if (params === undefined) {
    return CLARIFIER_URL;
  }
  const pairs: string[] = [];
  if (params.filter !== undefined && params.filter !== '') {
    pairs.push(`filter=${encodeURIComponent(params.filter)}`);
  }
  if (params.cause !== undefined && params.cause !== '') {
    pairs.push(`cause=${encodeURIComponent(params.cause)}`);
  }
  if (pairs.length === 0) {
    return CLARIFIER_URL;
  }
  const sep = CLARIFIER_URL.includes('?') ? '&' : '?';
  return `${CLARIFIER_URL}${sep}${pairs.join('&')}`;
}

export default clarifierHref;

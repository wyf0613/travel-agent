/* 前端配置（GitHub Pages 静态部署版）。
 *
 * 高德 JS API Key 按机制必须下发到浏览器，无法真正隐藏。
 * 上线前请在高德控制台为该 Key 配置「域名白名单」（含 wyf0613.github.io）
 * 并设置每日调用配额上限，避免被盗刷。
 */
window.AMAP_WEB_KEY = "8c3bc8245be2bcb62a9f58057c114c73";
window.AMAP_SECURITY_CODE = "3791cea36c409aebfc2b748b13665a08";

/* 后端接口地址：
 * GitHub Pages 是纯静态托管，没有后端。设为空串时请求走相对路径 /api/...，
 * 会 404，从而触发 app.js 内置的 demo-data.js 演示快照兜底。
 * 本地开发若起了后端，可在浏览器控制台手动设置 window.API_BASE 覆盖。
 */
window.API_BASE = "";

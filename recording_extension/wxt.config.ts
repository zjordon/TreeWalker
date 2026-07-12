import { defineConfig } from 'wxt';

// TreeWalker Recorder 扩展配置。
// 不含 'debugger' 权限 —— 全对齐指纹由 Python 后端经 remote-debugging-port CDP 负责。
export default defineConfig({
  manifest: {
    name: 'TreeWalker Recorder',
    description: '录制用户操作 → TreeWalker 历史重放',
    version: '0.1.0',
    permissions: ['activeTab', 'tabs', 'scripting', 'storage'],
    host_permissions: ['http://localhost:8765/*', 'http://*/*', 'https://*/*'],
    // MAIN-world 注入脚本（hook SPA pushState/replaceState）需对页面可访问
    web_accessible_resources: [{ resources: ['injected.js'], matches: ['<all_urls>'] }],
  },
});

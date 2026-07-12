// SPA 导航采集：注入 MAIN-world 脚本 hook pushState/replaceState（发 tw:nav），
// 并直接监听 popstate/hashchange（content 能收到的标准事件）→ 统一发 navigate(url)。
// lastUrl 去重：pushState hook 与 popstate/hashchange 不会对同一 URL 重复发。
//
// go_back 折叠：popstate 无法可靠区分「后退按钮」与「SPA 回退」，统一记 navigate(url)
// （重放落点同为某 URL，且 navigate(url) 比 go_back 依赖历史栈更稳）。

import type { RecorderEvent } from '../shared/types';

interface InstallOptions {
  sendEvent: (event: RecorderEvent) => void;
}

export function installNavigationRecorder(opts: InstallOptions): () => void {
  const { sendEvent } = opts;

  // 注入 MAIN-world hook（<script src>，web_accessible_resources 已声明 injected.js）
  try {
    const s = document.createElement('script');
    s.src = browser.runtime.getURL('injected.js');
    s.onload = () => s.remove();
    (document.head || document.documentElement).append(s);
  } catch {
    /* 某些页面（about:blank 等）无 head，忽略注入失败 */
  }

  let lastUrl = location.href;
  const onNav = () => {
    const url = location.href;
    if (url === lastUrl) return; // 去重（hook 与 popstate 可能都触发）
    lastUrl = url;
    console.log('[TW Recorder] navigate %s', url);
    sendEvent({ type: 'navigate', params: { url }, ts: Date.now() });
  };

  // tw:nav：MAIN-world 的 pushState/replaceState hook 派发（content 能收到，共享 window）
  window.addEventListener('tw:nav', onNav);
  // popstate/hashchange：content 直接监听（后退/前进/锚点变化）
  window.addEventListener('popstate', onNav);
  window.addEventListener('hashchange', onNav);

  return () => {
    window.removeEventListener('tw:nav', onNav);
    window.removeEventListener('popstate', onNav);
    window.removeEventListener('hashchange', onNav);
  };
}

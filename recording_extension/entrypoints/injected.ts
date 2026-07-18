// MAIN-world 注入脚本：hook history.pushState/replaceState，派发 tw:nav CustomEvent。
// content script（ISOLATED world）无法覆盖页面的 history 方法（两 world 各有一份），
// 必须把 hook 注入到页面同一 world（MAIN）才能拦截 SPA 路由的 pushState/replaceState。
// content script 经 <script src=injected.js> 注入本文件；跨 world 通信用 window 上的
// CustomEvent（两 world 共享同一个 window，事件互通）。popstate/hashchange 由 content
// script 直接监听（标准事件，content 能收到），故这里只 wrap pushState/replaceState。

export default defineUnlistedScript(() => {
  const w = window as unknown as { __twNavHooked?: boolean };
  if (w.__twNavHooked) return; // 防重复注入（多次注入脚本标签时只 wrap 一次）
  w.__twNavHooked = true;

  const dispatch = () =>
    window.dispatchEvent(new CustomEvent('tw:nav', { detail: { url: location.href } }));

  const wrap = (key: 'pushState' | 'replaceState') => {
    const orig = history[key] as History['pushState'];
    history[key] = function (...args: Parameters<typeof orig>) {
      const ret = orig.apply(this, args);
      dispatch();
      return ret;
    } as History['pushState'];
  };
  wrap('pushState');
  wrap('replaceState');
});

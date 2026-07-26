// MAIN-world 注入脚本：hook history.pushState/replaceState，派发 tw:nav CustomEvent。
// content script（ISOLATED world）无法覆盖页面的 history 方法（两 world 各有一份），
// 必须把 hook 注入到页面同一 world（MAIN）才能拦截 SPA 路由的 pushState/replaceState。
// content script 经 <script src=injected.js> 注入本文件；跨 world 通信用 window 上的
// CustomEvent（两 world 共享同一个 window，事件互通）。popstate/hashchange 由 content
// script 直接监听（标准事件，content 能收到），故这里只 wrap pushState/replaceState。
//
// 另：wrap EventTarget.prototype.addEventListener——给注册了 click/mousedown/pointerdown 监听器
// 的元素打 data-tw-jsclick DOM 标记。content script 看不到 addEventListener 注册的监听器（盲区），
// 导致 B 站封面 slot tab（纯 addEventListener、cursor=auto、无 role/onclick）的 click 被
// findInteractiveAncestor 当噪声丢掉。MAIN-world 这里能拦到页面 addEventListener，打标记后 ISOLATED
// world 的 findInteractiveAncestor 认标记（DOM 属性跨 world 共享）。对齐后端 has_js_click_listener。
// 详见 docs/user_recording/js-click-capture-fix-plan.md。

export default defineUnlistedScript(() => {
  const w = window as unknown as { __twNavHooked?: boolean; __twAELHooked?: boolean };

  // ── history hook（SPA 导航）── 防重复注入（多次注入脚本标签时只 wrap 一次）
  if (!w.__twNavHooked) {
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
  }

  // ── addEventListener hook（补 content script 看不到 JS 监听器的盲区）──
  if (!w.__twAELHooked) {
    w.__twAELHooked = true;
    const origAEL = EventTarget.prototype.addEventListener;
    const patchedAEL = function (this: EventTarget, type: string, listener: unknown, options?: unknown) {
      // 只标"点击类 affordance"事件 + 仅 Element（排除 window/document）。打 DOM 属性跨 world 可见。
      if ((type === 'click' || type === 'mousedown' || type === 'pointerdown') && this instanceof Element) {
        try {
          (this as Element).setAttribute('data-tw-jsclick', '1');
        } catch {
          /* SVG/匿名等罕见无 setAttribute 容错 */
        }
      }
      return origAEL.call(this, type, listener as EventListenerOrEventListenerObject | null, options as boolean | AddEventListenerOptions);
    };
    EventTarget.prototype.addEventListener = patchedAEL as typeof EventTarget.prototype.addEventListener;
  }
});

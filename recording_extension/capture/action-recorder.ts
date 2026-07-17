// Action 采集器 —— 借鉴 Browser-BC capture/action-recorder.ts，针对 TreeWalker 重放适配。
// 三类采集：
//   1. click：target 向上找可交互祖先（button/a/[role]），对齐 TreeWalker selector_map。
//   2. input/keyup：标准 <input>/<textarea>（value）。
//   3. contenteditable 富文本（如 Slate，内部 span[data-leaf]/[data-string] 结构）：
//      Slate 用 beforeinput 接管输入、标准 input 事件不派发 → 用 MutationObserver 直接观察
//      textContent 变化（不依赖事件传播，最可靠）。
//
// 结构对齐 Browser-BC：on() 工厂 + cleanup 收集器、emit() 统一填 ts；IME compositionstart/end
// 抑制 composing 中的 input，只录最终值。

import type { RecorderEvent } from '../shared/types';
import { buildElementRef } from './selector';

interface InstallOptions {
  sendEvent: (event: RecorderEvent) => void;
  /** 动作发出时通知（传 ts），供 SideEffectObserver.markAction 打开观察窗口。 */
  onAction?: (ts: number) => void;
}

/** 可交互元素选择器（对齐 TreeWalker selector_map 的可交互元素范围）。 */
const INTERACTIVE_SELECTOR = [
  'a[href]', 'button', 'input', 'select', 'textarea', 'summary', 'label',
  '[contenteditable]',
  '[role="button"]', '[role="link"]', '[role="textbox"]',
  '[role="menuitem"]', '[role="tab"]', '[role="checkbox"]', '[role="radio"]',
  '[role="switch"]', '[role="option"]',
].join(',');

/** 命名非打印键（send_keys 录这些；可打印字符归 input_text）。F1-F12 用正则另判。 */
const NAMED_KEYS = new Set([
  'Enter', 'Tab', 'Escape', 'Backspace', 'Delete',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'Home', 'End', 'PageUp', 'PageDown',
]);

/** 编辑键（无修饰符时归 input 最终值，不发 send_keys）：删除/移动光标的按键，其效果
 *  已反映在后续 input 事件的 value 里；单独发 send_keys 会 flushInput 打断 400ms 合并，
 *  把一次输入切成多步。Ctrl+Backspace 等组合键仍走 send_keys（hasMod 判断在前）。 */
const EDIT_KEYS = new Set([
  'Backspace', 'Delete',
  'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown',
  'Home', 'End', 'PageUp', 'PageDown',
]);

const INPUT_COALESCE_MS = 400;
const SCROLL_IDLE_MS = 500;
let installed = false;

/** 从 el 向上找最近的可交互祖先；找不到回退到 el 本身。 */
function findInteractiveAncestor(el: Element | null): Element | null {
  let cur: Element | null = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE_SELECTOR)) return cur;
      // 对齐后端 is_interactive 规则 14/10：cursor:pointer / onclick 属性。
      // INTERACTIVE_SELECTOR 不含 div 模拟组件，Semi select 触发器 / 抖音 cover-Jg3T4p 等
      // cursor:pointer div 靠这俩识别，否则会录到内部 span/div，回放点击不开 modal。
      // 但 cursor:pointer 只对 div 检查——span/svg 等内联元素常继承父按钮的 cursor:pointer，
      // 若命中会阻断向上找真正的 button/input（实测点上传按钮录到内部 span → locate 失败）。
      if (cur.tagName === 'DIV' && window.getComputedStyle(cur).cursor === 'pointer') return cur;
      const html = cur as HTMLElement;
      if (html.onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
    } catch {
      /* 非元素节点，继续向上 */
    }
    cur = cur.parentElement;
  }
  return el;
}

/** 取元素的原始定位属性（后端 ATTRIBUTE 级兜底用）。 */
function refAttrs(el: Element): Pick<RecorderEvent, 'tag' | 'id' | 'name' | 'ariaLabel' | 'role'> {
  return {
    tag: el.tagName.toLowerCase(),
    id: el.getAttribute('id') ?? undefined,
    name: el.getAttribute('name') ?? undefined,
    ariaLabel: el.getAttribute('aria-label') ?? undefined,
    role: el.getAttribute('role') ?? undefined,
  };
}

interface PendingInput {
  xpath: string;
  value: string;
  attrs: ReturnType<typeof refAttrs>;
  timer: number;
}

/** 取 target 的当前值（input/textarea → value；contenteditable → innerText，对齐 Browser-BC
 *  valueFor——比 textContent 更接近用户可见文本，去掉 Slate 零宽字符等噪声）。 */
function readValue(target: Element): string {
  const html = target as HTMLElement;
  if (html.isContentEditable) return html.innerText ?? '';
  if ('value' in target) return String((target as HTMLInputElement).value);
  return '';
}

export function installActionRecorder(opts: InstallOptions): () => void {
  if (installed) return () => {};
  installed = true;
  const { sendEvent, onAction } = opts;

  let pendingInput: PendingInput | null = null;
  // IME（中文等输入法）composing 中——抑制 input/MutationObserver 的 setPending，
  // 避免中间拼音值被录（只录 compositionend 后的最终值）。
  let isComposing = false;

  /** 统一发送：填 ts。对齐 Browser-BC emit()。同时通知 SideEffectObserver 开观察窗口。 */
  const emit = (partial: Omit<RecorderEvent, 'ts'>) => {
    const ts = Date.now();
    sendEvent({ ...partial, ts });
    onAction?.(ts); // 通知副作用观察器：动作已发，开启 1s 观察窗口
  };

  const flushInput = () => {
    if (!pendingInput) return;
    const { xpath, value, attrs } = pendingInput;
    clearTimeout(pendingInput.timer);
    pendingInput = null;
    if (!value) return; // 空值不发（避免噪声 input_text 步，如框被清空后 flush）
    console.log('[TW Recorder] flush input_text value=%s', JSON.stringify(value.slice(0, 30)));
    emit({
      type: 'input_text',
      xpath,
      params: { text: value, clear: true },
      ...attrs,
    });
  };

  /** 合并/启动 pendingInput（同 xpath 连续输入合并，延迟 400ms 发最终值）。 */
  const setPending = (target: Element) => {
    if (isComposing) return; // IME composing 中——等 compositionend 后的最终值
    const ref = buildElementRef(target);
    const value = readValue(target);
    if (pendingInput && pendingInput.xpath === ref.xpath) {
      pendingInput.value = value;
      pendingInput.attrs = refAttrs(target);
      clearTimeout(pendingInput.timer);
    } else {
      flushInput();
      pendingInput = { xpath: ref.xpath, value, attrs: refAttrs(target), timer: 0 };
    }
    pendingInput.timer = setTimeout(flushInput, INPUT_COALESCE_MS) as unknown as number;
  };

  const onClick = (e: Event) => {
    flushInput();
    const raw = e.composedPath()[0] as Element || (e.target as Element | null);
    if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
    // file input 的 click 几乎都是上传按钮 JS 触发的 input.click()（非用户直接点），
    // 后续 change 会录 upload_file；跳过避免冗余 click（回放 upload_file 不需要先点）。
    if (raw.tagName === 'INPUT' && (raw.getAttribute('type') || '').toLowerCase() === 'file') return;
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    emit({
      type: 'click',
      xpath: ref.xpath,
      rect: ref.rect,
      ...refAttrs(target),
    });
  };

  const onInput = (e: Event) => {
    const raw = e.composedPath()[0] as Element || (e.target as Element | null);
    if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
    // file input 选文件后 value 变为 C:\fakepath\<名>，不是用户文本输入 → 交给 onFileChange 录
    // upload_file。radio/checkbox 勾选时浏览器也派发 input 事件（checked 变化），但语义是"勾选"
    // 不是"文本输入"，由 onClick 录 click；这里都跳过，避免把勾选误录成 input_text
    // （否则 checkbox 重放会被 input_text 的 clear/type 干扰，toggle 回未选状态）。
    if (raw.tagName === 'INPUT') {
      const t = (raw.getAttribute('type') || '').toLowerCase();
      if (t === 'file' || t === 'radio' || t === 'checkbox') return;
    }
    const target = findInteractiveAncestor(raw) ?? raw;
    console.log('[TW Recorder] %s on <%s> ce=%s', e.type, target.tagName, (target as HTMLElement).isContentEditable);
    setPending(target);
  };

  // ── select_dropdown：<select> 的 change → 选中项 value（对齐重放侧 value 属性匹配）──
  const onSelect = (e: Event) => {
    const raw = e.target as Element | null;
    if (!raw || raw.tagName !== 'SELECT') return;
    flushInput();
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    const value = (target as HTMLSelectElement).value;
    console.log('[TW Recorder] select_dropdown value=%s', value);
    emit({
      type: 'select_dropdown',
      xpath: ref.xpath,
      ...refAttrs(target),
      params: { value },
    });
  };

  // ── send_keys：仅 ctrl/alt/meta 组合键 + 命名非打印键；可打印字符归 input_text ──
  const onKeyDown = (e: Event) => {
    const ke = e as KeyboardEvent;
    if (ke.repeat) return;
    const k = ke.key;
    // 裸修饰键按下（如只按 Shift）等组合，不发
    if (k === 'Control' || k === 'Alt' || k === 'Shift' || k === 'Meta') return;
    const hasMod = ke.ctrlKey || ke.altKey || ke.metaKey; // Shift 不计入
    // 编辑键（Backspace/Delete/方向/Home/End/PageUp/Down）无修饰符时归 input 最终值——
    // 其删除/移动效果已反映在后续 input 事件的 value 里；单独发会 flushInput 打断 400ms 合并。
    if (!hasMod && EDIT_KEYS.has(k)) return;
    const isNamed = NAMED_KEYS.has(k) || /^F([1-9]|10|11|12)$/.test(k);
    if (!hasMod && !isNamed) return; // 普通可打印 / Shift+字符 / IME Process → 由 input 处理
    flushInput();
    const mods: string[] = [];
    if (ke.ctrlKey) mods.push('Control');
    if (ke.altKey) mods.push('Alt');
    if (ke.shiftKey && hasMod) mods.push('Shift'); // Shift 仅在配合 ctrl/alt/meta 时计入
    if (ke.metaKey) mods.push('Meta');
    const keys = (mods.length ? [...mods, k] : [k]).join('+');
    console.log('[TW Recorder] send_keys %s', keys);
    emit({ type: 'send_keys', params: { keys } });
  };

  // ── upload_file：<input type=file> 的 change → 文件名（浏览器安全限制只给文件名）──
  const onFileChange = (e: Event) => {
    const raw = e.target as Element | null;
    if (!raw || raw.tagName !== 'INPUT' || (raw.getAttribute('type') || '').toLowerCase() !== 'file') return;
    const file = (raw as HTMLInputElement).files?.[0];
    if (!file) return;
    flushInput();
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    // accept 在 change 瞬间从真实 file input 读（页面此刻还没因上传跳转）。
    // 后端据此 + xpath 落盘签名，重放端按 accept+xpath 解析——彻底不依赖「导航后 get_state」
    // 定位（选完视频抖音立即 /upload→/post/video 跳转，get_state 会抓到跳转后页面致 file input 错位）。
    const accept = raw.getAttribute('accept') ?? '';
    console.log('[TW Recorder] upload_file name=%s accept=%s', file.name, accept);
    emit({
      type: 'upload_file',
      xpath: ref.xpath,
      ...refAttrs(target),
      params: { path: file.name, accept },
    });
  };

  // ── IME：compositionstart/end 维护 isComposing flag（composing 中 setPending 抑制）──
  const onCompositionStart = () => { isComposing = true; };
  const onCompositionEnd = () => { isComposing = false; };

  // ── scroll：wheel 累计 → 一次 scroll(amount, direction)；方向反转/空闲 500ms flush ──
  let scrollY = 0; // 累计 deltaY（带符号）
  let scrollTimer = 0;
  const flushScroll = () => {
    if (scrollTimer) {
      clearTimeout(scrollTimer);
      scrollTimer = 0;
    }
    if (scrollY === 0) return;
    const vh = window.innerHeight || 1;
    const amount = Math.max(1, Math.min(10, Math.round(Math.abs(scrollY) / vh)));
    const direction = scrollY > 0 ? 'down' : 'up';
    scrollY = 0;
    console.log('[TW Recorder] scroll %s amount=%d', direction, amount);
    emit({ type: 'scroll', params: { amount, direction } });
  };
  const onWheel = (e: Event) => {
    const we = e as WheelEvent;
    // 方向反转：先冲刷上一段（避免 up/down 互相抵消成 amount=0），再累计新方向
    if (scrollY !== 0 && Math.sign(we.deltaY) !== Math.sign(scrollY)) flushScroll();
    scrollY += we.deltaY;
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(flushScroll, SCROLL_IDLE_MS) as unknown as number;
  };

  // ── on() 工厂 + cleanup 收集器（对齐 Browser-BC，替代手动 add/removeEventListener）──
  const cleanup: Array<() => void> = [];
  const on = (type: string, listener: (e: Event) => void) => {
    window.addEventListener(type, listener, { capture: true, passive: true });
    cleanup.push(() => window.removeEventListener(type, listener, { capture: true } as EventListenerOptions));
  };

  on('click', onClick);
  on('input', onInput);
  on('keyup', onInput);
  on('change', onSelect);
  on('keydown', onKeyDown);
  on('change', onFileChange);
  on('wheel', onWheel);
  on('compositionstart', onCompositionStart);
  on('compositionend', onCompositionEnd);

  // contenteditable 富文本（Slate 等）：标准 input 事件不派发，用 MutationObserver 直接观察
  // textContent 变化。content script 的 MutationObserver 观察 DOM（共享），不依赖事件传播。
  const ceObservers: MutationObserver[] = [];
  const observeCe = (el: Element) => {
    if (ceObservers.some((o) => (o as unknown as { _el?: Element })._el === el)) return;
    const mo = new MutationObserver(() => {
      if (isComposing) return; // IME composing 中——等 compositionend 后的最终值
      console.log('[TW Recorder] ce-mutation on <%s> value=%s', el.tagName, JSON.stringify((el.innerText ?? '').slice(0, 30)));
      setPending(el);
    });
    (mo as unknown as { _el?: Element })._el = el;
    mo.observe(el, { subtree: true, characterData: true, childList: true });
    ceObservers.push(mo);
  };
  document.querySelectorAll('[contenteditable]').forEach(observeCe);
  // 监听动态新增的 contenteditable（SPA 弹出编辑器）
  const docObs = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of Array.from(m.addedNodes)) {
        if (n.nodeType === Node.ELEMENT_NODE) {
          const el = n as Element;
          if (el.matches?.('[contenteditable]')) observeCe(el);
          el.querySelectorAll?.('[contenteditable]').forEach(observeCe);
        }
      }
    }
  });
  docObs.observe(document.body, { subtree: true, childList: true });

  return () => {
    flushInput();
    flushScroll();
    cleanup.splice(0).forEach((d) => d());
    ceObservers.forEach((o) => o.disconnect());
    docObs.disconnect();
    installed = false;
  };
}

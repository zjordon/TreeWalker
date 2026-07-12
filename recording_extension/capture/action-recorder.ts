// Action 采集器 —— 借鉴 Browser-BC capture/action-recorder.ts，针对 TreeWalker 重放适配。
// 三类采集：
//   1. click：target 向上找可交互祖先（button/a/[role]），对齐 TreeWalker selector_map。
//   2. input/keyup：标准 <input>/<textarea>（value）。
//   3. contenteditable 富文本（如 Slate，内部 span[data-leaf]/[data-string] 结构）：
//      Slate 用 beforeinput 接管输入、标准 input 事件不派发 → 用 MutationObserver 直接观察
//      textContent 变化（不依赖事件传播，最可靠）。

import type { RecorderEvent } from '../shared/types';
import { buildElementRef } from './selector';

interface InstallOptions {
  sendEvent: (event: RecorderEvent) => void;
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

/** 从 el 向上找最近的可交互祖先；找不到回退到 el 本身。 */
function findInteractiveAncestor(el: Element | null): Element | null {
  let cur: Element | null = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE_SELECTOR)) return cur;
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

let pendingInput: PendingInput | null = null;
const INPUT_COALESCE_MS = 400;
let installed = false;

function flushInput(sendEvent: (event: RecorderEvent) => void): void {
  if (!pendingInput) return;
  const { xpath, value, attrs } = pendingInput;
  clearTimeout(pendingInput.timer);
  pendingInput = null;
  console.log('[TW Recorder] flush input_text value=%s', JSON.stringify(value.slice(0, 30)));
  sendEvent({
    type: 'input_text',
    xpath,
    params: { text: value, clear: true },
    ...attrs,
    ts: Date.now(),
  });
}

/** 取 target 的当前值（input/textarea → value；contenteditable → textContent）。 */
function readValue(target: Element): string {
  if ((target as HTMLElement).isContentEditable) return target.textContent ?? '';
  if ('value' in target) return String((target as HTMLInputElement).value);
  return '';
}

/** 合并/启动 pendingInput（同 xpath 连续输入合并，延迟 400ms 发最终值）。 */
function setPending(sendEvent: (event: RecorderEvent) => void, target: Element): void {
  const ref = buildElementRef(target);
  const value = readValue(target);
  if (pendingInput && pendingInput.xpath === ref.xpath) {
    pendingInput.value = value;
    pendingInput.attrs = refAttrs(target);
    clearTimeout(pendingInput.timer);
  } else {
    flushInput(sendEvent);
    pendingInput = { xpath: ref.xpath, value, attrs: refAttrs(target), timer: 0 };
  }
  pendingInput.timer = setTimeout(() => flushInput(sendEvent), INPUT_COALESCE_MS);
}

export function installActionRecorder(opts: InstallOptions): () => void {
  if (installed) return () => {};
  installed = true;
  const { sendEvent } = opts;

  const onClick = (e: Event) => {
    flushInput(sendEvent);
    const raw = e.composedPath()[0] as Element || (e.target as Element | null);
    if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    sendEvent({
      type: 'click',
      xpath: ref.xpath,
      rect: ref.rect,
      ...refAttrs(target),
      ts: Date.now(),
    });
  };

  const onInput = (e: Event) => {
    const raw = (e.composedPath()[0] as Element) || (e.target as Element | null);
    if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
    const target = findInteractiveAncestor(raw) ?? raw;
    console.log('[TW Recorder] %s on <%s> ce=%s', e.type, target.tagName, (target as HTMLElement).isContentEditable);
    setPending(sendEvent, target);
  };

  // ── select_dropdown：<select> 的 change → 选中项 value（对齐重放侧 value 属性匹配）──
  const onSelect = (e: Event) => {
    const raw = e.target as Element | null;
    if (!raw || raw.tagName !== 'SELECT') return;
    flushInput(sendEvent);
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    const value = (target as HTMLSelectElement).value;
    console.log('[TW Recorder] select_dropdown value=%s', value);
    sendEvent({
      type: 'select_dropdown',
      xpath: ref.xpath,
      ...refAttrs(target),
      params: { value },
      ts: Date.now(),
    });
  };

  // ── send_keys：仅组合键(Ctrl/Alt/Meta) + 命名非打印键；可打印字符归 input_text ──
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.repeat) return;
    const k = e.key;
    // 裸修饰键按下（如只按 Shift）等组合，不发
    if (k === 'Control' || k === 'Alt' || k === 'Shift' || k === 'Meta') return;
    const mods: string[] = [];
    if (e.ctrlKey) mods.push('Control');
    if (e.altKey) mods.push('Alt');
    if (e.shiftKey) mods.push('Shift');
    if (e.metaKey) mods.push('Meta');
    const isNamed = NAMED_KEYS.has(k) || /^F([1-9]|10|11|12)$/.test(k);
    if (mods.length === 0 && !isNamed) return; // 普通可打印字符 → 由 input 处理走 input_text
    flushInput(sendEvent);
    const keys = (mods.length ? [...mods, k] : [k]).join('+');
    console.log('[TW Recorder] send_keys %s', keys);
    sendEvent({ type: 'send_keys', params: { keys }, ts: Date.now() });
  };

  // ── upload_file：<input type=file> 的 change → 文件名（浏览器安全限制只给文件名）──
  const onFileChange = (e: Event) => {
    const raw = e.target as Element | null;
    if (!raw || raw.tagName !== 'INPUT' || (raw.getAttribute('type') || '').toLowerCase() !== 'file') return;
    const file = (raw as HTMLInputElement).files?.[0];
    if (!file) return;
    flushInput(sendEvent);
    const target = findInteractiveAncestor(raw) ?? raw;
    const ref = buildElementRef(target);
    console.log('[TW Recorder] upload_file name=%s', file.name);
    sendEvent({
      type: 'upload_file',
      xpath: ref.xpath,
      ...refAttrs(target),
      params: { path: file.name },
      ts: Date.now(),
    });
  };

  // ── scroll：wheel 累计 → 一次 scroll(amount, direction)；方向反转/空闲 500ms flush ──
  let scrollY = 0; // 累计 deltaY（带符号）
  let scrollTimer = 0;
  const SCROLL_IDLE_MS = 500;
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
    sendEvent({ type: 'scroll', params: { amount, direction }, ts: Date.now() });
  };
  const onWheel = (e: WheelEvent) => {
    // 方向反转：先冲刷上一段（避免 up/down 互相抵消成 amount=0），再累计新方向
    if (scrollY !== 0 && Math.sign(e.deltaY) !== Math.sign(scrollY)) flushScroll();
    scrollY += e.deltaY;
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(flushScroll, SCROLL_IDLE_MS) as unknown as number;
  };

  window.addEventListener('click', onClick, { capture: true, passive: true });
  window.addEventListener('input', onInput, { capture: true, passive: true });
  window.addEventListener('keyup', onInput, { capture: true, passive: true });
  window.addEventListener('change', onSelect, { capture: true, passive: true });
  window.addEventListener('keydown', onKeyDown, { capture: true, passive: true });
  window.addEventListener('change', onFileChange, { capture: true, passive: true });
  window.addEventListener('wheel', onWheel, { capture: true, passive: true });

  // contenteditable 富文本（Slate 等）：标准 input 事件不派发，用 MutationObserver 直接观察
  // textContent 变化。content script 的 MutationObserver 观察 DOM（共享），不依赖事件传播。
  const ceObservers: MutationObserver[] = [];
  const observeCe = (el: Element) => {
    if (ceObservers.some((o) => (o as unknown as { _el?: Element })._el === el)) return;
    const mo = new MutationObserver(() => {
      console.log('[TW Recorder] ce-mutation on <%s> value=%s', el.tagName, JSON.stringify((el.textContent ?? '').slice(0, 30)));
      setPending(sendEvent, el);
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
    flushInput(sendEvent);
    flushScroll();
    window.removeEventListener('click', onClick, { capture: true } as EventListenerOptions);
    window.removeEventListener('input', onInput, { capture: true } as EventListenerOptions);
    window.removeEventListener('keyup', onInput, { capture: true } as EventListenerOptions);
    window.removeEventListener('change', onSelect, { capture: true } as EventListenerOptions);
    window.removeEventListener('keydown', onKeyDown, { capture: true } as EventListenerOptions);
    window.removeEventListener('change', onFileChange, { capture: true } as EventListenerOptions);
    window.removeEventListener('wheel', onWheel, { capture: true } as EventListenerOptions);
    ceObservers.forEach((o) => o.disconnect());
    docObs.disconnect();
    installed = false;
  };
}

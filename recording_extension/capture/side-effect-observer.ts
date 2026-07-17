// 副作用观察器 —— 检测动作引发的 DOM 变化（modal/dropdown 打开），作为后端 signal detection
// 的实时补充（DOM 变化瞬间捕获，比后端 get_state 对比更及时）。设计见 redesign.md §4.2。
//
// 仅在动作后 1s 窗口内观察（markAction 打时间戳），避免页面自身非用户触发的 DOM 变化误报。
// 检测到新增的 modal/dropdown 节点 → 发 SignalEvent，后端 attach_signal 附到最近动作。
// rule_file_upload 据 modal_opened signal 判「前置 click 打开了编辑器，绝非上传按钮」→ 不吸收。

import type { SignalEvent } from '../shared/types';

interface InstallOptions {
  sendSignal: (signal: SignalEvent) => void;
}

export interface SideEffectHandle {
  uninstall: () => void;
  /** 动作发出时调用，打时间戳开启 1s 观察窗口。 */
  markAction: (ts: number) => void;
}

/** modal 容器选择器（覆盖常见组件库：Semi / Antd / 通用）。 */
const MODAL_SELECTOR =
  '[role="dialog"], [aria-modal="true"], .modal, .ant-modal, .semi-modal, .semi-sidesheet';
/** 下拉选择器。 */
const DROPDOWN_SELECTOR =
  '[role="listbox"], .ant-select-dropdown, .semi-select-option-list, .semi-dropdown, .semi-popover';

/** 仅在动作后这段时间内的 DOM 变化视为副作用（毫秒）。 */
const ACTION_WINDOW_MS = 1000;
/** 同一选择器去重窗（毫秒）——避免一个 modal 多批 mutation 重复发信号。 */
const DEDUPE_WINDOW_MS = 500;

let installed = false;

export function installSideEffectObserver(opts: InstallOptions): SideEffectHandle {
  if (installed) return { uninstall: () => {}, markAction: () => {} };
  installed = true;

  let lastActionTs = 0;
  let lastEmitted = ''; // `${type}:${selector}@${ts-bucket}` 去重

  const observer = new MutationObserver((mutations) => {
    // 只在动作后窗口内观察，抑制页面自身 DOM 变化误报
    if (lastActionTs === 0 || Date.now() - lastActionTs > ACTION_WINDOW_MS) return;

    for (const m of mutations) {
      for (const node of Array.from(m.addedNodes)) {
        if (!(node instanceof HTMLElement)) continue;
        // 新增节点本身是 modal/dropdown，或其内含 modal/dropdown（wrapper 套层）
        detectAndEmit(node, MODAL_SELECTOR, 'modal_opened');
        detectAndEmit(node, DROPDOWN_SELECTOR, 'dropdown_opened');
      }
    }
  });

  const detectAndEmit = (
    root: HTMLElement,
    selector: string,
    type: 'modal_opened' | 'dropdown_opened',
  ) => {
    const target =
      root.matches(selector) ? root : (root.querySelector<HTMLElement>(selector) ?? null);
    if (!target) return;
    const sel = selectorOf(target);
    const now = Date.now();
    const key = `${type}:${sel}`;
    // 同 type+selector 在去重窗内只发一次
    if (lastEmitted === key && now - lastActionTs < DEDUPE_WINDOW_MS) return;
    lastEmitted = key;
    console.log('[TW Recorder] signal %s sel=%s', type, sel);
    opts.sendSignal({ type, selector: sel, ts: now });
  };

  observer.observe(document, { childList: true, subtree: true });

  return {
    uninstall: () => {
      observer.disconnect();
      installed = false;
    },
    markAction: (ts: number) => {
      lastActionTs = ts;
    },
  };
}

/** 简易选择器：tag + #id + 前两个 class（后端只用 selector 作 signal.detail，不参与定位）。 */
function selectorOf(el: HTMLElement): string {
  const parts = [el.tagName.toLowerCase()];
  if (el.id) parts.push(`#${el.id}`);
  const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean) : [];
  for (const c of cls.slice(0, 2)) parts.push(`.${c}`);
  return parts.join('');
}

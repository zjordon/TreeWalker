// Background service worker —— 录制中枢：维护录制状态、转发事件到后端、广播状态给 content。
// MV3 友好：与后端用无状态 HTTP（SW 按需唤醒），不持有 WebSocket 长连接。

import { postEvent, postStart, postStop } from '../shared/backend';
import type { RecorderEvent } from '../shared/types';

const STATE_KEY = 'tw_recording_state';

type Message =
  | { kind: 'event'; event: RecorderEvent }
  | { kind: 'query-state' }
  | { kind: 'start-recording' }
  | { kind: 'stop-recording' };

async function getState(): Promise<{ recording: boolean }> {
  const v = await chrome.storage.local.get(STATE_KEY);
  return { recording: v[STATE_KEY] === true };
}

async function setState(recording: boolean): Promise<void> {
  await chrome.storage.local.set({ [STATE_KEY]: recording });
}

/** 广播状态变化到所有 tab 的 content script（装配/卸载采集器）。 */
async function broadcast(kind: 'recording-started' | 'recording-stopped'): Promise<void> {
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) {
    if (t.id) {
      chrome.tabs.sendMessage(t.id, { kind }).catch(() => {
        /* 页面无 content script（如 chrome://），忽略 */
      });
    }
  }
}

export default defineBackground(() => {
  chrome.runtime.onMessage.addListener((msg: Message, _sender, sendResponse) => {
    (async () => {
      switch (msg.kind) {
        case 'query-state':
          sendResponse(await getState());
          return;
        case 'event': {
          const { recording } = await getState();
          console.log('[TW Recorder] bg recv event type=%s recording=%s', msg.event.type, recording);
          if (recording) await postEvent(msg.event);
          sendResponse({ ok: true });
          return;
        }
        case 'start-recording': {
          const ok = await postStart();
          if (ok) {
            await setState(true);
            await broadcast('recording-started');
          }
          sendResponse({ ok });
          return;
        }
        case 'stop-recording': {
          await setState(false);
          await broadcast('recording-stopped');
          const result = await postStop({ markDone: true, doneText: '录制完成' });
          sendResponse(result);
          return;
        }
        default:
          sendResponse({ ok: false });
      }
    })();
    return true; // 异步 sendResponse
  });

  // ── tab 事件：switch_tab / close_tab ──────────────────────────────────
  // 扩展 chrome.tabs 给的是 Chrome tabId；重放侧要 CDP targetId 后4位。
  // 这里发目标 tab 的 url，后端用 get_tabs() 解析成 target_id[-4:]。
  // onRemoved 时 tab 已销毁取不到 url，故用 tabUrlCache 提前记住。
  const tabUrlCache = new Map<number, string>();

  const postTabEvent = async (type: 'switch_tab' | 'close_tab', url: string, topUrl = false) => {
    const { recording } = await getState();
    if (!recording || !url) return;
    console.log('[TW Recorder] bg tab %s url=%s', type, url);
    // switch_tab 带 top-level url，让后端 _ensure_target 跟随到新 tab（get_state 读新页）
    await postEvent(
      topUrl
        ? { type, url, params: { url }, ts: Date.now() }
        : { type, params: { url }, ts: Date.now() },
    );
  };

  chrome.tabs.onActivated.addListener(async (info) => {
    try {
      const t = await chrome.tabs.get(info.tabId);
      if (t.url) tabUrlCache.set(info.tabId, t.url);
      await postTabEvent('switch_tab', t.url ?? '', true);
    } catch {
      /* tab 已消失 */
    }
  });

  chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
    if (change.url || change.status === 'complete') {
      tabUrlCache.set(tabId, tab.url ?? tabUrlCache.get(tabId) ?? '');
    }
  });

  chrome.tabs.onRemoved.addListener(async (tabId) => {
    const url = tabUrlCache.get(tabId) ?? '';
    tabUrlCache.delete(tabId);
    await postTabEvent('close_tab', url);
  });
});

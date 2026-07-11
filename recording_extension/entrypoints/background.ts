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
});

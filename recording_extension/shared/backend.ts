// 后端 HTTP 通信封装。background 是唯一与后端通信的入口（content 不直连）。
// 后端：aiohttp，默认 http://127.0.0.1:8765（见 examples/record_user_actions.py）。

import type { RecorderEvent, SignalEvent } from './types';

const DEFAULT_ENDPOINT = 'http://127.0.0.1:8765';

export function getEndpoint(): string {
  // 可扩展为从 chrome.storage 读用户配置；MVP 用默认。
  return DEFAULT_ENDPOINT;
}

export async function postStart(): Promise<boolean> {
  try {
    const r = await fetch(`${getEndpoint()}/start`, { method: 'POST' });
    return r.ok;
  } catch (e) {
    console.error('[TW Recorder] /start 失败（后端启动了吗？）', e);
    return false;
  }
}

export async function postEvent(event: RecorderEvent): Promise<boolean> {
  try {
    const r = await fetch(`${getEndpoint()}/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
    });
    return r.ok;
  } catch (e) {
    console.error('[TW Recorder] /event 失败', e);
    return false;
  }
}

/** 副作用信号（modal/dropdown 打开）→ 后端 attach_signal 附到最近动作。 */
export async function postSignal(signal: SignalEvent): Promise<boolean> {
  try {
    const r = await fetch(`${getEndpoint()}/signal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(signal),
    });
    return r.ok;
  } catch (e) {
    console.error('[TW Recorder] /signal 失败', e);
    return false;
  }
}

export async function postStop(opts: {
  filePath?: string;
  markDone?: boolean;
  doneText?: string;
}): Promise<{ ok: boolean; path?: string; steps?: number }> {
  try {
    const r = await fetch(`${getEndpoint()}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: opts.filePath,
        mark_done: opts.markDone ?? false,
        done_text: opts.doneText ?? '',
        success: true,
      }),
    });
    return await r.json();
  } catch (e) {
    console.error('[TW Recorder] /stop 失败', e);
    return { ok: false };
  }
}

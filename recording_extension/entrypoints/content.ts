// Content script —— 装配 action 采集器 + 副作用观察器，把 DOM 事件/信号归一化后发给 background。
// allFrames: true 穿透 iframe（MVP）；runAt: document_idle 不阻塞首屏。

import { installActionRecorder } from '../capture/action-recorder';
import { installNavigationRecorder } from '../capture/navigation-recorder';
import { installSideEffectObserver } from '../capture/side-effect-observer';
import type { RecorderEvent, SignalEvent } from '../shared/types';

export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: true,
  runAt: 'document_idle',
  main() {
    let uninstall: (() => void) | null = null;

    const sendEvent = (event: RecorderEvent) => {
      // 带 url 让后端定位到本 tab（content script 所在的 http 页），而非 popup/扩展页；
      // is_top_frame 标识是否顶层 frame（iframe 内的 content 为 false，后端定位参考）
      chrome.runtime.sendMessage({
        kind: 'event',
        event: { ...event, url: location.href, is_top_frame: window.top === window.self },
      });
    };

    const sendSignal = (signal: SignalEvent) => {
      chrome.runtime.sendMessage({ kind: 'signal', signal: { ...signal, url: location.href } });
    };

    const install = (): (() => void) => {
      // 先建副作用观察器，把它的 markAction 作 onAction 传给 action 采集器——
      // 每发一个动作就开启 1s 观察窗口，捕获该动作引发的 modal/dropdown 打开。
      const se = installSideEffectObserver({ sendSignal });
      const u1 = installActionRecorder({ sendEvent, onAction: se.markAction });
      const u2 = installNavigationRecorder({ sendEvent });
      return () => {
        u1();
        u2();
        se.uninstall();
      };
    };

    // 监听 background 的状态广播，动态装配/卸载采集器
    chrome.runtime.onMessage.addListener((msg: { kind?: string }) => {
      if (msg?.kind === 'recording-started' && !uninstall) {
        uninstall = install();
      } else if (msg?.kind === 'recording-stopped' && uninstall) {
        uninstall();
        uninstall = null;
      }
    });

    // 页面加载时若已在录制，立即装配（content 注入晚于开始录制的情况）
    chrome.runtime
      .sendMessage({ kind: 'query-state' })
      .then((state?: { recording?: boolean }) => {
        if (state?.recording && !uninstall) {
          uninstall = install();
        }
      })
      .catch(() => {
        /* background 未就绪，忽略 */
      });
  },
});

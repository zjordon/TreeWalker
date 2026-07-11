// 扩展事件协议（对应后端 src/tree_walker/recorder/event_mapper.py）。
// content script 采集 → background → POST 后端 /event。

/** 元素线索（借鉴 Browser-BC selector.ts 的 buildElementRef）。 */
export interface ElementRef {
  tag: string;
  id?: string;
  classes?: string[];
  role?: string;
  name?: string;
  text?: string;
  selector: string;
  /** Browser-BC xpathFor 风格：/html/body/...（前导 /）；后端 normalize_xpath 会 strip。 */
  xpath: string;
  rect: { x: number; y: number; width: number; height: number };
}

/** 发给后端的统一事件结构。type 对应 TreeWalker action 名。 */
export interface RecorderEvent {
  /** 事件发生时所在页面 url（content script 的 location.href），后端据此定位 CDP target（避免读到 popup/扩展页）。 */
  url?: string;
  /** 目标元素定位线索：xpath 失败时后端用 tag+name/id/aria-label 做 ATTRIBUTE 级兜底。 */
  tag?: string;
  id?: string;
  name?: string;
  ariaLabel?: string;
  role?: string;
  type:
    | 'click'
    | 'input_text'
    | 'select_dropdown'
    | 'scroll'
    | 'navigate'
    | 'go_back'
    | 'switch_tab'
    | 'close_tab'
    | 'send_keys'
    | 'upload_file';
  /** 目标元素 xpath 线索（需 index 的 action 才有）。 */
  xpath?: string;
  /** 目标元素 rect（多候选时后端按中心就近）。 */
  rect?: { x: number; y: number; width: number; height: number };
  /** 操作参数（text/value/url/...，不含 index——index 由后端 locator 定位后填）。 */
  params?: Record<string, unknown>;
  tab_id?: string;
  ts: number;
}

/** content → background 的消息。 */
export type ContentMessage =
  | { kind: 'event'; event: RecorderEvent }
  | { kind: 'query-state' };

/** background → content 的录制状态广播。 */
export interface RecordingState {
  recording: boolean;
}

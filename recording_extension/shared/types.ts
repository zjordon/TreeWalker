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
  /** 是否顶层 frame（window.top===window.self）。iframe 内的 content 为 false；后端 iframe 定位参考。 */
  is_top_frame?: boolean;
  /** 目标元素定位线索：xpath 失败时后端用 tag+name/id/aria-label 做 ATTRIBUTE 级兜底。 */
  tag?: string;
  id?: string;
  name?: string;
  ariaLabel?: string;
  role?: string;
  /** 目标元素可见文字（扩展 textContent，点击瞬间 ground truth）。后端 TEXT 级优先按它定位——
   *  cover step tab 等指纹/类撞车、仅靠文字区分的元素（issue #136）。 */
  text?: string;
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
  /** upload_file 专用语义线索（issue #139）：封装上传组件的上下文。后端存进 ``_semantic_clue``，
   *  重放端 ``_match_file_upload_by_clue`` 据此在多个同 accept 的 file input 里精筛（替代
   *  ``_resolve_file_input_by_accept`` 的 ``candidates[0]`` 兜底）。详见
   *  docs/user_recording/upload-semantic-clue-plan.md。 */
  upload_ctx?: {
    /** 封装 semi-upload widget 的 drag-area 文案（兄弟元素，不随 Semi-UI 重建 input 消失）——主区分信号。 */
    area_text: string;
    /** 活动 step tab / 最近 heading 文案（"设置横/竖封面" 等）——方向/区域辅助区分。 */
    nearby_text: string;
    /** 封装 widget 的 class（粗筛辅助，含 CSS-module hash，不稳）。 */
    upload_ancestor_class: string;
  };
  /** 操作参数（text/value/url/...，不含 index——index 由后端 locator 定位后填）。 */
  params?: Record<string, unknown>;
  tab_id?: string;
  ts: number;
}

/** 副作用信号（SideEffectObserver 检测到动作引发的 DOM 变化，POST 后端 /signal）。
 *  后端 attach_signal 把它附到最近动作的 signals 列表，供翻译规则判断意图
 *  （如 modal_opened 让 rule_file_upload 确认前置 click 是编辑器触发器，不吸收）。 */
export interface SignalEvent {
  type: 'modal_opened' | 'dropdown_opened';
  selector: string;
  ts: number;
}

/** content → background 的消息。 */
export type ContentMessage =
  | { kind: 'event'; event: RecorderEvent }
  | { kind: 'signal'; signal: SignalEvent }
  | { kind: 'query-state' };

/** background → content 的录制状态广播。 */
export interface RecordingState {
  recording: boolean;
}

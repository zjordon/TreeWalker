// P6 实时浏览器视图。mode='screenshots' 订阅最新帧 data URL；mode='livestream' 后置。
// 标注层（高亮 agent 当前操作元素）独立于 mode——先在截图流做好，直播视口直接复用（§4.2 机制 2）。
import type { Highlight } from "../types";

interface Props {
	mode: "screenshots" | "livestream";
	screenshot: string | null; // data URL（screenshots 模式）
	highlights?: Highlight[]; // I3：当前步 tool_call 目标元素几何（归一化百分比 ∈ [0,1]）
}

// 归一化 bbox → 百分比样式（截图与视口同宽高比，免采样比/DPR 换算）
function pct(v: number): string {
	return `${(v * 100).toFixed(2)}%`;
}

export default function BrowserView({ mode, screenshot, highlights = [] }: Props) {
	return (
		<div className="browser-view">
			<div className="browser-frame">
				{mode === "screenshots" ? (
					screenshot ? (
						<img src={screenshot} alt="browser" />
					) : (
						<div className="muted browser-placeholder">等待截图…</div>
					)
				) : (
					<div className="muted browser-placeholder">直播视口（后置）</div>
				)}
				{mode === "screenshots" && screenshot &&
					highlights.map((h) => (
						<div
							key={h.index}
							className="hl-box"
							style={{
								left: pct(h.bbox.left),
								top: pct(h.bbox.top),
								width: pct(h.bbox.width),
								height: pct(h.bbox.height),
							}}
						>
							<span className="hl-label">{h.index}</span>
						</div>
					))}
			</div>
		</div>
	);
}

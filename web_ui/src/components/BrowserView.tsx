// P6 实时浏览器视图。screenshots 模式订阅每步截图；livestream 模式订阅 CDP screencast 连续帧；
// 两者都喂 screenshot（data URL），渲染路径一致。标注层（高亮 agent 当前操作元素）独立于 mode（§4.2 机制 2）。
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
				{mode === "livestream" && <span className="mode-badge live">● 直播</span>}
				{screenshot ? (
					<img src={screenshot} alt="browser" />
				) : (
					<div className="muted browser-placeholder">
						{mode === "livestream" ? "等待直播帧…" : "等待截图…"}
					</div>
				)}
				{screenshot &&
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

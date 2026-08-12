// P6 实时浏览器视图。mode='screenshots' 订阅最新帧 data URL；mode='livestream' 后置。
// 标注层（高亮 agent 当前操作元素）独立于 mode——先在截图流做好，直播视口直接复用（§4.2 机制 2）。
interface Props {
	mode: "screenshots" | "livestream";
	screenshot: string | null; // data URL（screenshots 模式）
	annotation?: string | null; // 标注层：高亮元素描述（M3 先文本占位，后续绘 box）
}

export default function BrowserView({ mode, screenshot, annotation }: Props) {
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
			</div>
			{annotation && <div className="annotation">{annotation}</div>}
		</div>
	);
}

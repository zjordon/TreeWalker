import type { EditorState } from "../types";

interface Props {
	state: EditorState;
}

export default function RunPanel({ state }: Props) {
	if (!state.runResult) return null;
	const results = state.runResult;
	const done = results.find((r) => r.is_done);
	const success = done?.success === true;
	return (
		<div className="panel">
			<h2>
				试跑结果：{success ? "✓ 成功" : done ? "✗ 失败" : "未完成"}（{results.length} 步）
			</h2>
			<ul className="var-list">
				{results.map((r, i) => (
					<li key={i}>
						{r.is_done && r.success ? "✓" : "✗"} 步{i}
						{r.error && <span className="error-text"> — {r.error}</span>}
						{r.extracted_content && (
							<span> | {r.extracted_content.slice(0, 120)}</span>
						)}
					</li>
				))}
			</ul>
		</div>
	);
}

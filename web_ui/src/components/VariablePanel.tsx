import type { Dispatch } from "react";
import type { EditorState, DetectedVariable } from "../types";
import type { EditorAction } from "../reducer";

interface Props {
	state: EditorState;
	dispatch: Dispatch<EditorAction>;
}

export default function VariablePanel({ state, dispatch }: Props) {
	const names = Object.keys(state.variables);

	const exportCsv = () => {
		const header = names.join(",");
		const blob = new Blob([header + "\n"], { type: "text/csv" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = "data.csv";
		a.click();
		URL.revokeObjectURL(url);
	};

	return (
		<div className="panel">
			<h2>
				变量（detect ∪ manual）
				{names.length > 0 && (
					<button onClick={exportCsv} style={{ float: "right" }}>
						导出 CSV 模板
					</button>
				)}
			</h2>
			{names.length === 0 ? (
				<p className="muted">点"检测变量"（自动检测 + 人工标注并集）</p>
			) : (
				<ul className="var-list">
					{names.map((n) => {
						const v: DetectedVariable = state.variables[n];
						return (
							<li key={n}>
								<strong>{n}</strong> = {JSON.stringify(v.original_value)}
								{v.format && <span className="muted"> ({v.format})</span>}
								<button
									className="error"
									onClick={() => dispatch({ type: "REMOVE_VAR", name: n })}
								>
									删
								</button>
							</li>
						);
					})}
				</ul>
			)}
		</div>
	);
}

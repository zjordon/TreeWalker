import { useState, useEffect } from "react";
import type { Dispatch } from "react";
import type { EditorState } from "../types";
import type { EditorAction } from "../reducer";

interface Props {
	state: EditorState;
	dispatch: Dispatch<EditorAction>;
}

export default function ActionEditor({ state, dispatch }: Props) {
	const sel = state.selected;
	const step = sel && state.history ? state.history.history[sel.stepIdx] : null;
	const action = step && sel ? step.model_output.actions[sel.actionIdx] : null;

	// 受控本地输入：编辑中不每键 dispatch（apply 时统一提交）
	const [vals, setVals] = useState<Record<string, string>>({});
	useEffect(() => {
		if (!action) {
			setVals({});
			return;
		}
		const v: Record<string, string> = {};
		for (const k of Object.keys(action.params)) {
			if (typeof action.params[k] === "string") v[k] = action.params[k] as string;
		}
		setVals(v);
		// 依赖：选中步/动作变化 + history 变化（apply/外部 mutation 后同步）
	}, [sel?.stepIdx, sel?.actionIdx, state.history]);

	if (!step || !action || !sel) {
		return (
			<div className="panel">
				<h2>编辑选中动作</h2>
				<p className="muted">点击左侧动作行</p>
			</div>
		);
	}

	const strFields = Object.keys(action.params).filter(
		(k) => k !== "index" && typeof action.params[k] === "string"
	);

	const apply = () => {
		for (const f of strFields) {
			dispatch({
				type: "UPDATE_PARAM",
				stepIdx: sel.stepIdx,
				actionIdx: sel.actionIdx,
				field: f,
				value: vals[f],
			});
		}
	};
	const del = () => {
		if (confirm("删除整步？删 click 步可能破坏后续定位链。")) {
			dispatch({ type: "DELETE_STEP", stepIdx: sel.stepIdx });
		}
	};
	const mark = () => {
		const name = prompt("变量名（如 product）:");
		if (!name) return;
		const field = strFields[0] || "text";
		dispatch({
			type: "ADD_MANUAL_VAR",
			name,
			stepIdx: sel.stepIdx,
			actionIdx: sel.actionIdx,
			field,
			original: vals[field] ?? "",
		});
	};

	return (
		<div className="panel">
			<h2>
				步 {step.step_number}.{sel.actionIdx}: {action.name}
			</h2>
			{strFields.map((f) => (
				<div key={f}>
					<label>{f}</label>
					<input
						type="text"
						value={vals[f] ?? ""}
						onChange={(e) => setVals({ ...vals, [f]: e.target.value })}
					/>
				</div>
			))}
			{strFields.length > 0 && <button onClick={apply}>应用修改</button>}
			<hr />
			<button className="error" onClick={del}>
				删除此步
			</button>
			<hr />
			<button onClick={mark}>标注为变量</button>
		</div>
	);
}

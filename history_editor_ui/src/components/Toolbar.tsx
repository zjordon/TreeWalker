import { useState, useEffect } from "react";
import type { EditorState } from "../types";
import * as api from "../api";

interface Props {
	state: EditorState;
	onLoad: (name: string) => void;
	onDetect: () => void;
	onSave: () => void;
	onRun: () => void;
}

export default function Toolbar({ state, onLoad, onDetect, onSave, onRun }: Props) {
	const [files, setFiles] = useState<string[]>([]);
	const [sel, setSel] = useState("");

	const refresh = () => api.listFiles().then(setFiles).catch(() => setFiles([]));
	useEffect(() => {
		refresh();
	}, [state.loadedName, state.dirty]);

	return (
		<div className="bar">
			<label>
				历史文件:
				<select value={sel} onChange={(e) => setSel(e.target.value)}>
					<option value="">-- 选择 --</option>
					{files.map((f) => (
						<option key={f} value={f}>
							{f}
						</option>
					))}
				</select>
			</label>
			<button onClick={() => sel && onLoad(sel)} disabled={!sel}>
				加载
			</button>
			<button onClick={onDetect} disabled={!state.loadedName}>
				检测变量
			</button>
			<button onClick={onSave} disabled={!state.history}>
				保存{state.dirty ? " *" : ""}
			</button>
			<button onClick={onRun} disabled={!state.loadedName}>
				试跑
			</button>
			<span className="status">{state.status}</span>
		</div>
	);
}

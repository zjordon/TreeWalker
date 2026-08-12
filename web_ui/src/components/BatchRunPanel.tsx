import { useState } from "react";
import type { EditorState } from "../types";

interface Props {
	state: EditorState;
	onStart: (file: File) => void;
	onCancel: () => void;
	onReset: () => void;
}

export default function BatchRunPanel({ state, onStart, onCancel, onReset }: Props) {
	const { batch } = state;
	const [file, setFile] = useState<File | null>(null);
	const running = batch.phase === "running" || batch.phase === "starting";
	const succeeded = batch.rows.filter((r) => r.success).length;
	const finished = batch.phase === "done" || batch.phase === "cancelled" || batch.phase === "error";

	return (
		<div className="panel">
			<h2>
				CSV 批量重放
				{batch.totalRows > 0 && running && (
					<span className="status">
						{" "}{batch.rows.length}/{batch.totalRows} 行（{succeeded} 成功）
					</span>
				)}
			</h2>
			<div className="bar">
				<input
					type="file"
					accept=".csv"
					onChange={(e) => setFile(e.target.files?.[0] ?? null)}
					disabled={running}
				/>
				<button
					onClick={() => file && onStart(file)}
					disabled={!file || !state.loadedName || running}
				>
					开始批量
				</button>
				{running && (
					<button className="error" onClick={onCancel}>
						中止
					</button>
				)}
				{finished && <button onClick={onReset}>重置</button>}
			</div>
			{batch.phase === "done" && (
				<span className="status">
					批量完成：{succeeded}/{batch.rows.length} 行成功
				</span>
			)}
			{batch.phase === "cancelled" && (
				<span className="status">已中止（{batch.rows.length} 行已执行）</span>
			)}
			{batch.phase === "error" && <span className="error-text">{batch.error}</span>}
			{batch.currentStep && running && (
				<div className="status">
					步 {batch.currentStep.step_index}/{batch.currentStep.total}：
					{batch.currentStep.success ? "✓" : "✗"}
					{batch.currentStep.extracted_content &&
						` | ${batch.currentStep.extracted_content.slice(0, 80)}`}
					{batch.currentStep.error && (
						<span className="error-text"> {batch.currentStep.error.slice(0, 80)}</span>
					)}
				</div>
			)}
			{batch.rows.length > 0 && (
				<ul className="var-list">
					{batch.rows.map((r) => (
						<li key={r.row_index}>
							{r.success ? "✓" : "✗"} 行{r.row_index + 1}
							<span className="muted"> ({r.n_steps} 步)</span>
							{r.error && <span className="error-text"> — {r.error.slice(0, 100)}</span>}
							{r.extracted_content && <span> | {r.extracted_content.slice(0, 80)}</span>}
						</li>
					))}
				</ul>
			)}
		</div>
	);
}

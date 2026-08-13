import { useState, useEffect, useCallback } from "react";
import * as api from "../api";
import { SKILL_FILES, type SkillFile } from "../api";
import { useAppNav } from "../appNav";

// P6 后续 B：技能面。分栏——左 host 列表（domain-skills/<host>），右三文件 tab 编辑。
// 后端 /skills/{list,get,put}；put 后后端失效 live agent 的 skill 缓存（热更新）。
// 文件名 → tab 标签：_sop.md→SOP / selectors.md→Selectors / quirks.md→Quirks。
const FILE_LABELS: Record<SkillFile, string> = {
	"_sop.md": "SOP",
	"selectors.md": "Selectors",
	"quirks.md": "Quirks",
};

export default function SkillsShell() {
	const [hosts, setHosts] = useState<string[]>([]);
	const [selected, setSelected] = useState<string | null>(null);
	const [files, setFiles] = useState<Record<SkillFile, string> | null>(null);
	const [activeFile, setActiveFile] = useState<SkillFile>("_sop.md");
	const [draft, setDraft] = useState("");
	const [dirty, setDirty] = useState(false);
	const [status, setStatus] = useState("");
	const nav = useAppNav();

	const refresh = useCallback(async () => {
		try {
			setHosts(await api.listSkills());
		} catch {
			setHosts([]);
		}
	}, []);

	useEffect(() => {
		refresh();
	}, [refresh]);

	const onSelectHost = useCallback(async (host: string) => {
		if (dirty && !window.confirm("当前文件未保存，切换将丢弃改动，是否继续？")) return;
		setSelected(host);
		setDirty(false);
		try {
			const f = await api.getSkill(host);
			setFiles(f);
			setActiveFile("_sop.md");
			setDraft(f["_sop.md"] ?? "");
			setStatus(`已加载 ${host}`);
		} catch (e) {
			setFiles(null);
			setStatus(`加载失败: ${e}`);
		}
	}, [dirty]);

	// I1：从 RunView「活动技能」chip 跳进来（context skillsHost 变化）→ 预选该 host
	useEffect(() => {
		const h = nav?.skillsHost;
		if (h && h !== selected) {
			void onSelectHost(h);
		}
	}, [nav?.skillsHost, selected, onSelectHost]);

	const onSwitchFile = useCallback((file: SkillFile) => {
		if (file === activeFile) return;
		if (dirty && !window.confirm("当前文件未保存，切换将丢弃改动，是否继续？")) return;
		setActiveFile(file);
		setDraft(files?.[file] ?? "");
		setDirty(false);
	}, [activeFile, dirty, files]);

	const onSave = useCallback(async () => {
		if (!selected || !files) return;
		try {
			await api.putSkill(selected, activeFile, draft);
			setFiles({ ...files, [activeFile]: draft });
			setDirty(false);
			setStatus(`已保存 ${selected}/${activeFile} ✓`);
		} catch (e) {
			setStatus(`保存失败: ${e}`);
		}
	}, [selected, files, activeFile, draft]);

	return (
		<div className="flow-workspace">
			<aside className="flow-sidebar">
				<h2>技能（host）</h2>
				<ul className="flow-list">
					{hosts.length === 0 && <li className="muted">（空）</li>}
					{hosts.map((h) => (
						<li key={h} className={h === selected ? "selected-flow" : ""}>
							<button className="flow-item" onClick={() => onSelectHost(h)}>
								{h}
							</button>
						</li>
					))}
				</ul>
			</aside>

			<section className="flow-main">
				<nav className="flow-tabs">
					{SKILL_FILES.map((f) => (
						<button
							key={f}
							className={f === activeFile && selected ? "active" : ""}
							onClick={() => onSwitchFile(f)}
							disabled={!selected}
						>
							{FILE_LABELS[f]}
						</button>
					))}
					<button onClick={onSave} disabled={!selected}>
						保存{dirty ? " *" : ""}
					</button>
					<span className="status">{status}</span>
				</nav>

				{selected ? (
					<textarea
						className="skill-editor"
						value={draft}
						onChange={(e) => {
							setDraft(e.target.value);
							setDirty(true);
						}}
						placeholder={`编辑 ${selected}/${activeFile} …`}
					/>
				) : (
					<div className="panel muted">从左侧选择一个 host 开始编辑技能（_sop / selectors / quirks）</div>
				)}
			</section>
		</div>
	);
}

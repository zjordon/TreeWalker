import { useState, useEffect, useCallback, useMemo } from "react";
import * as api from "../api";
import type { SettingFieldDTO } from "../api";

// T2 C（M4）：设置面。分栏——左 section 导航（LLM/Agent/Browser/高级，从注册表动态取），
// 右该区字段表单（type→控件：bool→checkbox / enum→select / int·float→number / str→text）。
// 后端 /settings/get|set（进程内存 override）：改动仅对新任务生效，表单底部明示。
// drafts 全局持有（不按 section 分片）→ 切区不丢改动，无需「丢弃确认」；「应用」提交全部 diff。
// 敏感字段：draft 恒从空开始（placeholder 提示已设置），不动就不产生 diff → 不会把掩码串提交。
const SECTION_LABELS: Record<string, string> = {
	llm: "LLM",
	agent: "Agent",
	browser: "Browser",
	advanced: "高级",
};

export default function SettingsShell() {
	const [fields, setFields] = useState<SettingFieldDTO[] | null>(null);
	const [section, setSection] = useState("llm");
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [originals, setOriginals] = useState<Record<string, string>>({});
	const [status, setStatus] = useState("");

	useEffect(() => {
		(async () => {
			try {
				const { fields: fs } = await api.getSettings();
				setFields(fs);
				const d: Record<string, string> = {};
				const o: Record<string, string> = {};
				for (const f of fs) {
					// masked 初值不进 draft（掩码串不参与 diff），originals 记 "" → 输入真值才算改动
					d[f.env] = f.masked ? "" : f.value;
					o[f.env] = f.masked ? "" : f.value;
				}
				setDrafts(d);
				setOriginals(o);
			} catch (e) {
				setStatus(`加载失败: ${e}`);
			}
		})();
	}, []);

	const sections = useMemo(() => {
		if (!fields) return [];
		return [...new Set(fields.map((f) => f.section))];
	}, [fields]);

	// 只提交 diff；空串不算改动（与后端「敏感空值跳过」一致，也避免误清 str 字段）
	const changed = useMemo(() => {
		const c: Record<string, string> = {};
		for (const env of Object.keys(drafts)) {
			if (drafts[env] !== originals[env] && drafts[env] !== "") c[env] = drafts[env];
		}
		return c;
	}, [drafts, originals]);
	const dirty = Object.keys(changed).length > 0;

	const onField = (env: string, value: string) => {
		setDrafts((d) => ({ ...d, [env]: value }));
	};

	const onApply = useCallback(async () => {
		if (!dirty) return;
		try {
			await api.setSettings(changed);
			setOriginals((o) => ({ ...o, ...changed }));
			setStatus("已应用 ✓（新任务生效）");
		} catch (e) {
			setStatus(`应用失败: ${e}`);
		}
	}, [changed, dirty]);

	if (!fields) {
		return <div className="panel muted">{status || "加载设置中…"}</div>;
	}

	const sectionFields = fields.filter((f) => f.section === section);
	return (
		<div className="flow-workspace">
			<aside className="flow-sidebar">
				<h2>设置</h2>
				<ul className="flow-list">
					{sections.map((s) => {
						const sDirty = fields.some((f) => f.section === s && changed[f.env] !== undefined);
						return (
							<li key={s} className={s === section ? "selected-flow" : ""}>
								<button className="flow-item" onClick={() => setSection(s)}>
									{SECTION_LABELS[s] ?? s}
									{sDirty ? " *" : ""}
								</button>
							</li>
						);
					})}
				</ul>
			</aside>

			<section className="flow-main">
				<nav className="flow-tabs">
					<button onClick={onApply} disabled={!dirty}>
						应用{dirty ? " *" : ""}
					</button>
					<span className="status">{status}</span>
				</nav>

				<div className="settings-form">
					{sectionFields.map((f) => (
						<div key={f.env} className="setting-row">
							<label htmlFor={`set-${f.env}`} title={`env: ${f.env}（默认 ${f.default}）`}>
								{f.key}
							</label>
							{f.type === "bool" ? (
								<input
									id={`set-${f.env}`}
									type="checkbox"
									checked={drafts[f.env] === "true"}
									onChange={(e) => onField(f.env, e.target.checked ? "true" : "false")}
								/>
							) : f.type === "enum" ? (
								<select
									id={`set-${f.env}`}
									value={drafts[f.env]}
									onChange={(e) => onField(f.env, e.target.value)}
								>
									{f.choices.map((c) => (
										<option key={c} value={c}>
											{c}
										</option>
									))}
								</select>
							) : (
								<input
									id={`set-${f.env}`}
									type={f.type === "int" || f.type === "float" ? "number" : "text"}
									value={drafts[f.env]}
									onChange={(e) => onField(f.env, e.target.value)}
									placeholder={
										f.sensitive
											? f.masked
												? `已设置（${f.value}），输入以覆盖`
												: "未设置"
											: ""
									}
								/>
							)}
							<span className="muted setting-env">{f.env}</span>
						</div>
					))}
					<div className="muted settings-note">
						改动仅对新任务生效（运行中任务不受影响）；重启 tw-web 后回落 .env / 默认值。
					</div>
				</div>
			</section>
		</div>
	);
}

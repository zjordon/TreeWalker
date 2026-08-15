import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import type { SettingFieldDTO } from "../api";

const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		getSettings: vi.fn(),
		setSettings: vi.fn(),
	},
}));

vi.mock("../api", () => apiMocks);

import SettingsShell from "../components/SettingsShell";

// 与后端 _SETTINGS_FIELDS 对齐的样例（覆盖 str/enum/int/bool × llm/agent 区 + 敏感掩码）
const FIELDS: SettingFieldDTO[] = [
	{ key: "模型", env: "LLM_MODEL", section: "llm", type: "str", choices: [], default: "glm-5.1", value: "glm-5.1", sensitive: false, masked: false },
	{ key: "输出模式", env: "LLM_OUTPUT_MODE", section: "llm", type: "enum", choices: ["standard", "flash", "thinking"], default: "standard", value: "standard", sensitive: false, masked: false },
	{ key: "API Key", env: "ZHIPU_API_KEY", section: "llm", type: "str", choices: [], default: "", value: "****efgh", sensitive: true, masked: true },
	{ key: "最大步数", env: "AGENT_MAX_STEPS", section: "agent", type: "int", choices: [], default: "100", value: "100", sensitive: false, masked: false },
	{ key: "计划模式", env: "AGENT_ENABLE_PLANNING", section: "agent", type: "bool", choices: [], default: "true", value: "true", sensitive: false, masked: false },
];

// 包含匹配：section/应用按钮 dirty 时带「 *」后缀，精确匹配会落空
function findButton(container: HTMLElement, text: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) =>
		b.textContent?.includes(text),
	);
	if (!btn) throw new Error(`button containing "${text}" not found`);
	return btn as HTMLButtonElement;
}

describe("SettingsShell（T2 C M4）", () => {
	beforeEach(() => {
		apiMocks.getSettings.mockReset().mockResolvedValue({ fields: FIELDS, applies: "new_tasks" });
		apiMocks.setSettings.mockReset().mockResolvedValue(undefined);
	});

	it("挂载加载设置；默认 LLM 区字段可见、Agent 区不可见", async () => {
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		const model = container.querySelector("#set-LLM_MODEL") as HTMLInputElement;
		expect(model.value).toBe("glm-5.1");
		expect(container.querySelector("#set-AGENT_MAX_STEPS")).toBeNull();
		// 敏感字段：空输入 + placeholder 提示已设置（掩码串不进输入框）
		const key = container.querySelector("#set-ZHIPU_API_KEY") as HTMLInputElement;
		expect(key.value).toBe("");
		expect(key.placeholder).toContain("****efgh");
	});

	it("切到 Agent 区 → int 用 number 控件、bool 用 checkbox", async () => {
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		fireEvent.click(findButton(container, "Agent"));
		const steps = container.querySelector("#set-AGENT_MAX_STEPS") as HTMLInputElement;
		expect(steps.type).toBe("number");
		expect(steps.value).toBe("100");
		const planning = container.querySelector("#set-AGENT_ENABLE_PLANNING") as HTMLInputElement;
		expect(planning.type).toBe("checkbox");
		expect(planning.checked).toBe(true);
	});

	it("无改动时应用按钮 disabled；改动后可应用，payload 只含 diff", async () => {
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		expect(findButton(container, "应用").disabled).toBe(true);
		// 改 enum + 切 bool + 改 int
		fireEvent.change(container.querySelector("#set-LLM_OUTPUT_MODE")!, { target: { value: "flash" } });
		fireEvent.click(findButton(container, "Agent"));
		fireEvent.change(container.querySelector("#set-AGENT_MAX_STEPS")!, { target: { value: "3" } });
		fireEvent.click(container.querySelector("#set-AGENT_ENABLE_PLANNING")!);
		// 切回 LLM 区——drafts 全局持有，改动不丢（无需丢弃确认）
		fireEvent.click(findButton(container, "LLM"));
		const apply = findButton(container, "应用 *");
		expect(apply.disabled).toBe(false);
		fireEvent.click(apply);
		await waitFor(() => expect(apiMocks.setSettings).toHaveBeenCalledWith({
			LLM_OUTPUT_MODE: "flash",
			AGENT_MAX_STEPS: "3",
			AGENT_ENABLE_PLANNING: "false",
		}));
		await waitFor(() => expect(container.textContent).toContain("已应用 ✓（新任务生效）"));
		// 应用成功后 dirty 清（按钮回到 disabled）
		await waitFor(() => expect(findButton(container, "应用").disabled).toBe(true));
	});

	it("敏感字段不动 → 应用 payload 不含该 key（掩码串不会被提交）", async () => {
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		fireEvent.change(container.querySelector("#set-LLM_MODEL")!, { target: { value: "glm-5.2" } });
		fireEvent.click(findButton(container, "应用 *"));
		await waitFor(() => expect(apiMocks.setSettings).toHaveBeenCalled());
		expect(apiMocks.setSettings).toHaveBeenCalledWith({ LLM_MODEL: "glm-5.2" });
	});

	it("敏感字段输入真值 → 提交真值", async () => {
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		fireEvent.change(container.querySelector("#set-ZHIPU_API_KEY")!, { target: { value: "sk-real" } });
		fireEvent.click(findButton(container, "应用 *"));
		await waitFor(() =>
			expect(apiMocks.setSettings).toHaveBeenCalledWith({ ZHIPU_API_KEY: "sk-real" }));
	});

	it("应用失败 → 显示失败状态", async () => {
		apiMocks.setSettings.mockRejectedValue(new Error("未知配置项"));
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(apiMocks.getSettings).toHaveBeenCalled());
		fireEvent.change(container.querySelector("#set-LLM_MODEL")!, { target: { value: "glm-5.2" } });
		fireEvent.click(findButton(container, "应用 *"));
		await waitFor(() => expect(container.textContent).toContain("应用失败"));
	});

	it("加载失败 → 显示失败提示", async () => {
		apiMocks.getSettings.mockRejectedValue(new Error("backend down"));
		const { container } = render(<SettingsShell />);
		await waitFor(() => expect(container.textContent).toContain("加载失败"));
	});
});

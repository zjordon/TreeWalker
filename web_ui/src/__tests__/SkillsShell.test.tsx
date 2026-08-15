import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";

const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		listSkills: vi.fn(),
		getSkill: vi.fn(),
		putSkill: vi.fn(),
	},
}));

vi.mock("../api", () => ({ ...apiMocks, SKILL_FILES: ["_sop.md", "selectors.md", "quirks.md"] }));

import SkillsShell from "../components/SkillsShell";

function findButton(container: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text) as
		| HTMLButtonElement
		| undefined;
}

describe("SkillsShell (P6 后续 B)", () => {
	beforeEach(() => {
		apiMocks.listSkills.mockReset();
		apiMocks.getSkill.mockReset();
		apiMocks.putSkill.mockReset();
	});

	it("挂载时列出 host", async () => {
		apiMocks.listSkills.mockResolvedValue(["member.bilibili.com", "creator.douyin.com"]);
		const { container } = render(<SkillsShell />);
		await waitFor(() => expect(apiMocks.listSkills).toHaveBeenCalled());
		expect(container.textContent).toContain("member.bilibili.com");
		expect(container.textContent).toContain("creator.douyin.com");
	});

	it("点击 host → 加载三文件，textarea 显示 _sop 内容", async () => {
		apiMocks.listSkills.mockResolvedValue(["member.bilibili.com"]);
		apiMocks.getSkill.mockResolvedValue({
			"_sop.md": "# sop body",
			"selectors.md": "selectors body",
			"quirks.md": "quirks body",
		});
		const { container } = render(<SkillsShell />);
		await waitFor(() => expect(container.textContent).toContain("member.bilibili.com"));
		fireEvent.click(findButton(container, "member.bilibili.com")!);
		await waitFor(() => expect(apiMocks.getSkill).toHaveBeenCalledWith("member.bilibili.com"));
		const ta = container.querySelector("textarea") as HTMLTextAreaElement;
		expect(ta.value).toBe("# sop body");
	});

	it("切换 Selectors tab → textarea 切到 selectors 内容", async () => {
		apiMocks.listSkills.mockResolvedValue(["h.example.com"]);
		apiMocks.getSkill.mockResolvedValue({
			"_sop.md": "sop",
			"selectors.md": "selectors body",
			"quirks.md": "quirks",
		});
		const { container } = render(<SkillsShell />);
		await waitFor(() => expect(container.textContent).toContain("h.example.com"));
		fireEvent.click(findButton(container, "h.example.com")!);
		await waitFor(() => expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("sop"));
		fireEvent.click(findButton(container, "Selectors")!);
		await waitFor(() =>
			expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("selectors body"),
		);
	});

	it("保存 → 调 putSkill(host, activeFile, draft)，dirty 清", async () => {
		apiMocks.listSkills.mockResolvedValue(["h.example.com"]);
		apiMocks.getSkill.mockResolvedValue({ "_sop.md": "old", "selectors.md": "", "quirks.md": "" });
		apiMocks.putSkill.mockResolvedValue(undefined);
		const { container } = render(<SkillsShell />);
		await waitFor(() => expect(container.textContent).toContain("h.example.com"));
		fireEvent.click(findButton(container, "h.example.com")!);
		await waitFor(() => expect(apiMocks.getSkill).toHaveBeenCalled());
		const ta = container.querySelector("textarea") as HTMLTextAreaElement;
		fireEvent.change(ta, { target: { value: "new content" } });
		fireEvent.click(findButton(container, "保存 *")!);
		await waitFor(() =>
			expect(apiMocks.putSkill).toHaveBeenCalledWith("h.example.com", "_sop.md", "new content"),
		);
	});
});

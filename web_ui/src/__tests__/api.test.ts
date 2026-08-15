import { describe, it, expect, vi, afterEach } from "vitest";

// readJson 守卫：响应为 HTML（SPA fallback / 路径未被代理）时给出可诊断错误，
// 而非把 "Unexpected token '<'" 的原始 SyntaxError 冒给用户（T2 C M4 排坑后补）。
import * as api from "../api";

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("api readJson 守卫", () => {
	it("响应为 HTML → 报「非 JSON / 未代理或后端过旧」可诊断错误", async () => {
		vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
			new Response("<!DOCTYPE html><html></html>", {
				status: 200,
				headers: { "Content-Type": "text/html" },
			}),
		));
		await expect(api.listFiles()).rejects.toThrow(/非 JSON/);
		await expect(api.listFiles()).rejects.toThrow(/未代理|后端版本过旧/);
	});

	it("错误状态且响应为 HTML（如 404 页面）→ 同样走守卫而非 SyntaxError", async () => {
		vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
			new Response("<!DOCTYPE html><html>404</html>", {
				status: 404,
				headers: { "Content-Type": "text/html; charset=utf-8" },
			}),
		));
		await expect(api.loadHistory("a.json")).rejects.toThrow(/非 JSON/);
	});

	it("正常 JSON 响应照常解析", async () => {
		vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ files: ["a.json", "b.json"] }), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			}),
		));
		expect(await api.listFiles()).toEqual(["a.json", "b.json"]);
	});
});

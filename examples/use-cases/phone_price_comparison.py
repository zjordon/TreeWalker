"""Example: 跨站结构化价格对比（output_model + CDP）。移植自 browser-use/examples/use-cases/phone_comparison.py。

让 agent 在多个购物站比较某型号手机价格，结构化返回。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/use-cases/phone_price_comparison.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from pydantic import BaseModel, Field

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


class PhonePrice(BaseModel):
	site: str = Field(..., description="站点名")
	price: str = Field(..., description="价格（含货币）")
	url: str = Field(..., description="商品页 URL")


class PriceComparison(BaseModel):
	model_name: str
	prices: list[PhonePrice]


# 可按需改成你想比较的型号 / 站点
TASK = (
	"Compare the price of 'iPhone 16 Pro 256GB' across at least 2 shopping sites "
	"(e.g. Amazon, Best Buy). Return structured data with the model name and a "
	"list of per-site prices."
)


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(
		task=TASK,
		llm=llm,
		browser=browser,
		settings=settings.agent,
		output_model=PriceComparison,
	)
	history = await agent.run()

	result = history.final_result()
	if not result:
		print("No result")
		return
	parsed = PriceComparison.model_validate_json(result)
	print(f"\n{parsed.model_name}")
	for p in parsed.prices:
		print(f"  - {p.site}: {p.price}  ({p.url})")


if __name__ == "__main__":
	asyncio.run(main())

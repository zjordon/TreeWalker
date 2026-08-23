"""P7 轨迹假设验证（DB 路径）：Task 1 bestsellers 品牌聚合（确定性，无 LLM、无浏览器）。

背景：docs/p7/01-task1-trajectory-anatomy.md 对 WebArena task 1（top-1 best-selling
brand in Q1 2022，参考答案 Sprite）的失败轨迹做了解剖。本脚本直接查
WebArena Docker（shopping_admin 容器）里的 Magento 物化表做地面真值验证：

  H2  agent 日志自述「33 records」——实际 Q1 有销售天数 = 33（每日 top-1 行数也是
      33）；但 (day,product) 全量行是每店 124 行 / 111 个产品（agent 读数的语义未定）
  H4  各行 Order Quantity 是否全为 1 —— sales_bestsellers_aggregated_daily.qty_ordered
      在 Q1 的唯一取值
  H5  按品牌（产品名首词）聚合的 top-1 是否 = 参考答案 Sprite

用法：
  uv run python examples/p7_verify_bestsellers_brand.py
  uv run python examples/p7_verify_bestsellers_brand.py --date-from 2022-01-01 --date-to 2022-03-31 --expected Sprite

复用说明（这类任务的验证标准姿势）：
  1. 地面真值优先走环境 DB（docker exec + mysql 物化表），比复刻 UI 操作可靠——
     UI 路径见 p7_probe_*.py 系列：bestsellers 页的 Show Report 按钮在全新浏览器
     会话里没有任何监听（jQuery 事件空、无 onclick），JS 设值+trusted click+
     URL 直参数全部无法让网格出数据；agent 当时（9222 会话）显然能触发 AJAX 重载，
     差异原因未定，留作环境谜团。
  2. DB 凭据从容器内 Magento env.php 读取，勿硬编码进仓库。
  3. brand = SUBSTRING_INDEX(product_name, ' ', 1)（产品名首词；WebArena 数据集
     品牌编码在产品名里，DB 无独立 brand 属性列——2026-08-16 验证时确认）。
"""

import argparse
import html
import subprocess
import sys
from collections import defaultdict

CONTAINER = "shopping_admin"
# 凭据来自容器内 /var/www/magento2/app/etc/env.php（db.connection.default）
MYSQL = ["mysql", "-umagentouser", "-pMyPassword", "magentodb", "-N", "-B"]


def run_sql(sql: str, container: str = CONTAINER) -> str:
	"""docker exec 进容器跑 SQL，返回原始 TSV 文本。"""
	res = subprocess.run(
		["docker", "exec", container, *MYSQL, "-e", sql],
		capture_output=True, text=True, encoding="utf-8", timeout=60,
	)
	if res.returncode != 0:
		raise RuntimeError(f"SQL 失败: {res.stderr.strip()[:300]}")
	return res.stdout.strip()


def main() -> int:
	ap = argparse.ArgumentParser(description="Task 1 bestsellers 品牌聚合地面真值验证")
	ap.add_argument("--container", default=CONTAINER)
	ap.add_argument("--date-from", default="2022-01-01")
	ap.add_argument("--date-to", default="2022-03-31")
	ap.add_argument("--expected", default="Sprite", help="参考答案（品牌名）")
	args = ap.parse_args()

	sql = lambda s: run_sql(s, args.container)

	where = f"period BETWEEN '{args.date_from}' AND '{args.date_to}'"

	# ── 维度总览：store × 行数 × qty ──
	print(f"=== 维度总览（{args.date_from} ~ {args.date_to}） ===")
	out = sql(
		f"SELECT store_id, COUNT(*), SUM(qty_ordered), COUNT(DISTINCT product_id), "
		f"COUNT(DISTINCT period) FROM sales_bestsellers_aggregated_daily "
		f"WHERE {where} GROUP BY store_id"
	)
	days = 0
	for line in out.splitlines():
		store, rows, qty, n_prod, n_days = line.split("\t")
		days = max(days, int(n_days))
		print(f"  store {store}: {rows} 行 / qty 合计 {qty} / {n_prod} 个产品 / {n_days} 个有销售天数")

	# ── H4：qty 取值集合 ──
	qtys = sql(
		f"SELECT DISTINCT qty_ordered FROM sales_bestsellers_aggregated_daily "
		f"WHERE {where} ORDER BY qty_ordered"
	).splitlines()
	qty_set = [q.split("\t")[0] for q in qtys]
	h4 = qty_set == ["1.0000"]
	print(f"\n[{'PASS' if h4 else 'FAIL'}] H4 qty 全为 1（实际取值集合: {qty_set}）")

	# ── H2：33 的语义 ──
	h2 = days == 33
	print(f"[{'PASS' if h2 else 'INFO'}] H2 「33 records」与有销售天数吻合"
		f"（实际 {days} 天；行数语义见上，agent 读数的确切含义未定）")

	# ── H5：品牌聚合（store_id=1 单店口径，避免 0/1 双份重复计数）──
	out = sql(
		f"SELECT SUBSTRING_INDEX(product_name, ' ', 1), SUM(qty_ordered), "
		f"COUNT(DISTINCT product_id) FROM sales_bestsellers_aggregated_daily "
		f"WHERE {where} AND store_id = 1 "
		f"GROUP BY SUBSTRING_INDEX(product_name, ' ', 1) ORDER BY SUM(qty_ordered) DESC, 1"
	)
	ranking = []
	for line in out.splitlines():
		brand, qty, n_prod = line.split("\t")
		ranking.append((html.unescape(brand), int(float(qty)), int(n_prod)))

	print("\n=== 品牌聚合（qty 降序 top 8） ===")
	for brand, qty, n_prod in ranking[:8]:
		print(f"  {qty:>3}  {brand}（{n_prod} 个产品）")
	if len(ranking) >= 2:
		gap = ranking[0][1] - ranking[1][1]
		print(f"  第 1 与第 2 差距: {gap}")

	top = ranking[0] if ranking else None
	h5 = top is not None and top[0].lower() == args.expected.lower()
	print(f"\n[{'PASS' if h5 else 'FAIL'}] H5 top-1 品牌 = {args.expected}（实际 {top}）")

	# 附：rating_pos=1（每日 top-1）口径对照——该口径下品牌并列，不构成参考答案口径
	out = sql(
		f"SELECT SUBSTRING_INDEX(product_name, ' ', 1), COUNT(*) "
		f"FROM sales_bestsellers_aggregated_daily "
		f"WHERE {where} AND store_id = 1 AND rating_pos = 1 "
		f"GROUP BY SUBSTRING_INDEX(product_name, ' ', 1) ORDER BY COUNT(*) DESC, 1 LIMIT 5"
	)
	print("\n=== 对照：rating_pos=1（每日 top-1）口径 top5 ===")
	for line in out.splitlines():
		brand, n = line.split("\t")
		print(f"  {n:>3}  {html.unescape(brand)}")

	return 0 if (h4 and h5) else 1


if __name__ == "__main__":
	sys.exit(main())

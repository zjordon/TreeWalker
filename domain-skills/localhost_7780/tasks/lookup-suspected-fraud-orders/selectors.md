# Selectors — localhost

筛选面板中各元素均有可见标签（Purchase Date / Grand Total / Purchase Point / ID / Bill-to Name / Ship-to Name / Status / Braintree Transaction Source）且在 sop_md 中已就地描述，无需额外指纹表。唯一需要定位依据：Status 下拉 = `name=status, aria-label=notice-CLS6LPS`（id 为随机生成，每次刷新会变，不要依赖 id）。
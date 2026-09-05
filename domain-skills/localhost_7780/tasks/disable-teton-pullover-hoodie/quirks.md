# Quirks — localhost

1. **status 复选框语义反直觉**（stage 78_4）："Enable Product" 复选框 checked=false 且 value=2 才代表 Disabled。禁用 = 取消勾选，不是勾选。判断成功看产品列表 Status 列文字 "Disabled"，不要靠 checked 属性直觉。
2. **只需改父产品**：Teton Pullover Hoodie 是 Configurable Product（MH02），含 15 个尺码/颜色变体（Simple Product，均 Enabled）。禁用父产品即可整体下架，不要逐个禁用变体。
3. **搜索前先清过滤器**（stage product_2）：若上次会话留有 Active filters，fulltext 搜索可能不生效或结果集是全量 2040 条；先点 "Clear all" 再输入关键词。
4. **编辑页元素 id 不稳定**：表单控件 id（如 BQG6PKS、GT0AOJ9）每次进入页面都会重新生成，必须靠 name 属性定位，勿记忆 index/id。
5. **保存是同步跳转**：点 Save 后页面显示 "Please wait..." 并重载，随后停在编辑页；需再点 Back 才回产品列表验证。
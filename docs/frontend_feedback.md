# Find Funding 页面修改需求

来源：Solveathon2026 文档 "Testing" tab 的测试笔记。现网站：`#today`（Today's short list）和 `#match`（Find Funding · Set your qualifiers）。

---

## 1. 默认打开 Today's short list，不带筛选

**现状**：用户要先在 Find Funding 设一堆 qualifiers，再点 Run match。

**改成**
- 一打开网站就显示 **Today's short list**：只列建议 **Chase** 的 grant，不加任何筛选。
- 列表上方加一行提示，引导用户去筛选：
  > Want something specific? **Set filters** to narrow by division, money type, deadline or confidence.
- Find Funding 改成一个可选的筛选工具，不再是必经的第一步。

## 2. 加搜索框：按 grant 名称搜

- 放在 Today 和 Find Funding 两个页面的顶部。
- 支持按名称、Opportunity # 或 agency 搜，边打字边出结果。
- 占位文字：`Search by grant name, number or agency`

## 3. 筛选结果即时显示，不要重跑整个 workflow

**现状**：页面已经显示 "4 items pass your qualifiers"，用户其实已经知道是哪 4 个、每个的字段是什么，但点 Run match 之后还会重新跑一遍 collect → score → write → apply。

**改成**
- 改筛选条件时，结果列表**马上更新**，直接用 pipeline 当天已经写好的数据，不重新收集、不重新打分。
- 只有"写 decision card"这一步按需触发：用户点开某个 grant 时，才生成或加载它的卡片。
- **Run match** 按钮改名为 **Show N grants**（N 是当前条数），或者干脆去掉。

## 4. 0 个匹配时直接告诉用户

筛选结果为 0 时：
- 立刻显示，不要再跑一遍：
  > **No grants match these filters.** Try widening the deadline window or lowering the confidence threshold.
- 同时给一个 **Reset filters** 按钮。
- 不显示 Run match。

## 5. Money type 改成互斥且穷尽的分类

**现状**：只有 `competitive` 和 `one-time disaster` 两个选项。它们用的不是同一个标准（一个讲怎么分钱，一个讲多久来一次），所以一个 grant 可以同时属于两个，也可以两个都不属于（比如每年的公式拨款）。

**改成**，按"怎么分钱"分，每个 grant 只属于一类：

| 选项 | 说明（显示为 tooltip） |
|---|---|
| **Formula** | Every state gets a share; almost certain if eligible |
| **State-only competition** | Only states compete; limited awards |
| **Open competition** | Anyone can apply, incl. universities and nonprofits |
| **Unknown** | The listing doesn't say |

- 对应模型里 Chance 的分法：formula / state-only / competitive / 默认。
- `one-time disaster` 不再当分类，改成卡片上的小标签（tag）：**One-time**。

## 6. Confidence 用数字表示

**现状**：`Any / Medium+ / High`，用户不清楚 Medium 和 High 具体是什么意思。

**改成**
- 换成一个可以拖动的滑条：**Minimum confidence: 70%+**，范围 0–100%，步长 10%。
- 卡片上也直接显示百分比，比如 `Confidence 85%`，不要只写 "High confidence"。
- 如果要保留文字标签，就写清楚范围，比如 `High (80%+)`、`Medium (50–79%)`。

## 7. Suggestions 区的选项有冲突，删掉重复的

**现状**

| 选项 | 问题 |
|---|---|
| Include Low-confidence items | 和上面的 Minimum confidence 冲突：已经选了 High，就默认不要 low-confidence 的了 |
| Include items the scorer suggests Skip | 和上面的 Suggested Chase/Waitlist/Skip/Route 冲突：已经只勾了 Chase，就不会要 Skip 的 |
| Hide items I already skipped | 不冲突，这是用户自己的操作记录 |

**改成**
- 删掉前两个 checkbox。
- 只保留 **Hide items I already skipped**。

---

## 改完后的 Find Funding 结构

```
[ Search by grant name, number or agency ]

01 · Division        多选；不选 = 全部
02 · Money type      Formula · State-only competition · Open competition · Unknown
03 · Timing          Closes within: Any · 14d · 30d · 60d · 90d · 6mo
     Confidence      滑条 0–100%（默认 0%）
04 · Suggestion      Chase · Waitlist · Skip · Route
     [ ] Hide items I already skipped

→ 结果列表实时更新：显示 "N grants"
→ 0 个时显示提示 + Reset filters
→ 点开某个 grant 才加载它的 decision card
```

---

## 附：和最终模型不一致的地方（不在测试笔记里，是我看网站时发现的）

- **Chase 线**：卡片上写 "Final score 83 is at or above the **Chase line 60**"。最终模型的 Chase 线是 **38**，公式是 `Fit × (0.30 + 0.20 Chance + 0.30 Value + 0.15 Urgency + 0.05 Effort)`。网站上的分数和 Chase 线要跟最终模型对齐。
- **Cooperative Endangered Species**：网站建议 **Chase**，由 DNCR Natural Heritage 申请。最终模型把它 **Route** 给 NC Wildlife Resources Commission，因为 Section 6 合作协议通常在 WRC 手里。两边要统一，或者在卡片上写明 "confirm who holds the Section 6 agreement"。

---

## 8. Found while preparing the demo (11/7 evening)

**AI suggestion filter works backwards.** On Find Funding, Chase, You decide, Waitlist and Route start
selected; clicking "Chase" removes Chase items instead of showing only them. Collections Stewardship
disappeared this way during demo prep. Fix: start with nothing selected (= show all) and make a click
mean "show only these", or label the row "Hide:".

**"Priority" badge reads like a grade.** A 52 in an orange circle looks like 52/100, but the Chase line is
38 and the day's top score is about 64. "Priority" also suggests urgency, which is only 15% of the score.
Options, smallest first:
1. Add the reference: `52 · Chase >= 38`.
2. Rename to "Match score".
3. Show the day's rank instead (`#3 of 29 today`) and move the raw score into Details.
4. Show the suggested button (Chase / Decide / Route / Skip) in the circle, rank beside it.

**Demo checklist:** reset Saved / Forwarded marks on the demo grant before recording; check that the
site's Chase line and routes match the final rubric (Chase >= 38; Cooperative Endangered Species -> WRC).

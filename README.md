# diting · 谛听

> 谛听，地藏菩萨座下神兽，伏地一听，可辨天下万物真伪。

**diting 是一个"概念索引"工具：允许代码重复，但不允许重复的地方悄悄分叉。**

在 AI 写代码的时代，强行抽取共享抽象的收益在下降，而同一条业务规则散落在多处实现（结算、退款、展示……）是常态。diting 不要求你合并代码，而是在仓库里维护一层**人能读、机器能校验**的索引：

- **概念（Concept）**：一条业务规则，用受控词表写成的 S-P-O 陈述，例如 `会员 · 享受 · 折扣 [条件: 订单金额 >= 200]`；
- **出现点（Occurrence）**：这条规则在代码里的每一处实现，可登记"有意的差异"（variant）；
- **锚点（Anchor）**：出现点绑定的 AST 符号，带内容指纹，代码变了就能发现。

改规则时，Agent 读索引就知道要改哪几处；漏改时，`diting lint` / CI 会拦下来。

## 安装

```bash
pip install -e ".[dev]"     # Python >= 3.10
diting --help
```

## 快速上手

```bash
cd your-repo
diting init                                   # 生成 .diting/ 布局，扫描符号，挖掘候选词表
diting vocab add entity 会员 -s member -s VIP  # 把候选词提升为正式词表（含同义词）
diting vocab add entity 折扣 -s discount
diting vocab add predicate 享受 -s apply --class compute

diting concept add trade.member_discount.rounding "会员折扣金额四舍五入保留两位小数" \
    -S 会员 -P 享受 -O 折扣 -m "condition=订单金额 >= 200" --level critical

diting anchor add trade.member_discount.rounding svc/pricing/discount.py#DiscountCalc.apply -o settlement
diting anchor add trade.member_discount.rounding svc/refund/refund.py#refund_member_discount -o refund
diting anchor add trade.member_discount.rounding web/price_label.py#PriceLabel.format_discount \
    -o web-display --variant 展示时取整到元

diting status
diting lint        # 有 error 时退出码为 1，可直接接入 CI
```

所有命令都支持 `--json`，供 Agent 消费。完整示例见 [`examples/shop`](examples/shop)。

## 查询与影响分析（PR-2）

```bash
diting query 会员折扣怎么取整          # 一句话 → 概念 → 所有出现点（含当前行号、variant、是否漂移）
diting query "VIP discount rounding"  # 同义词、上位词（is-a）都会参与匹配，每条结果附带匹配理由

diting impact                         # 工作区相对 HEAD 的改动（含未跟踪文件）
diting impact --staged                # 只看已暂存的改动
diting impact --base origin/main      # 相对 merge-base 的改动，适合 PR / CI
diting impact --diff change.diff      # 任意 unified diff（'-' 读 stdin）
diting impact --base main --fail-on-review   # 有未同步的兄弟出现点时退出码为 1
```

`impact` 把改动行映射到符号（新旧两侧都会解析），再映射到锚点与概念，输出：

- `changed`：本次改了逻辑（或符号消失）的出现点；
- `review`：同一概念下**没改**的兄弟出现点——这正是"悄悄分叉"的风险点；
- 只改注释/格式（指纹不变）记为 `cosmetic`，不会触发 review；
- `unindexed`：改了逻辑但尚未登记到任何概念的符号，提示补索引。

## 仓库内布局

```
.diting/
├── config.yaml              # 语言、扫描范围、按等级的门禁策略
├── vocab.yaml               # 词表：限界上下文 / 实体 / 谓词 / 同义词 / 上位词
├── concepts/trade/member_discount/rounding.yaml   # 一个概念一个文件，路径 = id
├── edges/trade.member_discount.rounding.yaml      # 概念 → 出现点 → 锚点
├── changesets/              # （规划中）一次规则变更的计划与执行记录
└── .cache/                  # 派生数据（符号表等），gitignore，可随时重建
```

YAML 是唯一事实源，便于 code review；`.cache/` 中的一切都能由源码 + YAML 重建。

## 锚点保鲜

每个锚点保存两种哈希：

| 哈希 | 计算方式 | 不受影响 | 会变化 |
|---|---|---|---|
| fingerprint | 归一化 AST（去注释、docstring、格式） | 移动、重排版、加注释 | 改名、改逻辑 |
| shape | 在 fingerprint 基础上匿名化标识符 | 以上 + 改名 | 改逻辑 |

据此形成四级重锚定：移动 → 自动；改名 → 自动（shape 不变）；逻辑变化 → 需 AI/人确认；符号消失 → 阻断。PR-1 已实现检测（`anchor-drift` / `anchor-unresolved`），自动重锚定在 `sync` 中实现。

## lint 规则（PR-1）

| code | 级别 | 含义 |
|---|---|---|
| `schema` | error | YAML 结构非法 / 路径与 id 不一致 |
| `orphan-edges` | error | 出现点指向不存在的概念 |
| `removed-referenced` | error | 已 removed 的概念仍有出现点 |
| `anchor-unresolved` | error | 锚点符号已不存在 |
| `anchor-drift` | warning | 锚点内容指纹变化，需确认是否同步修改其他出现点 |
| `stale-occurrence` | warning | 概念已升版本，出现点尚未确认 |
| `vocab` | warning | 概念使用了词表外的术语 |
| `no-occurrence` | warning | active 概念没有任何出现点 |

## 路线图

- [x] **PR-1** 骨架：schema、`init / scan / vocab / concept / anchor / lint / status`、Python 提取器
- [x] **PR-2** `query`（一句话 → 出现点，走同义词/上位词）与 `impact --diff`（改动 → 受影响概念）
- [ ] **PR-3** `sync`（自动重锚定）与 `check`（CI 门禁：漏改、过期、按等级分级），增量 = 全量一致性校验
- [ ] **PR-4** `skill`（生成 Agent 使用的 SKILL.md）、GitHub Action 模板、端到端 demo
- [ ] 之后：tree-sitter 多语言提取、性质测试生成、锚点变异测试、基于 git 历史的效果评测集

## 开发

```bash
python -m pytest -q
```

## License

MIT

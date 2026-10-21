# diting · 谛听

> 谛听，地藏菩萨座下神兽，伏地一听，可辨天下万物真伪。

**diting 是一个代码的"概念索引"：允许同一条业务规则在多处重复实现，但不允许这些实现悄悄分叉。**

```text
$ diting check
BLOCK anchor-changed  [critical] trade.member_discount.rounding/refund: logic of svc/refund/refund.py#refund_member_discount changed since it was confirmed
BLOCK sibling-review  [critical] trade.member_discount.rounding/settlement: sibling(s) refund changed but this site was not updated or re-confirmed
BLOCK sibling-review  [critical] trade.member_discount.rounding/web-display: sibling(s) refund changed but this site was not updated or re-confirmed (registered variant: 展示时取整到元)
-- FAIL: 3 blocking, 0 warning(s)
```

---

## 目录

- [为什么需要它](#为什么需要它)
- [核心概念](#核心概念)
- [安装](#安装)
- [五分钟上手：在示例仓库里体验一次漏改](#五分钟上手在示例仓库里体验一次漏改)
- [在你自己的仓库落地](#在你自己的仓库落地)
- [日常工作流](#日常工作流)
- [接入 CI 门禁](#接入-ci-门禁)
- [接入编码 Agent](#接入编码-agent)
- [命令参考](#命令参考)
- [工作原理](#工作原理)
- [门禁规则与 lint 规则](#门禁规则与-lint-规则)
- [配置](#配置)
- [仓库内布局与数据格式](#仓库内布局与数据格式)
- [设计取舍](#设计取舍)
- [当前限制](#当前限制)
- [开发与回归](#开发与回归)
- [路线图](#路线图)

---

## 为什么需要它

传统做法是"不要重复自己"：把同一条规则抽成共享函数或公共库。但在 AI 写代码的时代，这个权衡变了：

- **重复的代价下降了。** Agent 写一份实现几乎零成本，强行抽象反而带来跨团队耦合、发布协调、"改一处牵动全身"的风险。
- **分叉的代价没变。** 结算按四舍五入、退款按向下取整、页面又按另一套展示——这种"同一条规则、多处实现、悄悄不一致"才是真正出事故的地方。
- **难点在"知道还有哪几处"。** 改规则的人（或 Agent）通常只知道眼前这一处；其余几处散落在别的服务、别的目录、别的命名里，`grep` 很难找全。

diting 不要求你合并代码，而是在仓库里维护一层**人能读、机器能校验**的索引：

1. **改之前**：用一句话（中英文都行）查出这条规则的所有实现位置；
2. **改之后**：自动算出"你改了哪几处、哪几处同规则的实现还没动"；
3. **合并前**：CI 门禁拦下漏改——即使漏改发生在几次提交之前，或者改的人只签收了自己那一处。

## 核心概念

| 概念 | 是什么 | 例子 |
|---|---|---|
| **词表（Vocabulary）** | 受控术语：限界上下文、实体、谓词，以及同义词、上位词 | 实体 `会员`（同义词 `member`、`VIP`），谓词 `享受`（同义词 `apply`） |
| **概念（Concept）** | 一条业务规则，用词表写成的 **主-谓-宾（S-P-O）陈述**，带修饰语、等级、版本 | `trade.member_discount.rounding`：会员 · 享受 · 折扣 [条件: 订单金额 >= 200]，"会员折扣金额四舍五入保留两位小数" |
| **出现点（Occurrence）** | 这条规则在代码里的一处实现，可登记"有意的差异"（variant） | `settlement`、`refund`、`web-display`（variant：展示时取整到元） |
| **锚点（Anchor）** | 出现点绑定的 AST 符号，带内容指纹与签收状态 | `svc/refund/refund.py#refund_member_discount` |
| **签收（Confirm）** | 人或 Agent 核对后声明"这处实现与概念一致" | `diting confirm trade.member_discount.rounding refund` |

概念用受控词表写成 S-P-O，是为了让"同一条规则"有一个与代码命名无关的、稳定的身份：代码里叫 `apply`、`refund_member_discount` 还是 `format_discount` 都没关系，它们都指向同一个概念。

## 安装

要求 Python ≥ 3.10，唯一运行时依赖是 PyYAML。目前未发布到 PyPI，从 GitHub 安装：

```bash
pip install "diting @ git+https://github.com/Meepoljd/diting@main"
diting --version
```

生产 / CI 中建议钉到具体 commit，避免 main 变更影响门禁结果：

```bash
pip install "diting @ git+https://github.com/Meepoljd/diting@<commit-sha>"
```

本地开发：

```bash
git clone https://github.com/Meepoljd/diting && cd diting
pip install -e ".[dev]"
```

## 五分钟上手：在示例仓库里体验一次漏改

[`examples/shop`](examples/shop) 里，"会员折扣四舍五入保留两位小数"这条规则被**有意**实现了三次：结算（`svc/pricing`）、退款（`svc/refund`）、页面展示（`web`，登记了 variant：展示时取整到元）。它的 `.diting/` 已经建好。

```bash
cp -r examples/shop /tmp/shop && cd /tmp/shop
git init -q && git add -A && git commit -qm init    # 让后面可以用 git 撤回改动

diting query 会员折扣怎么取整
```

```text
query: 会员折扣怎么取整
  vocabulary hits: 会员, 折扣

trade.member_discount.rounding  [active/critical v1]  score 6.857
  会员折扣金额四舍五入保留两位小数
  why: subject=会员; object=折扣; text~会员,员折,折扣
  - settlement
      svc/pricing/discount.py#DiscountCalc.apply  L10-14  [verified]
  - refund
      svc/refund/refund.py#refund_member_discount  L4-8  [verified]
  - web-display  (variant: 展示时取整到元)
      web/price_label.py#PriceLabel.format_discount  L5-10  [verified]
```

现在只改退款这一处（四舍五入 → 向下取整），模拟一次漏改：

```bash
sed -i 's/rounding=ROUND_HALF_UP)/rounding=ROUND_DOWN)/; s/from decimal import ROUND_HALF_UP,/from decimal import ROUND_DOWN, ROUND_HALF_UP,/' svc/refund/refund.py
diting check; echo "exit=$?"
```

你会看到开头那段输出：退款处 `anchor-changed`，结算和展示两处 `sibling-review`，退出码 1。接下来有两种合法的收尾方式：

```bash
# A. 撤回改动：check 恢复通过
git checkout -- svc/refund/refund.py
diting check                               # PASS

# B. 规则确实要变：把结算也改掉，复核展示处的 variant 仍然成立，然后签收全部
sed -i 's/rounding=ROUND_HALF_UP)/rounding=ROUND_DOWN)/; s/from decimal import ROUND_HALF_UP,/from decimal import ROUND_DOWN, ROUND_HALF_UP,/' svc/pricing/discount.py
diting confirm trade.member_discount.rounding --note "全部改为向下取整；展示处仍取整到元"
diting check                               # PASS
```

如果只签收退款这一处（`diting confirm trade.member_discount.rounding refund`），结算和展示**仍会被拦**——这正是 diting 要防的情况。

## 在你自己的仓库落地

```bash
cd your-repo
diting init
```

`init` 会创建 `.diting/`、扫描所有 Python 符号，并从标识符里挖掘候选词表写入 `.diting/vocab.candidates.yaml`。然后按下面四步建立第一条概念：

```bash
# 1. 词表：把候选词提升为正式术语（同义词让中英文、代码命名都能命中）
diting vocab add context 交易
diting vocab add entity 会员 -s member -s VIP
diting vocab add entity 折扣 -s discount
diting vocab add predicate 享受 -s apply --class compute

# 2. 概念：一条规则 = 一个 S-P-O 陈述；涉及钱、安全、合规的用 critical
diting concept add trade.member_discount.rounding "会员折扣金额四舍五入保留两位小数" \
    --context 交易 -S 会员 -P 享受 -O 折扣 -m "condition=订单金额 >= 200" --level critical

# 3. 锚点：把每一处实现挂上去；有意的差异用 --variant 登记
diting anchor add trade.member_discount.rounding svc/pricing/discount.py#DiscountCalc.apply -o settlement
diting anchor add trade.member_discount.rounding svc/refund/refund.py#refund_member_discount -o refund
diting anchor add trade.member_discount.rounding web/price_label.py#PriceLabel.format_discount \
    -o web-display --variant 展示时取整到元

# 4. 校验并提交
diting lint
diting check
git add .diting && git commit -m "diting: index member discount rounding"
```

建议从**最容易出事故的几条规则**开始（金额计算、权限判断、校验规则、对外展示口径），而不是试图一次索引整个仓库。之后两件事几乎不需要额外维护：

- 函数被移动、改名时，`diting sync` 自动跟随；
- CI 会持续提醒"改了逻辑但还没登记到任何概念"的符号（`impact` 输出中的 `unindexed`）。

## 日常工作流

无论是人还是 Agent，改业务规则都走同一条路径：

```text
query ──> 改全部出现点 ──> impact ──> sync ──> confirm ──> check ──> 提交代码 + .diting/
            │
            └─ 规则本身变了？先 concept bump
```

| 步骤 | 命令 | 作用 |
|---|---|---|
| 1. 找全 | `diting query "<一句话描述规则>"` | 走同义词、上位词匹配，列出每个出现点的路径、符号、当前行号、variant |
| 2. 改全 | （编辑代码）；规则变了先 `diting concept bump <id> --statement "<新陈述>"` | bump 后版本 +1，所有出现点都必须对照新陈述重新签收 |
| 3. 自查 | `diting impact` / `diting impact --base origin/main` | `changed`：你改了的出现点；`review`：同规则但你没改的出现点；`unindexed`：改了但未登记的符号 |
| 4. 重锚定 | `diting sync` | 移动 / 改名自动跟随；逻辑变化标 `suspect`；找不到标 `lost` |
| 5. 签收 | `diting confirm <id> [occ...] --note "<为什么一致>"` | 核对后签收；不写出现点 = 签收全部 |
| 6. 门禁 | `diting check --base origin/main` | 和 CI 跑的是同一个检查；退出码 1 = 会被拦 |

几条约定：

- **只在真正核对过之后才 `confirm`。** 签收是"这处与概念一致"的署名，不是让检查变绿的开关。
- **登记了 variant 的出现点也会被要求复核**：同步规则的公共部分，保留登记过的差异，确认后签收即可。
- **`.diting/` 与代码一起提交、一起 review。** 不要手工编辑 `edges/` 里的 `fingerprint`、`basis`、`state`，用 `sync` / `confirm`。

## 接入 CI 门禁

```bash
diting ci-template -o .github/workflows/diting.yml
# 钉版本：diting ci-template -o .github/workflows/diting.yml --pip "diting @ git+https://github.com/Meepoljd/diting@<commit-sha>"
git add .github/workflows/diting.yml && git commit -m "ci: diting gate"
```

生成的工作流（快照见 [`docs/diting-check.yml`](docs/diting-check.yml)）：

- `actions/checkout` 使用 `fetch-depth: 0`，以便计算与目标分支的 merge-base；
- **pull_request**：运行 `diting check --base "origin/<base_ref>"`，在签收状态检查之上叠加整个 PR 的 diff 影响分析；
- **push 到 main**：运行 `diting check`；
- 有阻断级发现时退出码为 1，PR 变红。是否阻断由概念等级与 [`gate` 配置](#配置) 决定。

把这个 job 设为分支保护的必需检查（Settings → Branches → Require status checks），漏改就无法合并。

不用 GitHub Actions 时，在任意 CI 中执行同样的命令即可：

```bash
pip install "diting @ git+https://github.com/Meepoljd/diting@<commit-sha>"
diting check --base "origin/$TARGET_BRANCH"     # 需要完整 git 历史
```

`check` 默认还会做一次 L0 自检（增量索引 == 全量重建）；在超大仓库上若想跳过，加 `--no-verify-index`。

## 接入编码 Agent

```bash
diting skill                                             # 写入 .diting/SKILL.md
diting skill --stdout > .claude/skills/diting/SKILL.md   # 或直接放进 Agent 的 skill 目录
```

`SKILL.md` 带有 `name` / `description` 头信息（便于支持 skill 的 Agent 自动触发），正文教 Agent 按[日常工作流](#日常工作流)行事：改规则前先 `query`、改全部出现点、用 `impact` 自查、`sync` / `confirm`、最后跑和 CI 一样的 `check`，以及遇到每种 finding 该怎么处理、如何为新规则建索引。也可以在 `AGENTS.md` / `CLAUDE.md` 里引用它。

所有命令都支持 `--json`（加 `--compact` 输出单行），Agent 应始终用 JSON 解析结果。错误统一返回 `{"ok": false, "error": "..."}`，退出码 2。

`skill` 和 `ci-template -o` 都是幂等的：内容相同时报告 `unchanged`；本地改过的文件不会被覆盖，除非加 `--force`。

## 命令参考

通用参数：`--json`（机器可读输出）、`--compact`（单行 JSON）、`--root PATH`（仓库根，默认从当前目录向上查找 `.diting/`）。

### 建立索引

| 命令 | 说明 |
|---|---|
| `diting init` | 创建 `.diting/` 布局，扫描符号，挖掘候选词表 |
| `diting vocab add {context\|entity\|predicate} <名称> [-s 同义词 ...] [--hypernym 上位实体] [--class compute\|validate\|display\|persist\|permission\|other] [--context C] [--description D]` | 新增或扩展术语 |
| `diting vocab list` | 查看词表 |
| `diting concept add <id> "<陈述>" -S <主语> -P <谓语> -O <宾语> [-m key=value ...] [--context C] [--level critical\|normal\|info] [--lifecycle draft\|active\|deprecated\|removed] [--force]` | 声明概念；id 为点分小写下划线，如 `trade.member_discount.rounding` |
| `diting concept list` | 列出概念 |
| `diting anchor add <概念id> <path>#<Qual.name> [-o 出现点id] [--variant "差异"] [--channel App\|Web\|backend]` | 把出现点挂到代码符号；同一出现点可挂多个锚点 |
| `diting lint` | 结构、词表、锚点有效性检查 |
| `diting status` | 索引概况 |

### 查询与影响分析

| 命令 | 说明 |
|---|---|
| `diting query "<一句话>" [--limit N] [--min-score S] [--include-removed]` | 一句话 → 概念 → 出现点；每条结果附匹配理由 |
| `diting impact` | 工作区相对 HEAD 的改动（含未跟踪文件） |
| `diting impact --staged` | 只看已暂存的改动 |
| `diting impact --base <REF>` | 相对与 REF 的 merge-base 的改动，适合 PR |
| `diting impact --diff <FILE\|->` | 任意 unified diff（`-` 读 stdin） |
| `diting impact ... --fail-on-review` | 存在未同步的兄弟出现点时退出码 1 |

### 保鲜与门禁

| 命令 | 说明 |
|---|---|
| `diting sync [--dry-run]` | 自动重锚定，见[工作原理](#sync-决策树) |
| `diting confirm <概念id> [出现点 ...] [--note "..."]` | 签收；不写出现点 = 全部；输出 `pending_siblings`（仍待复核的兄弟出现点） |
| `diting concept bump <概念id> [--statement "..."] [-m key=value ...]` | 规则本身变更：版本 +1，所有出现点变为过期 |
| `diting check [--base REF \| --staged \| --diff FILE] [--no-verify-index]` | 门禁；有阻断项时退出码 1 |
| `diting index [--full] [--verify]` | 增量更新符号缓存；`--verify` 断言增量结果 == 全量重建 |
| `diting scan` | 全量重建符号缓存并列出符号 |

### 集成

| 命令 | 说明 |
|---|---|
| `diting skill [-o PATH \| --stdout] [--force]` | 生成 Agent 指南 `SKILL.md` |
| `diting ci-template [-o PATH] [--pip SPEC] [--force]` | 生成 GitHub Actions 门禁工作流（不带 `-o` 时输出到 stdout） |

### 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功 / 门禁通过 |
| 1 | 门禁未通过（`check` 有阻断项、`impact --fail-on-review` 有待复核项、`index --verify` 不一致） |
| 2 | 使用错误（未知概念、未初始化、参数错误等），JSON 模式下返回 `{"ok": false, "error": ...}` |

## 工作原理

### 符号与哈希

diting 用 Python 的 `ast` 提取每个函数、方法、类，生成两个哈希：

| 哈希 | 计算方式 | 不受影响 | 会变化 |
|---|---|---|---|
| **fingerprint** | 归一化 AST：去掉注释、docstring、格式，以及符号**自身的名字** | 移动、改名、重排版、加注释 | 改逻辑、改内部标识符 |
| **shape** | 在 fingerprint 基础上匿名化所有标识符 | 以上 + 内部变量改名 | 改逻辑结构 |

fingerprint 不含自身名字，所以"纯改名"在 diting 看来就是一次移动，可以自动跟随；只改注释、格式的提交不会触发任何告警。

### sync 决策树

对每个锚点：

1. 符号还在、指纹不变 → `unchanged`（之前是 suspect / lost 的标为 `recovered`）；
2. 符号还在、指纹变了 → `suspect`，需要 `confirm`；
3. 符号不在，别处有**唯一**同指纹的符号 → `moved`（跨文件移动或改名），自动改指向，状态 `auto`；
4. 符号不在，别处有**唯一**同 shape 的符号 → `reshaped`（改名同时改了内部标识符），改指向但标 `suspect`；
5. 否则 → `lost`（有多个候选时列出候选）。

### 漏改检测：family basis

这是 diting 能"跨提交"发现漏改的关键。

- `confirm` 时，每个被签收的锚点记录一个 `basis`：该概念**所有**出现点当时指纹的哈希（family hash）。
- 之后任何一个兄弟出现点改了逻辑，family hash 就变了。
- `check` 对"自己没变、但 `basis` ≠ 当前 family hash"的出现点报 `sibling-review`。

因为 `basis` 只写在被签收的锚点上，所以：

- 改了退款、只签收退款 → 结算和展示的 `basis` 仍是旧的 → 继续被拦；
- 这个状态保存在 `.diting/edges/` 里、随代码提交，**换一个提交、换一个人、换一个 CI 运行都不会丢**；
- 先签收了退款和展示、之后又改了结算 → 退款和展示会被再次要求复核，因为它们签收时看到的"兄弟们"已经变了。

`check --base <REF>` 会额外叠加 PR diff 的影响分析，用于覆盖还没有 `basis` 的旧锚点。

### 查询

`query` 把句子切成词，命中词表中的术语与同义词，沿上位词（is-a）扩展，再与概念的 S/P/O、修饰语、陈述文本做打分匹配。返回的每条结果都带 `why`（命中了主语、宾语，还是文本片段），便于人和 Agent 判断是否找对了。

### 影响分析

`impact` 把 diff 的改动行映射到符号（新旧两侧都解析，删除和移动也能识别），再映射到锚点和概念，产出 `changed` / `review` / `cosmetic`（只改注释、格式，指纹不变）/ `unindexed`。

### L0 一致性

符号缓存是按文件增量维护的。`index --verify`（以及 `check` 默认）会断言"增量结果 == 全量重建"，不一致时报 `index-inconsistent` 并总是阻断，防止缓存 bug 让门禁失真。

## 门禁规则与 lint 规则

### check 发现

| code | 含义 | 修复 |
|---|---|---|
| `anchor-changed` | 出现点的逻辑在签收后改了 | 保证所有出现点一致后 `diting confirm` |
| `sibling-review` | 兄弟出现点改了，本处既没同步也没重新签收 | 同样修改，或确认仍然正确（例如 variant）后 `diting confirm <id> <occ>` |
| `anchor-moved` | 符号被移动 / 改名，可自动修复 | `diting sync`，提交 `.diting/` |
| `anchor-lost` | 符号消失（或改名的同时改了内容） | `diting sync`；仍找不到则 `diting anchor add` 新位置或移除该出现点 |
| `anchor-suspect` | 锚点已被 `sync` 标为 suspect | 复核后 `diting confirm` |
| `stale-occurrence` | 概念升版后出现点尚未重新签收 | 对照新陈述复核后 `diting confirm` |
| `index-inconsistent` | 增量索引与全量重建不一致（总是阻断） | 删除 `.diting/.cache/` 并报告 bug |

概念相关的发现按概念等级经 `gate` 配置分级（`block` / `warn` / `off`）；只有 `active` 和 `deprecated` 的概念参与门禁，`draft` 不拦。`check` 同时包含下面的结构类 lint 规则。

### lint 规则

| code | 级别 | 含义 |
|---|---|---|
| `schema` | error | YAML 结构非法，或文件路径与 id 不一致 |
| `orphan-edges` | error | 出现点指向不存在的概念 |
| `removed-referenced` | error | 已 removed 的概念仍有出现点 |
| `anchor-unresolved` | error | 锚点符号已不存在（`check` 中细分为 moved / lost） |
| `anchor-drift` | warning | 锚点指纹变化（`check` 中升级为按等级门禁的 `anchor-changed`） |
| `stale-occurrence` | warning | 概念已升版，出现点尚未签收 |
| `vocab` | warning | 概念使用了词表外的术语 |
| `no-occurrence` | warning | active 概念没有任何出现点 |

## 配置

`.diting/config.yaml`（`init` 生成，可直接编辑）：

```yaml
version: 1
languages: [python]
include: ["**/*.py"]
exclude:
  - .git/**
  - .diting/**
  - .venv/**
  - venv/**
  - "**/node_modules/**"
  - "**/__pycache__/**"
  - build/**
  - dist/**
# 按概念等级的门禁策略：block（阻断） | warn（只告警） | off（不报）
gate:
  critical: block
  normal: block
  info: warn
```

`gate` 只需写要覆盖的键，其余沿用默认值。例如让 info 级概念完全静默：

```yaml
gate:
  info: "off"     # 注意加引号，YAML 中裸写 off 会被解析成布尔值
```

## 仓库内布局与数据格式

```text
.diting/
├── config.yaml                 # 语言、扫描范围、门禁策略
├── vocab.yaml                  # 词表：上下文 / 实体 / 谓词 / 同义词 / 上位词
├── vocab.candidates.yaml       # init 挖掘的候选词（已 gitignore）
├── concepts/trade/member_discount/rounding.yaml   # 一个概念一个文件，路径 = id
├── edges/trade.member_discount.rounding.yaml      # 概念 → 出现点 → 锚点
├── changesets/                 # 预留：一次规则变更的计划与执行记录
├── SKILL.md                    # diting skill 生成的 Agent 指南（可选）
└── .cache/                     # 派生数据（符号缓存），已 gitignore，可随时重建
```

YAML 是唯一事实源，便于 code review；`.cache/` 里的一切都能由源码 + YAML 重建。

概念文件：

```yaml
id: trade.member_discount.rounding
statement: 会员折扣金额四舍五入保留两位小数
context: 交易
subject: 会员
predicate: 享受
object: 折扣
modifiers:
  condition: 订单金额 >= 200
  manner: 四舍五入保留两位
lifecycle: active        # draft | active | deprecated | removed
level: critical          # critical | normal | info
version: 1
```

边文件（由 `anchor add` / `sync` / `confirm` 维护，不要手工编辑哈希和状态）：

```yaml
concept: trade.member_discount.rounding
occurrences:
- id: web-display
  variant: 展示时取整到元
  anchors:
  - path: web/price_label.py
    symbol: PriceLabel.format_discount
    fingerprint: 594e195fe0d55e95
    shape: a99e68d3d0630be3
    state: verified          # verified | auto | suspect | lost
    confirmed_version: 1     # 签收时的概念版本，落后于概念版本即 stale
    basis: ef0a6c3e039d4e88  # 签收时整个 family 的指纹哈希
```

## 设计取舍

- **允许重复，管住分叉。** diting 不评判代码该不该抽象，只保证"声明为同一条规则的地方"要么一致、要么差异已被登记并签收。
- **受控词表 + S-P-O，而不是自由文本标签。** 规则的身份不依赖代码命名，中英文、不同团队的叫法通过同义词收敛；词表外的术语会被 lint 提醒，防止索引自身"分叉"。
- **AST 指纹，而不是行号或文本 diff。** 移动、改名、格式化、改注释都不算逻辑变化，所以门禁噪音低，重构不会被误伤。
- **签收是显式的人 / Agent 署名。** 工具负责发现"可能不一致"，不臆测语义；是否一致由核对的人或 Agent 签收，并留下 `--note`。
- **状态随代码进版本库。** 签收状态和 family basis 写在 `.diting/edges/` 里，与代码同一个 PR review；门禁结果只取决于仓库内容，可复现，不依赖外部服务或模型调用。
- **按等级分级门禁。** 钱、安全、合规用 `critical` 阻断；不那么重要的规则可以先 `warn` 或 `off`，便于渐进落地。
- **自检优先。** 增量缓存必须与全量重建一致，否则门禁直接失败，而不是给出不可信的结果。

## 当前限制

- **只支持 Python。** 符号提取基于 Python `ast`；多语言（tree-sitter）在路线图中。
- **锚点粒度是函数 / 方法 / 类。** 同一函数里只改了与规则无关的部分，也会触发复核。
- **不做语义判断。** diting 能发现"同规则的几处实现中有一处变了"，但不会判断新实现是否符合陈述；这一步由签收者负责。
- **概念与锚点需要人工或 Agent 建立。** `init` 只提供候选词，不会自动发现重复实现。
- **未发布到 PyPI**，需从 GitHub 安装；`changesets/` 目录为预留功能。

## 开发与回归

```bash
pip install -e ".[dev]"
python -m pytest -q                                            # 单元 / 集成测试
DITING="python -m diting" PYTHONPATH=src bash scripts/e2e.sh    # 端到端回归（已安装时直接 bash scripts/e2e.sh）
```

本仓库 CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在 Python 3.10–3.13 上对每个 PR 和 main 运行：

1. `pytest -q`；
2. `bash scripts/e2e.sh`：在 `examples/shop` 的临时 git 副本上，用**真实 CLI** 走完整工作流，并逐步断言退出码与 JSON 字段，覆盖以下场景：
   - 建索引；
   - `query` 命中三处；
   - 只改退款的 PR 被 `impact` / `check --base` 拦下，只签收退款仍被拦，同步修改并签收全部后放行；
   - 跨文件移动、方法改名由 `sync` 自动跟随；
   - 只改注释不告警；
   - 删除出现点报 `anchor-lost`；
   - `concept bump` 使全部出现点过期；
   - info 级只告警，`gate` 设为 off 时被抑制；
   - 增量索引 == 全量重建，损坏的缓存能被发现；
   - `ci-template` 产物是合法的工作流；
3. `diting ci-template | diff - docs/diting-check.yml`：模板快照一致性。

源码结构：

| 模块 | 职责 |
|---|---|
| `extract/python.py` | Python 符号提取、fingerprint / shape |
| `store.py` | `.diting/` 读写、符号缓存、增量索引 |
| `model.py` | 词表、概念、出现点、锚点数据模型 |
| `bootstrap.py` | `init` 的候选词挖掘 |
| `query.py` | 一句话 → 概念 |
| `impact.py` | diff → 符号 → 锚点 → 概念 |
| `sync.py` | 重锚定决策树 |
| `check.py` | 门禁、family basis、按等级分级 |
| `lint.py` | 结构与词表规则 |
| `templates.py` | `SKILL.md` 与 CI 工作流模板 |
| `cli.py` | 命令行入口 |

## 路线图

- [x] **PR-1** 骨架：schema、`init / scan / vocab / concept / anchor / lint / status`、Python 提取器
- [x] **PR-2** `query`（一句话 → 出现点，走同义词 / 上位词）与 `impact`（改动 → 受影响概念 → 兄弟复核）
- [x] **PR-3** `sync`（自动重锚定）、`confirm`、`concept bump`、`check`（CI 门禁：漏改、过期、按等级分级），增量 == 全量一致性校验
- [x] **PR-4** `skill`（Agent 指南）、`ci-template`（GitHub Actions 门禁）、端到端回归接入 CI
- [ ] tree-sitter 多语言提取（TypeScript / Go / Java）
- [ ] 发布到 PyPI，提供可直接引用的 GitHub Action
- [ ] changesets：一次规则变更的计划、执行与审计记录
- [ ] 根据概念陈述生成性质测试；锚点变异测试
- [ ] 基于 git 历史的效果评测集（历史上的漏改能否被拦下）

## License

MIT

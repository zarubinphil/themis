# Themiz

忒弥斯把办案的机械活从你桌上拿走：读卷宗、找判例，还检查自己写的文书。

[English](README.md) · [Русский](README.ru.md)

[![License](https://img.shields.io/badge/license-community%201.0-blue.svg)](LICENSE) [![Stars](https://img.shields.io/github/stars/zarubinvibe/themiz?style=flat&color=C9A87A)](https://github.com/zarubinvibe/themiz/stargazers) [![Status](https://img.shields.io/badge/status-in%20development-brightgreen.svg)](https://github.com/zarubinvibe/themiz) [![Olympuz](https://img.shields.io/badge/olympuz-family-B8D6EA.svg)](https://github.com/zarubinvibe/athena#olympuz-family)

<p align="center"><img src="docs/assets/pantheon/hero.png" alt="白色大理石的忒弥斯手持天平与放下的剑，站在古典石柱旁，案卷与审阅卡片摊在日光里" width="100%"></p>

<!-- owner-welcome:start -->

> 你好，我是 Fil。
>
> 我做忒弥斯是给自己用的：我厌倦了把晚上耗在案件的机械部分——两百页扫描件、核对日期、检查引用。如果它对你也有用，我很高兴。
>
> 请试一试。有什么坏了，就开一个 issue，我会看。如果喜欢，请给仓库点个星，并告诉那位还在用手做这一切的同行。也欢迎看看 Olympuz 的其他项目：https://zarubinvibe.com
>
> — Filipp Zarubin

<!-- owner-welcome:end -->

## 目录

- [这是什么](#这是什么)
- [它解决什么问题](#它解决什么问题)
- [最大的优势](#最大的优势)
- [工作流程](#工作流程)
- [快速开始](#快速开始)
- [简单对比](#简单对比)
- [简单词汇](#简单词汇)
- [安全与隐私](#安全与隐私)
- [局限](#局限)
- [点亮星标与参与](#点亮星标与参与)

<!-- beginner-readme:start -->

## 这是什么

项目已改名：Themis 现在叫 Themiz，与 Olympuz 家族其他项目写法一致。旧的 GitHub 链接仍会跳转到这里，但已克隆或 fork 的仓库需要执行 `git remote set-url` 才能跟上。

忒弥斯在律师旁边干活。她在你的电脑上读材料，搭出案件地图，找对你有利和不利的判例，起草文书，再交给另一个智能体审阅。决定仍然归你，这是它的构造，不是结尾处的一句免责。

## 它解决什么问题

好律师的时间并没有花在法律上。两百页扫描件。核对日期。检查引用。还有那个一定在第三卷某处的细节。这部分忒弥斯整块拿走，把需要思考的那部分留给你。

## 最大的优势

**最大的优势：** 数字和法条原文由程序给出，不是由模型给出。

**为什么这样更好：** 法定利息、诉讼期限、国家规费和金额大写都由代码计算。法条从你自己硬盘上的语料里逐字引用，所以转述不会悄悄换掉条文。

## 工作流程

案件按阶段推进。每个阶段有自己的智能体，任何文书都不会没经过第二双眼睛就出门。

<!-- workflow-diagram:start -->

<p align="center"><img src="docs/assets/pantheon/takt-zh.png" alt="Pantheon 宽幅大理石场景中的忒弥斯工作流程：从接收到开庭的七个带标注步骤，由蓝色丝线串起，旁边是古典石柱" width="100%"></p>

<!-- workflow-diagram:end -->

| 阶段 | 会发生什么 |
|---|---|
| 1. 接收 | 扫描件、照片和文书落进案件目录 |
| 2. 读取 | Apple Vision 识别、直接取文本、带校验位的要素 |
| 3. 地图 | 当事人、日期、诉求和证据放进同一张图 |
| 4. 判例 | 支持的判例、程序上的招法、对方最好的论点 |
| 5. 合议 | 立场来自争论，而不是一个人的意见 |
| 6. 文书 | 独立的审阅者、格式检查和个人数据守卫 |
| 7. 开庭 | 期限、本地面板、可选的 Telegram 提醒 |

### 第 1 步：把案件材料交过来

你把材料放进案件目录。当事人目录在仓库层面被禁止发布，材料留在本地。

<p align="center"><img src="docs/assets/pantheon/workflow/01-intake.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 1 步，把案件材料交过来" width="100%"></p>

**你会得到：** 一个案子一个目录，后面要用的东西都在里面。

### 第 2 步：扫描件在你的 Mac 上被读出来

识别在本地进行，大约每页一秒半。税号、企业注册号、案号和金额会被提取出来，并用校验位在不联网的情况下核对。

<p align="center"><img src="docs/assets/pantheon/workflow/02-extract.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 2 步，扫描件在你的 Mac 上被读出来" width="100%"></p>

**你会得到：** 可读的文本，关键要素已经核对过。

### 第 3 步：搭起案件地图

事实从文书搬进一张统一的地图：谁、什么时候、主张什么、由什么支撑。另有一个智能体把几个阅读者互相对照。

<p align="center"><img src="docs/assets/pantheon/workflow/03-case-map.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 3 步，搭起案件地图" width="100%"></p>

**你会得到：** 一个可以直接看的地方，不必再把整个卷宗读一遍。

### 第 4 步：正反两面的判例

一个智能体找支持你立场的判例，另一个找程序上的招法，第三个专门找对你不利的判例。检索请求是去标识化的。

<p align="center"><img src="docs/assets/pantheon/workflow/04-research.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 4 步，正反两面的判例" width="100%"></p>

**你会得到：** 在对方提出之前，你先看到争议的两面。

### 第 5 步：五位法学家争论

五个审阅智能体从不同角度把立场拆开再拼回去。分歧正是意义所在：脆弱的论点应该倒在这里，而不是在庭上。

<p align="center"><img src="docs/assets/pantheon/workflow/05-council.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 5 步，五位法学家争论" width="100%"></p>

**你会得到：** 一个已经指出自己弱点的立场。

### 第 6 步：一个写，另一个查

文书由一个智能体写，由另一个没写过它的智能体审阅。审阅之前不允许拼装文书，提交之前会核对格式，每次提交都跑一遍个人数据守卫。

<p align="center"><img src="docs/assets/pantheon/workflow/06-draft.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 6 步，一个写，另一个查" width="100%"></p>

**你会得到：** 一份你以律师身份去改的草稿，而不是需要逐行复核的文本。

### 第 7 步：开庭准备与提醒

期限按工作日历计算，并附上依据的法条。本地面板显示工作的状态。提醒发给你自己的机器人，只带日期。

<p align="center"><img src="docs/assets/pantheon/workflow/07-hearing.png" alt="Pantheon 宽幅大理石场景：Themiz 工作流程第 7 步，开庭准备与提醒" width="100%"></p>

**你会得到：** 开庭准备就绪，而你的修改会教会下一份文书。

## 快速开始

需要一台 Mac 来识别扫描件，还要 Python 3.11 以上、Xcode 命令行工具和 Claude Code。

```bash
git clone https://github.com/zarubinvibe/themiz.git
cd themiz
bash install.sh

# дальше открывайте, чем привычнее:
claude                  # Claude Code
codex                   # Codex CLI
code .                  # VS Code: агент открывается внутри редактора
python3 cockpit/app.py   # только панель в браузере, без агента
```

上面三行就是全部安装。`bash install.sh` 会把一切装好，并且在装任何东西之前先征求同意。它完全不需要智能体，一个普通终端就够了。

**Claude Code。** 在该目录运行 `claude`，再说 `/themiz-setup`。安装会以对话方式进行，一次问一个问题。

**Codex CLI。** 在同一个目录运行 `codex`。同样的智能体和同样的规则已经在项目里了。

**VS Code 或 Cursor。** 用 `code .` 打开目录，在编辑器里启动你的智能体。

**完全不用智能体。** `python3 cockpit/app.py` 会在 `http://127.0.0.1:8800` 打开本地面板，你可以在那里读案卷、盯期限、手工生成文书。

没有 Git？拿 [ZIP](https://github.com/zarubinvibe/themiz/archive/refs/heads/main.zip) 解压即可。安装步骤一样。

第一次做这件事？[上手引导](docs/ONBOARDING.zh.md) 会一步一步带你走完第一次运行，并写清楚每条命令之后你会看到什么。

**你会得到：** 安装会一次一个问题地了解你的执业方向，先下你最需要的法典，最后用你自己的真实文书自检一遍。

## 简单对比

| 方案 | 是什么 | 案件材料放在哪 | 读你的扫描件 | 正反两面的判例 | 起草文书 | 谁来复核 | 价格 |
|---|---|---|---|---|---|---|---|
| **忒弥斯** | 针对单个案件的多智能体助手 | 在你的 Mac 上 | 是，本地识别 | 是，正反两面 | 是，按案件契约 | 另一个智能体，独立角色 | 个人律师免费 |
| 自己动手 | 一位律师和一个卷宗 | 在你手里 | 你自己读 | 一周能读多少算多少 | 是 | 你自己 | 你的工时 |
| ConsultantPlus、Garant | 俄罗斯法律检索系统 | 不放案件材料 | 否 | 检索法条与判例 | 模板 | 你自己 | 订阅 |
| Sudact、法院案卡 | 公开的裁判文书检索 | 不放案件材料 | 否 | 检索裁判文书 | 否 | 你自己 | 免费 |
| ChatGPT、Claude 原样使用 | 通用聊天助手 | 在厂商云端 | 上传文件才行 | 凭模型记忆 | 是 | 你自己 | 订阅 |
| Harvey、CoCounsel | 面向律所的法律 AI | 在厂商云端 | 是 | 是，限于其覆盖的法域 | 是 | 取决于套餐 | 企业合同 |
| Doczilla、FreshDoc | 文书生成工具 | 在厂商云端 | 否 | 否 | 是，按模板 | 你自己 | 订阅 |

名称归各自所有者。此表说明每种方案的用途，而不是评测：别家产品会变，本页不替它们作出承诺。

## 简单词汇

| 词 | 简单解释 |
|---|---|
| Repository | 仓库：Git 保存并记录版本的项目文件夹 |
| Terminal | 终端：你输入命令的窗口 |
| Command | 命令：给电脑的一条指令 |
| Branch | 分支：不影响 `main` 的另一条修改线 |
| Pull Request | 合并请求：请别人审阅并接受你的修改 |
| Case map | 案件地图：把当事人、日期、诉求和证据放在一起的一份文件 |
| Agent | 智能体：只负责一件窄活的助手，比如读扫描件或找判例 |

## 安全与隐私

- 阅读和识别都在你的电脑上；当事人目录在仓库层面被禁止发布。
- 判例检索发出的是去标识化的请求，卷宗不会外传。
- 只有当本地识别一无所获，或者必须确认一个关键要素时，才允许对单页做一次云端检查。没有静默切换到云端这回事。
- Telegram 提醒走你自己的机器人，只带日期和“完成”两个字：没有姓名、案号和金额。
- 每次提交都会跑个人数据守卫，另一个守卫不允许删除案件材料。
- 提交之前会检查文书格式，并且不允许在审阅之前先把文书拼出来。

把当事人材料放到共用电脑之前，请先读 [SECURITY.md](SECURITY.md)。

## 局限

状态：开发中，按照律师全程掌控来设计。主路径走 Claude Code。

- 本地扫描件识别依赖 Apple Vision，只在 macOS 上可用。文本 PDF、DOCX 和 XLSX 在其他系统上也能读。
- 判例检索依赖外部来源，有时会不可用。
- 闸门变红或者少了某个智能体，工作流会停下，而不是替你猜。
- Themiz 不代表你出面，不签任何东西，也不代替律师的判断。
- Windows 和 Linux 上的干净安装还没有验证过。

想更深：[它到底怎么工作](docs/HOW-IT-WORKS.ru.md)（俄语，没有广告）和[完整参考](docs/DETAILS.md)（含智能体名单）。

## 点亮星标与参与

觉得有用？给 Themiz 点亮星标：[https://github.com/zarubinvibe/themiz](https://github.com/zarubinvibe/themiz)。这只要一秒，却决定别人能不能找到这个项目。

想改点什么？流程很短：先 fork 仓库，建一个分支 branch，提交 commit，推送 push，然后开一个 Pull Request。请不要直接向 `main` 推送，发布闸门会拒绝。

发现问题？到 [https://github.com/zarubinvibe/themiz/issues](https://github.com/zarubinvibe/themiz/issues) 开一个 issue，写清楚你运行了什么、发生了什么。

<!-- beginner-readme:end -->

<!-- pantheon-family:start -->
## Olympuz 家族

这是 [Olympuz 家族](https://github.com/zarubinvibe/athena#olympuz-family) 的公开项目之一。表格里的每一行都可以打开仓库，或者直接下载源码压缩包。

| 类型 | 名称 | 做什么 | 获取 |
|---|---|---|---|
| 项目 | Athena | 可携带的智能体操作系统：在新的 Mac 上重建 Claude 与 Codex 的工作环境。 | [仓库](https://github.com/zarubinvibe/athena) · [ZIP](https://github.com/zarubinvibe/athena/archive/refs/heads/main.zip) |
| 项目 | Helioz | 全天候的智能体工作传送带，带可验证的完成标记和按目标做出的夜间决策。 | [仓库](https://github.com/zarubinvibe/helioz) · [ZIP](https://github.com/zarubinvibe/helioz/archive/refs/heads/main.zip) |
| 项目 | Mnemazine | 本地优先的记忆系统：把原始材料变成可复用的、已核验的知识。 | [仓库](https://github.com/zarubinvibe/mnemazine) · [ZIP](https://github.com/zarubinvibe/mnemazine/archive/refs/heads/main.zip) |
| 项目 | Themiz | 面向俄罗斯诉讼的多智能体助手，本地识别扫描件，五位法学家组成合议审阅。 | [仓库](https://github.com/zarubinvibe/themiz) · [ZIP](https://github.com/zarubinvibe/themiz/archive/refs/heads/main.zip) |
| 项目 | Zeuz | 工作流工厂：把一个想法变成带规则、闸门、可观测性和回放的多智能体系统。 | [仓库](https://github.com/zarubinvibe/zeuz) · [ZIP](https://github.com/zarubinvibe/zeuz/archive/refs/heads/main.zip) |
| 项目 | Lynceuz | 以零成本收集公开网页证据；安全路径走完时，它会给出诚实的理由并停下。 | [仓库](https://github.com/zarubinvibe/lynceuz) · [ZIP](https://github.com/zarubinvibe/lynceuz/archive/refs/heads/main.zip) |
<!-- pantheon-family:end -->

## 许可证

Themiz Community Licence 1.0：个人律师免费使用，包括个人执业；组织需要商业许可。见 [LICENSE](LICENSE) 与 [LICENSE.ru.md](LICENSE.ru.md)。

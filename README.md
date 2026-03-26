# CNKIBug 

> 中国知网（CNKI）论文标题批量爬取工具，支持直接打包为 Windows 独立 `.exe`，无需用户安装任何环境。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-0.0.6-orange)

---

##  功能特性

-  输入关键词，自动批量抓取知网论文标题
-  结果自动导出为 `.xlsx` Excel 文件，保存至桌面
-  优先调用系统自带的 **Microsoft Edge**，无需额外安装浏览器驱动
-  完善的错误提示，缺少环境时弹出友好的引导窗口
-  可打包为单文件 `.exe`，双击即用，无需 Python 环境

---

##  使用演示

```
==================================================
  CNKI_Bug_dev  |  copyright by Kaffu_Alcaid
  Version 0.0.6
==================================================
  本软件用于抓取中国知网的论文标题

请输入你要搜索的关键词: 机器学习
请输入想抓取的总页数（纯数字）: 3

[*] 已启动 Microsoft Edge 浏览器
[*] 目标关键词：机器学习
[*] 读取第 1 页...
  -> 抓取到: 机器学习在医学影像诊断中的应用综述
  -> 抓取到: 基于深度学习的自然语言处理研究进展
  ...

==================================================
[*] 共抓取 60 条数据。
[*] 文件已保存至：
    >>> C:\Users\用户名\Desktop\cnki_titles_机器学习.xlsx <<<
==================================================
```

---

##  快速开始

### 方式一：直接运行（推荐普通用户）

1. 前往 [Releases](../../releases) 页面下载最新的 `CNKIBug.exe`
2. 确保电脑已安装 **Microsoft Edge**（Win10/11 通常已预装）
3. 双击 `CNKIBug.exe`，按提示输入关键词和页数即可
4. 请注意：**一定要手动通过知网的滑块人机验证**

> 如提示未找到 Edge，请访问 https://www.microsoft.com/zh-cn/edge/download 下载安装。

### 方式二：源码运行（开发者）

```bash
# 1. 安装依赖
pip install playwright openpyxl

# 2. 安装浏览器驱动（开发环境需要）
playwright install chromium

# 3. 运行
python CNKIBug_v0.0.6.py
```

### 方式三：自行打包为 exe

```bash
pip install pyinstaller
pyinstaller --onefile --console --name CNKIBug CNKIBug_v0.0.6.py
# 生成文件在 dist/CNKIBug.exe
```

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11 |
| 浏览器 | Microsoft Edge（预装或手动安装） |
| Python | 3.10+（仅源码运行需要） |

---

##  项目结构

```
CNKIBug/
├── CNKIBug_vxxxx.py   # 主程序（当前版本）
├── README.md
└── dist/
    └── CNKIBug.exe        # 打包产物（不纳入版本管理）
```

---

##  版本规划

- [x] `v0.0.x` — 基础标题抓取，Edge 支持，exe 打包
- [ ] `v0.1.x` — 支持多关键词批量抓取
- [ ] `v0.2.x` — 抓取更多字段（作者、期刊、年份、引用数）
- [ ] `v1.0` — 图形界面（GUI）

---

##  免责声明

本工具仅供学习与研究使用，请遵守知网用户协议及相关法律法规。爬取频率过高可能触发验证码，请合理设置页数。

---

##  作者

**Kaffu_Alcaid** — 非计算机专业，业余开发，欢迎 Issue 和 PR。

## 🌟 致谢 / Contributors

感谢以下小伙伴对 CNKIBug 做出的突出贡献（包括核心代码、全平台测试等）：

<table>
  <tr>
    <td align="center" width="200px">
      <a href="https://github.com/KaffuAlcaid">
        <img src="https://github.com/KaffuAlcaid.png" width="80px;" alt="KaffuAlcaid"/>
        <br />
        <sub><b>Kaffu_Alcaid</b></sub>
      </a>
      <br />
      核心开发 & 产品架构<br />(不要问我为什么在里边)
    </td>
    <td align="center" width="200px">
      <a href="https://github.com/Speechlessyc">
        <img src="https://github.com/Speechlessyc.png" width="80px;" alt="Speechlessyc"/>
        <br />
        <sub><b>Speechlessyc</b></sub>
      </a>
      <br />
      图标设计 & 测试统筹<br />(大佬跪谢了)
    </td>
  </tr>
  <tr>
    <td align="center" width="200px">
      <a href="https://github.com/cloudw233">
        <img src="https://github.com/cloudw233.png" width="80px;" alt="cloudw233"/>
        <br />
        <sub><b>cloudw233</b></sub>
      </a>
      <br />
      自动化构建<br />(最强⑨级支持)
    </td>
    <td align="center" width="200px">
      <a href="https://github.com/claude">
        <img src="https://github.com/claude.png" width="80px;" alt=""/>
        <br />
        <sub><b>claude</b></sub>
      </a>
      <br />
      文档润色 & 代码改进<br />(感谢一路陪伴)
    </td>
  </tr>
</table>

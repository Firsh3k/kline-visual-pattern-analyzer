# ⚠️ DEPRECATED — 早期 FastAPI 原型（非生产）

本目录是 FIR-31 早期探索方案的遗留：**独立 FastAPI 服务 + Node/Express BFF 通过 HTTP 调用**。
该方案已废弃，**当前线上生产架构不是这个**，请勿运行、勿用于分析、勿以此为准。

## 当前生产架构（以此为准）

见 `../app-builder/`：

```
NestJS BFF（App Builder）──child_process spawn──> python_analyzer.py ──> 仓库根 Python Canonical Core
```

- 不需要独立常驻的 FastAPI 进程；NestJS 直接在 App Builder 环境内用子进程调用环境自带 Python。
- 形态识别唯一 Source of Truth 是仓库根 `core/`（swing_pivot.py / pivot_patterns.py）。

## 本目录文件说明（仅历史留存）

| 文件 | 状态 |
|------|------|
| `main.py` | 早期 FastAPI 服务入口（Express BFF 经 HTTP 调用），已废弃 |
| `standalone_analyzer.py` | 自包含、内联了一份 core 的早期原型（约 2883 行），**不依赖仓库根 core，可能与当前 core 算法漂移，严禁作为分析结果来源** |
| `services/` | 原型残留（空） |

⚠️ `standalone_analyzer.py` 内联的算法是某个历史时点的快照，**不会**随仓库根 `core/` 更新；
任何真实分析、网页、Skill、回测都必须使用仓库根 `core/`，不得引用本文件。

## 为什么保留

仅为完整保留 FIR-31 架构演进的历史记录与可追溯性。生产代码全部在 `../app-builder/`。

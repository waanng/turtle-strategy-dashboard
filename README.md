# 🐢 海龟法则策略看板

基于唐奇安通道 + ATR + 风险预算的量化策略每日自动化看板。

## 策略概要

| 参数 | 默认值 |
|------|--------|
| 入场通道 | 20 日唐奇安通道 |
| 退出通道 | 10 日唐奇安通道 |
| ATR 周期 | 20 日（Wilder 口径） |
| 加仓步长 | 0.5 × ATR |
| 最大单位 | 4 |
| 止损倍数 | 2 × ATR |
| 风险预算 | 账户权益的 1% |

## 标的池

| 代码 | 名称 | 市场 |
|------|------|------|
| 688981.SH | 中芯国际 | A 股 |
| 002594.SZ | 比亚迪 | A 股 |
| 600900.SH | 长江电力 | A 股 |
| 0981.HK | 中芯国际 | 港股 |
| 1211.HK | 比亚迪 | 港股 |

## 自动化更新

- **频率**：每个工作日 8:00 CST
- **数据源**：yfinance
- **部署**：GitHub Actions → GitHub Pages
- **手动触发**：Actions → Daily Turtle Strategy Update → Run workflow

## 本地运行

```bash
# 安装依赖
pip install pandas numpy yfinance matplotlib

# 拉取数据并计算
python scripts/daily_update.py

# 本地预览
cd dist && python3 -m http.server 8080
```

## 文件结构

```
├── .github/workflows/daily_update.yml   # GitHub Actions
├── scripts/
│   ├── turtle_engine.py                 # 核心回测引擎
│   └── daily_update.py                  # 数据 Pipeline
├── src/
│   └── index.html                       # 看板页面
├── dist/                                # 部署输出（gh-pages）
│   ├── index.html
│   └── data/*.json
└── README.md
```

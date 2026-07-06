#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海龟策略每日数据 Update
通过 yfinance 拉取 5 只股票日线，计算回测，生成 JSON 数据文件
可在 GitHub Actions / 本地运行
"""

import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta

# 将 scripts/ 加入路径，确保能找到 turtle_engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

# ── 股票池 ──
STOCKS = {
    '688981.SH': {'yf': '688981.SS', 'name': '中芯国际A', 'market': 'A股'},
    '002594.SZ': {'yf': '002594.SZ', 'name': '比亚迪A', 'market': 'A股'},
    '600900.SH': {'yf': '600900.SS', 'name': '长江电力A', 'market': 'A股'},
    '0981.HK':   {'yf': '0981.HK', 'name': '中芯国际HK', 'market': '港股'},
    '1211.HK':   {'yf': '1211.HK', 'name': '比亚迪HK', 'market': '港股'},
}

# ── 输出目录 ──
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DATA = os.path.join(ROOT_DIR, 'dist', 'data')
os.makedirs(DIST_DATA, exist_ok=True)


class NumpyEncoder(json.JSONEncoder):
    """将 numpy 类型转为 Python 原生类型"""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj) if not np.isnan(obj) else None
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        if isinstance(obj, (pd.Timestamp,)):
            return str(obj)
        return super().default(obj)


def fetch_data_yfinance(max_retries=3, delay=5):
    """
    通过 yfinance 下载 5 只股票日线
    支持重试，A 股有时不稳定
    """
    import yfinance as yf

    data = {}
    for ts_code, info in STOCKS.items():
        yf_code = info['yf']
        name = info['name']
        print(f"[{datetime.now():%H:%M:%S}] 下载 {ts_code} ({yf_code})...", end=' ', flush=True)

        df = None
        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(yf_code)
                df = ticker.history(period='3y', auto_adjust=True)
                if df is not None and not df.empty:
                    break
                print(f"(尝试 {attempt+1}/{max_retries} 为空, 重试...) ", end='', flush=True)
                time.sleep(delay)
            except Exception as e:
                print(f"(尝试 {attempt+1} 失败: {e}) ", end='', flush=True)
                time.sleep(delay)

        if df is None or df.empty:
            print(f"✗ 失败")
            raise RuntimeError(f"Cannot fetch {ts_code} ({yf_code}) after {max_retries} retries")

        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        # yfinance uses 'Date' column
        if 'date' not in df.columns:
            # try capital D
            for c in ['Date', 'index']:
                if c in df.columns:
                    df = df.rename(columns={c: 'date'})
                    break
        data[ts_code] = df
        print(f"✓ {len(df)} 行  {df['date'].iloc[0].strftime('%Y-%m-%d') if hasattr(df['date'].iloc[0], 'strftime') else str(df['date'].iloc[0])[:10]} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d') if hasattr(df['date'].iloc[-1], 'strftime') else str(df['date'].iloc[-1])[:10]}")

    return data


def save_json(filepath, obj):
    """安全写入 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, cls=NumpyEncoder)


def build_summary(results, update_time, params):
    """构建 summary.json 数据结构"""
    stocks_list = []
    for ts_code in STOCKS.keys():
        if ts_code not in results:
            continue
        m = results[ts_code]['metrics'].copy()
        m['ts_code'] = ts_code
        m['name'] = STOCKS[ts_code]['name']
        m['market'] = STOCKS[ts_code]['market']
        stocks_list.append(m)

    return {
        'stocks': stocks_list,
        'update_time': update_time,
        'params': {
            'entry_period': params['entry_period'],
            'exit_period': params['exit_period'],
            'atr_period': params['atr_period'],
            'risk_fraction': params['risk_fraction'],
            'initial_capital': params['initial_capital'],
        }
    }


def main():
    tz_cst = timezone(timedelta(hours=8))
    now = datetime.now(tz_cst)
    update_time_str = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    print("=" * 60)
    print(f"🐢 海龟策略每日更新 — {update_time_str}")
    print("=" * 60)

    # 1. 取数
    data = fetch_data_yfinance()

    # 2. 回测
    from turtle_engine import run_all_stocks

    params = dict(
        initial_capital=100000, commission_rate=0.0003, slippage=0.0001,
        risk_fraction=0.01, add_unit_step=0.5, max_units=4,
        stop_multiple=2.0, entry_period=20, exit_period=10, atr_period=20
    )

    print(f"\n[{datetime.now():%H:%M:%S}] 运行回测...")
    results = run_all_stocks(data, params)

    # 3. 保存 summary.json
    summary = build_summary(results, update_time_str, params)
    save_json(os.path.join(DIST_DATA, 'summary.json'), summary)
    print(f"  ✓ summary.json ({len(summary['stocks'])} 只股票)")

    # 4. 保存每只股票的详细数据
    for ts_code, res in results.items():
        # 净值曲线
        equity_data = res['equity']
        # 只保留最近 200 个点以控制文件大小
        if len(equity_data['labels']) > 200:
            for k in ['labels', 'strategy_equity', 'benchmark_equity', 'drawdown', 'atr']:
                equity_data[k] = equity_data[k][-200:]

        save_json(os.path.join(DIST_DATA, f'{ts_code}_equity.json'), {
            'ts_code': ts_code,
            **equity_data,
        })

        # 信号（最近 100 日）
        save_json(os.path.join(DIST_DATA, f'{ts_code}_signals.json'), {
            'ts_code': ts_code,
            'signals': res['signals'][-20:] if len(res['signals']) > 20 else res['signals'],
            'latest_signal': res['metrics'].get('latest_signal', 'WAIT'),
        })

        # 交易记录
        save_json(os.path.join(DIST_DATA, f'{ts_code}_trades.json'), {
            'ts_code': ts_code,
            'trades': res['trades'][-30:] if len(res['trades']) > 30 else res['trades'],
        })

        print(f"  ✓ {ts_code}: equity({len(equity_data['labels'])}点) signals({len(res['signals'])}条) trades({len(res['trades'])}笔)")

    # 5. 更新时间戳
    save_json(os.path.join(DIST_DATA, 'update_time.json'), {
        'last_update': update_time_str,
        'data_start': str(data[list(STOCKS.keys())[0]]['date'].iloc[0])[:10] if data else '',
        'data_end': str(data[list(STOCKS.keys())[0]]['date'].iloc[-1])[:10] if data else '',
        'status': 'success',
    })

    print(f"\n✓ 全部完成 — {len(DIST_DATA)} 个文件已写入 {DIST_DATA}")
    print("=" * 60)


if __name__ == '__main__':
    main()

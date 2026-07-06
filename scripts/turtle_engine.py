#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海龟法则核心回测引擎（独立模块，零 notebook 依赖）
可在 GitHub Actions / 本地 Python 直接运行
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime

# =============================================================================
# 1. IndicatorCalculator — 唐奇安通道 + ATR
# =============================================================================

class IndicatorCalculator:
    def __init__(self, entry_period=20, exit_period=10, atr_period=20):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period

    def calc_donchian_channel(self, df, period, shift=1):
        df = df.copy()
        df['upper_channel'] = df['high'].rolling(period).max().shift(shift)
        df['lower_channel'] = df['low'].rolling(period).min().shift(shift)
        return df

    def calc_true_range(self, df):
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['prev_close'])
        df['tr3'] = abs(df['low'] - df['prev_close'])
        df['tr'] = pd.concat([df['tr1'], df['tr2'], df['tr3']], axis=1).max(axis=1)
        return df

    def calc_atr(self, df, shift=1):
        df = df.copy()
        if 'tr' not in df.columns:
            df = self.calc_true_range(df)
        df['atr'] = np.nan
        p = self.atr_period
        if len(df) >= p:
            df.loc[p - 1, 'atr'] = df['tr'][:p].mean()
            for i in range(p, len(df)):
                df.loc[i, 'atr'] = (df.loc[i - 1, 'atr'] * (p - 1) + df.loc[i, 'tr']) / p
        df['atr'] = df['atr'].shift(shift)
        return df

    def add_all_indicators(self, df):
        df = df.copy()
        # 先删除可能的旧指标列，避免 rename 产生重复列
        for col in ['entry_upper', 'entry_lower', 'exit_upper', 'exit_lower',
                     'upper_channel', 'lower_channel', 'atr', 'tr',
                     'prev_close', 'tr1', 'tr2', 'tr3']:
            if col in df.columns:
                df = df.drop(columns=[col])
        df = self.calc_atr(df)
        df = self.calc_donchian_channel(df, self.entry_period, shift=1)
        df = df.rename(columns={'upper_channel': 'entry_upper', 'lower_channel': 'entry_lower'})
        df = self.calc_donchian_channel(df, self.exit_period, shift=1)
        df = df.rename(columns={'upper_channel': 'exit_upper', 'lower_channel': 'exit_lower'})
        return df


# =============================================================================
# 2. TurtleSignalGenerator — 入场/退出信号
# =============================================================================

class TurtleSignalGenerator:
    def __init__(self, entry_period=20, exit_period=10, atr_period=20,
                 add_unit_step=0.5, max_units=4, stop_multiple=2.0):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period
        self.add_unit_step = add_unit_step
        self.max_units = max_units
        self.stop_multiple = stop_multiple
        self.calc = IndicatorCalculator(entry_period, exit_period, atr_period)

    def generate_signals(self, df):
        df = df.copy()
        df = self.calc.add_all_indicators(df)
        df['entry_signal'] = 0
        df['exit_signal'] = 0
        for i in range(self.entry_period, len(df)):
            if df.loc[i, 'close'] > df.loc[i, 'entry_upper']:
                df.loc[i, 'entry_signal'] = 1
            if df.loc[i, 'close'] < df.loc[i, 'exit_lower']:
                df.loc[i, 'exit_signal'] = 1
        df['entry_delayed'] = df['entry_signal'].shift(1).fillna(0).astype(int)
        df['exit_delayed'] = df['exit_signal'].shift(1).fillna(0).astype(int)
        return df


# =============================================================================
# 3. PositionManager — 仓位管理
# =============================================================================

class PositionManager:
    def __init__(self, risk_fraction=0.01, add_unit_step=0.5,
                 max_units=4, stop_multiple=2.0, min_lot=100):
        self.risk_fraction = risk_fraction
        self.add_unit_step = add_unit_step
        self.max_units = max_units
        self.stop_multiple = stop_multiple
        self.min_lot = min_lot

    def calc_unit_size(self, equity, atr, price):
        if atr <= 0 or price <= 0:
            return 0
        risk_amount = equity * self.risk_fraction
        shares = int(risk_amount / (atr * price))
        if shares < 1:
            shares = 1
        if self.min_lot > 1:
            shares = (shares // self.min_lot) * self.min_lot
        return max(shares, self.min_lot) if self.min_lot > 1 else max(shares, 1)

    def check_stop_loss(self, positions, current_price):
        stop_indices = []
        for idx, pos in enumerate(positions):
            stop_price = pos['entry_price'] - self.stop_multiple * pos['entry_atr']
            if current_price < stop_price:
                stop_indices.append(idx)
        return stop_indices

    def check_add_unit(self, positions, current_price, atr):
        if len(positions) >= self.max_units or len(positions) == 0:
            return False
        last_price = positions[-1]['entry_price']
        return current_price > last_price + self.add_unit_step * atr


# =============================================================================
# 4. BacktestEngine — 回测引擎
# =============================================================================

class BacktestEngine:
    def __init__(self, initial_capital=100000, commission_rate=0.0003,
                 slippage=0.0001, risk_fraction=0.01, add_unit_step=0.5,
                 max_units=4, stop_multiple=2.0, entry_period=20,
                 exit_period=10, atr_period=20):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.max_units = max_units
        self.signal_gen = TurtleSignalGenerator(
            entry_period, exit_period, atr_period,
            add_unit_step, max_units, stop_multiple
        )
        self.pos_mgr_defaults = dict(
            risk_fraction=risk_fraction, add_unit_step=add_unit_step,
            max_units=max_units, stop_multiple=stop_multiple
        )

    def run(self, df, ts_code):
        df = self.signal_gen.generate_signals(df)
        cash = self.initial_capital
        positions = []
        equity_records = []
        trade_records = []

        for i in range(self.signal_gen.entry_period, len(df)):
            row = df.iloc[i]
            date = row['date']
            o, c, atr = row['open'], row['close'], row['atr']
            entry_sig = row['entry_signal']
            exit_sig = row['exit_signal']

            if pd.isna(o) or pd.isna(c) or pd.isna(atr):
                continue

            is_a = '.SH' in ts_code or '.SZ' in ts_code
            pm = PositionManager(min_lot=100 if is_a else 1, **self.pos_mgr_defaults)

            # 止损
            stop_idxs = pm.check_stop_loss(positions, c)
            if stop_idxs:
                for idx in sorted(stop_idxs, reverse=True):
                    p = positions[idx]
                    exec_p = o * (1 - self.slippage)
                    gross = p['shares'] * exec_p
                    comm = gross * self.commission_rate
                    cash += gross - comm
                    trade_records.append({
                        'entry_date': str(p['entry_date']),
                        'exit_date': str(date),
                        'direction': 'LONG',
                        'entry_price': p['entry_price'],
                        'exit_price': exec_p,
                        'shares': p['shares'],
                        'return_pct': (exec_p - p['entry_price']) / p['entry_price'],
                        'holding_days': (date - p['entry_date']).days,
                        'entry_atr': p['entry_atr'],
                        'stop_loss_triggered': True,
                    })
                    positions.pop(idx)

            # 退出
            if len(positions) > 0 and exit_sig:
                for p in positions:
                    exec_p = o * (1 - self.slippage)
                    gross = p['shares'] * exec_p
                    comm = gross * self.commission_rate
                    cash += gross - comm
                    trade_records.append({
                        'entry_date': str(p['entry_date']),
                        'exit_date': str(date),
                        'direction': 'LONG',
                        'entry_price': p['entry_price'],
                        'exit_price': exec_p,
                        'shares': p['shares'],
                        'return_pct': (exec_p - p['entry_price']) / p['entry_price'],
                        'holding_days': (date - p['entry_date']).days,
                        'entry_atr': p['entry_atr'],
                        'stop_loss_triggered': False,
                    })
                positions = []

            # 入场
            if len(positions) == 0 and entry_sig:
                equity = cash
                unit_shares = pm.calc_unit_size(equity, atr, o)
                if unit_shares > 0:
                    exec_p = o * (1 + self.slippage)
                    cost = unit_shares * exec_p * (1 + self.commission_rate)
                    if cost <= cash:
                        cash -= cost
                        positions.append({
                            'unit_id': 1, 'entry_date': date,
                            'entry_price': exec_p, 'shares': unit_shares, 'entry_atr': atr,
                        })

            # 加仓
            elif 0 < len(positions) < self.max_units and pm.check_add_unit(positions, c, atr):
                equity = cash
                unit_shares = pm.calc_unit_size(equity, atr, o)
                if unit_shares > 0:
                    exec_p = o * (1 + self.slippage)
                    cost = unit_shares * exec_p * (1 + self.commission_rate)
                    if cost <= cash:
                        cash -= cost
                        positions.append({
                            'unit_id': len(positions) + 1, 'entry_date': date,
                            'entry_price': exec_p, 'shares': unit_shares, 'entry_atr': atr,
                        })

            # 记录净值
            pos_value = sum(p['shares'] * c for p in positions)
            total = cash + pos_value
            equity_records.append({
                'date': str(date),
                'cash': round(cash, 2),
                'position_value': round(pos_value, 2),
                'total_equity': round(total, 2),
                'close': c,
                'atr': atr,
                'num_units': len(positions),
            })

        return pd.DataFrame(equity_records), pd.DataFrame(trade_records)


# =============================================================================
# 5. MetricsCalculator — 评价指标
# =============================================================================

class MetricsCalculator:
    def __init__(self, risk_free_rate=0.025):
        self.rfr = risk_free_rate

    def max_drawdown(self, equity_series):
        peak = equity_series.expanding().max()
        dd = (equity_series - peak) / peak
        max_dd = dd.min()
        end = dd.idxmin()
        start = equity_series[:end].idxmax() if not pd.isna(end) else 0
        return max_dd, start, end

    def compute(self, equity_df, trades_df, initial_capital):
        if len(equity_df) == 0:
            return {}
        final = equity_df['total_equity'].iloc[-1]
        cum_ret = (final - initial_capital) / initial_capital
        days = len(equity_df)
        years = days / 252
        ann_ret = (1 + cum_ret) ** (1 / years) - 1 if years > 0 else 0
        daily_r = equity_df['total_equity'].pct_change().dropna()
        ann_vol = daily_r.std() * np.sqrt(252)
        excess = daily_r - self.rfr / 252
        sharpe = excess.mean() / excess.std() * np.sqrt(252) if excess.std() > 0 else 0
        max_dd, dd_start, dd_end = self.max_drawdown(equity_df['total_equity'])
        n_trades = len(trades_df)
        win_rate, p_l_ratio, avg_hold, stop_count = 0, 0, 0, 0
        if n_trades > 0:
            win_rate = (trades_df['return_pct'] > 0).mean()
            avg_win = trades_df[trades_df['return_pct'] > 0]['return_pct'].mean()
            avg_loss = trades_df[trades_df['return_pct'] <= 0]['return_pct'].mean()
            p_l_ratio = abs(avg_win / avg_loss) if (avg_loss != 0 and not pd.isna(avg_loss)) else 0
            avg_hold = trades_df['holding_days'].mean() if 'holding_days' in trades_df.columns else 0
            stop_count = int(trades_df['stop_loss_triggered'].sum()) if 'stop_loss_triggered' in trades_df.columns else 0
        avg_units = equity_df['num_units'].mean() if 'num_units' in equity_df.columns else 0
        max_units_h = int(equity_df['num_units'].max()) if 'num_units' in equity_df.columns else 0
        bench_ret = (equity_df['close'].iloc[-1] - equity_df['close'].iloc[0]) / equity_df['close'].iloc[0]

        latest_signal = 'HOLD'
        if 'num_units' in equity_df.columns and equity_df['num_units'].iloc[-1] > 0:
            latest_signal = 'HOLD'
        else:
            latest_signal = 'WAIT'

        return {
            'cumulative_return': float(cum_ret),
            'annualized_return': float(ann_ret),
            'annualized_volatility': float(ann_vol),
            'sharpe_ratio': float(sharpe),
            'max_drawdown': float(max_dd),
            'max_drawdown_start': str(dd_start),
            'max_drawdown_end': str(dd_end),
            'win_rate': float(win_rate),
            'profit_loss_ratio': float(p_l_ratio),
            'total_trades': int(n_trades),
            'avg_holding_days': float(avg_hold),
            'avg_units': float(avg_units),
            'max_units_held': max_units_h,
            'stop_loss_count': stop_count,
            'benchmark_return': float(bench_ret),
            'excess_return': float(cum_ret - bench_ret),
            'latest_close': float(equity_df['close'].iloc[-1]),
            'latest_atr': float(equity_df['atr'].iloc[-1]) if 'atr' in equity_df.columns else 0,
            'latest_signal': latest_signal,
        }


# =============================================================================
# 6. 入口函数：批量运行
# =============================================================================

def run_all_stocks(data_dict, params=None):
    """
    对多只股票执行海龟策略回测

    Parameters
    ----------
    data_dict : dict
        {ts_code: DataFrame} 字典，DataFrame 需包含 date/open/high/low/close/volume 列
    params : dict, optional
        策略参数，默认使用标准海龟参数

    Returns
    -------
    dict
        {ts_code: {'metrics': {...}, 'equity': [...], 'trades': [...], 'signals': [...]}}
    """
    if params is None:
        params = dict(
            initial_capital=100000, commission_rate=0.0003, slippage=0.0001,
            risk_fraction=0.01, add_unit_step=0.5, max_units=4,
            stop_multiple=2.0, entry_period=20, exit_period=10, atr_period=20
        )

    engine = BacktestEngine(**params)
    mc = MetricsCalculator()

    results = {}
    for ts_code, df in data_dict.items():
        # 标准化列名
        cols = [c.lower() for c in df.columns]
        df.columns = cols
        if 'date' not in df.columns:
            raise KeyError(f"{ts_code}: DataFrame 缺少 date 列")
        for col in ['open', 'high', 'low', 'close']:
            if col not in df.columns:
                raise KeyError(f"{ts_code}: DataFrame 缺少 {col} 列")
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        eq_df, tr_df = engine.run(df, ts_code)
        metrics = mc.compute(eq_df, tr_df, engine.initial_capital)
        metrics['ts_code'] = ts_code

        # 最近 100 日信号
        sig_df = engine.signal_gen.generate_signals(df)
        sig_df_recent = sig_df.tail(150).copy()
        signals = []
        for _, row in sig_df_recent.iterrows():
            if row['entry_signal'] == 1:
                signals.append({'date': str(row['date']), 'type': 'ENTRY', 'price': float(row['close'])})
            if row['exit_signal'] == 1:
                signals.append({'date': str(row['date']), 'type': 'EXIT', 'price': float(row['close'])})

        # 净值曲线 JSON
        # 计算策略平均仓位占比（用于等仓位基准）
        if len(eq_df) > 0 and 'position_value' in eq_df.columns and 'total_equity' in eq_df.columns:
            avg_pos_pct = np.mean(eq_df['position_value'] / eq_df['total_equity'].replace(0, np.nan))
            avg_pos_pct = float(avg_pos_pct) if not np.isnan(avg_pos_pct) else 0
        else:
            avg_pos_pct = 0

        # 等仓位基准：同样仓位比例买入持有（剩余现金不动）
        first_close = eq_df['close'].iloc[0] if len(eq_df) > 0 else 1
        equalpos = params['initial_capital'] * (1 - avg_pos_pct) + \
                   params['initial_capital'] * avg_pos_pct * (eq_df['close'] / first_close)

        equity_data = {
            'labels': eq_df['date'].tolist() if len(eq_df) > 0 else [],
            'strategy_equity': eq_df['total_equity'].tolist() if len(eq_df) > 0 else [],
            'benchmark_equity': (params['initial_capital'] * (1 + (eq_df['close'] - first_close) / first_close)).tolist() if len(eq_df) > 0 else [],
            'equalpos_benchmark': equalpos.tolist() if len(eq_df) > 0 else [],
            'drawdown': ((eq_df['total_equity'] - eq_df['total_equity'].expanding().max()) / eq_df['total_equity'].expanding().max()).tolist() if len(eq_df) > 0 else [],
            'atr': eq_df['atr'].tolist() if len(eq_df) > 0 and 'atr' in eq_df.columns else [],
            'avg_position_pct': avg_pos_pct,
        }

        results[ts_code] = {
            'metrics': metrics,
            'equity': equity_data,
            'trades': tr_df.to_dict('records') if len(tr_df) > 0 else [],
            'signals': signals,
        }

    return results


def export_detail_data(df, ts_code, params=None, recent_days=150):
    """
    导出单只股票的详情图表数据：唐奇安通道 + ATR + 买卖点标记

    Parameters
    ----------
    df : DataFrame
        原始OHLCV数据
    ts_code : str
        标的代码
    params : dict
        策略参数
    recent_days : int
        取最近多少天的数据（图表展示用）

    Returns
    -------
    dict
        {
            'labels': [...],           # 日期
            'close': [...],            # 收盘价
            'entry_upper': [...],      # 入场通道上轨(20日)
            'entry_lower': [...],      # 入场通道下轨(20日)
            'exit_upper': [...],       # 退出通道上轨(10日)
            'exit_lower': [...],       # 退出通道下轨(10日)
            'atr': [...],              # ATR
            'entry_points': [{date, price}, ...],     # 入场信号点
            'exit_points': [{date, price}, ...],      # 退出信号点
            'add_points': [{date, price}, ...],       # 加仓点
            'stop_points': [{date, price}, ...],      # 止损点
        }
    """
    if params is None:
        params = dict(
            entry_period=20, exit_period=10, atr_period=20,
            initial_capital=100000, commission_rate=0.0003, slippage=0.0001,
            risk_fraction=0.01, add_unit_step=0.5, max_units=4, stop_multiple=2.0
        )

    # 标准化
    cols = [c.lower() for c in df.columns]
    df = df.copy()
    df.columns = cols
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # 生成信号（generate_signals 内部会通过 calc.add_all_indicators 完成指标计算）
    sig_gen = TurtleSignalGenerator(
        entry_period=params['entry_period'],
        exit_period=params['exit_period'],
        atr_period=params['atr_period'],
        add_unit_step=params['add_unit_step'],
        max_units=params['max_units'],
        stop_multiple=params['stop_multiple']
    )
    df = sig_gen.generate_signals(df)

    # 运行回测以获取交易记录（含加仓/止损）
    engine = BacktestEngine(**{k: v for k, v in params.items()
                               if k in ['initial_capital', 'commission_rate', 'slippage',
                                         'risk_fraction', 'add_unit_step', 'max_units',
                                         'stop_multiple', 'entry_period', 'exit_period', 'atr_period']})
    _, trades_df = engine.run(df, ts_code)

    # 收集信号点
    entry_points = []
    exit_points = []
    add_points = []
    stop_points = []

    for _, row in df.iterrows():
        if row.get('entry_signal') == 1:
            entry_points.append({
                'date': str(row['date']),
                'price': float(row['close'])
            })
        if row.get('exit_signal') == 1:
            exit_points.append({
                'date': str(row['date']),
                'price': float(row['close'])
            })

    # 从交易记录中提取加仓和止损
    for _, trade in trades_df.iterrows():
        if trade.get('stop_loss_triggered'):
            stop_points.append({
                'date': str(trade['exit_date']),
                'price': float(trade['exit_price'])
            })
        # 加仓：同一天有多个unit_id的入场
        # 我们通过entry_date分组来识别加仓
    # 加仓点：通过entry_date分组，同一天出现多次入场即为加仓
    if len(trades_df) > 0:
        entry_date_counts = trades_df['entry_date'].value_counts()
        for ed, count in entry_date_counts.items():
            if count > 1:
                # 这是加仓日
                row = trades_df[trades_df['entry_date'] == ed].iloc[0]
                add_points.append({
                    'date': str(ed),
                    'price': float(row['entry_price'])
                })

    # 取最近 recent_days 天的数据
    df_recent = df.tail(recent_days).copy()

    # 过滤信号点到最近 recent_days 天
    cutoff_date = df_recent['date'].iloc[0]
    entry_points = [p for p in entry_points if p['date'] >= str(cutoff_date)]
    exit_points = [p for p in exit_points if p['date'] >= str(cutoff_date)]
    add_points = [p for p in add_points if p['date'] >= str(cutoff_date)]
    stop_points = [p for p in stop_points if p['date'] >= str(cutoff_date)]

    return {
        'labels': [str(d) for d in df_recent['date'].tolist()],
        'close': [float(v) if not pd.isna(v) else None for v in df_recent['close'].tolist()],
        'entry_upper': [float(v) if not pd.isna(v) else None for v in df_recent['entry_upper'].tolist()],
        'entry_lower': [float(v) if not pd.isna(v) else None for v in df_recent['entry_lower'].tolist()],
        'exit_upper': [float(v) if not pd.isna(v) else None for v in df_recent['exit_upper'].tolist()],
        'exit_lower': [float(v) if not pd.isna(v) else None for v in df_recent['exit_lower'].tolist()],
        'atr': [float(v) if not pd.isna(v) else None for v in df_recent['atr'].tolist()],
        'entry_points': entry_points,
        'exit_points': exit_points,
        'add_points': add_points,
        'stop_points': stop_points,
    }


if __name__ == '__main__':
    print("turtle_engine.py 加载成功")
    print("  IndicatorCalculator ✓")
    print("  TurtleSignalGenerator ✓")
    print("  PositionManager ✓")
    print("  BacktestEngine ✓")
    print("  MetricsCalculator ✓")
    print("  run_all_stocks() ✓")
    print("  export_detail_data() ✓")

"""
Módulo de Métricas de Performance para el Bot de Trading
Calcula estadísticas avanzadas: Win Rate, Profit Factor, Sharpe Ratio, etc.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
import math

from logger import bot_logger as logger


@dataclass
class PerformanceMetrics:
    """Métricas de performance del bot"""
    # Generales
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # PnL
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    
    # Ratios
    profit_factor: float = 0.0
    risk_reward_ratio: float = 0.0
    expectancy: float = 0.0
    
    # Drawdown
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown: float = 0.0
    
    # Avanzadas
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    
    # Por periodo
    pnl_today: float = 0.0
    pnl_week: float = 0.0
    pnl_month: float = 0.0
    
    # Por caso
    case_stats: Dict[int, Dict] = None
    
    def __post_init__(self):
        if self.case_stats is None:
            self.case_stats = {1: {}, 3: {}, 4: {}}  # Caso 2 eliminado


class PerformanceCalculator:
    """Calculadora de métricas de performance"""
    
    def __init__(self, initial_balance: float = 30.0):
        self.initial_balance = initial_balance
        self.balance_history: List[float] = [initial_balance]
        self.pnl_history: List[float] = []
    
    def calculate_all(self, trade_history: List[dict], 
                      current_balance: float) -> PerformanceMetrics:
        """Calcular todas las métricas"""
        metrics = PerformanceMetrics()
        
        if not trade_history:
            return metrics
        
        # Filtrar solo trades cerrados
        closed_trades = [t for t in trade_history if t.get('pnl') is not None]
        
        if not closed_trades:
            return metrics
        
        # Básicas
        metrics.total_trades = len(closed_trades)
        pnls = [t.get('pnl', 0) for t in closed_trades]
        
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        
        metrics.winning_trades = len(winners)
        metrics.losing_trades = len(losers)
        metrics.total_pnl = sum(pnls)
        
        # Win Rate
        if metrics.total_trades > 0:
            metrics.win_rate = metrics.winning_trades / metrics.total_trades * 100
            metrics.avg_pnl = metrics.total_pnl / metrics.total_trades
        
        # Promedios
        if winners:
            metrics.avg_win = sum(winners) / len(winners)
        if losers:
            metrics.avg_loss = sum(losers) / len(losers)
        
        # Profit Factor
        total_wins = sum(winners) if winners else 0
        total_losses = abs(sum(losers)) if losers else 0
        if total_losses > 0:
            metrics.profit_factor = total_wins / total_losses
        
        # Risk/Reward Ratio
        if metrics.avg_loss != 0:
            metrics.risk_reward_ratio = abs(metrics.avg_win / metrics.avg_loss)
        
        # Expectancy (Expected value per trade)
        if metrics.total_trades > 0:
            win_prob = metrics.winning_trades / metrics.total_trades
            loss_prob = metrics.losing_trades / metrics.total_trades
            metrics.expectancy = (win_prob * metrics.avg_win) + (loss_prob * metrics.avg_loss)
        
        # Drawdown
        metrics.max_drawdown = min((t.get('min_pnl', 0) for t in closed_trades), default=0)
        if self.initial_balance > 0:
            metrics.max_drawdown_pct = (metrics.max_drawdown / self.initial_balance) * 100
        
        # Drawdown actual
        peak_balance = max(self.balance_history) if self.balance_history else self.initial_balance
        metrics.current_drawdown = current_balance - peak_balance
        
        # Sharpe Ratio (simplificado, asumiendo risk-free rate = 0)
        if len(pnls) > 1:
            avg_return = sum(pnls) / len(pnls)
            std_dev = math.sqrt(sum((p - avg_return) ** 2 for p in pnls) / len(pnls))
            if std_dev > 0:
                metrics.sharpe_ratio = avg_return / std_dev
        
        # Sortino Ratio (solo considera downside deviation)
        if losers:
            avg_return = sum(pnls) / len(pnls)
            downside_returns = [min(0, p - avg_return) for p in pnls]
            downside_dev = math.sqrt(sum(r ** 2 for r in downside_returns) / len(pnls))
            if downside_dev > 0:
                metrics.sortino_ratio = avg_return / downside_dev
        
        # Calmar Ratio (Return / Max Drawdown)
        if metrics.max_drawdown != 0:
            metrics.calmar_ratio = abs(metrics.total_pnl / metrics.max_drawdown)
        
        # PnL por periodo
        now = datetime.now()
        today = now.date()
        week_ago = (now - timedelta(days=7)).date()
        month_ago = (now - timedelta(days=30)).date()
        
        for trade in closed_trades:
            try:
                closed_at = trade.get('closed_at', '')
                if closed_at:
                    trade_date = datetime.fromisoformat(closed_at).date()
                    pnl = trade.get('pnl', 0)
                    
                    if trade_date == today:
                        metrics.pnl_today += pnl
                    if trade_date >= week_ago:
                        metrics.pnl_week += pnl
                    if trade_date >= month_ago:
                        metrics.pnl_month += pnl
            except:
                pass
        
        # Por caso (Caso 2 eliminado)
        for case_num in [1, 3, 4]:
            case_trades = [t for t in closed_trades if t.get('strategy_case') == case_num]
            if case_trades:
                case_pnls = [t.get('pnl', 0) for t in case_trades]
                case_winners = [p for p in case_pnls if p > 0]
                
                metrics.case_stats[case_num] = {
                    'total': len(case_trades),
                    'winners': len(case_winners),
                    'win_rate': len(case_winners) / len(case_trades) * 100 if case_trades else 0,
                    'total_pnl': sum(case_pnls),
                    'avg_pnl': sum(case_pnls) / len(case_trades) if case_trades else 0
                }
            else:
                metrics.case_stats[case_num] = {
                    'total': 0, 'winners': 0, 'win_rate': 0, 'total_pnl': 0, 'avg_pnl': 0
                }
        
        return metrics
    
    def update_balance(self, balance: float):
        """Actualizar historial de balance"""
        self.balance_history.append(balance)
    
    def add_pnl(self, pnl: float):
        """Agregar PnL al historial"""
        self.pnl_history.append(pnl)
    
    def format_report(self, metrics: PerformanceMetrics) -> str:
        """Formatear métricas para display"""
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                    📊 MÉTRICAS DE PERFORMANCE                      ║
╠══════════════════════════════════════════════════════════════════╣
║  GENERALES                                                        ║
║  ├ Total Trades:     {metrics.total_trades:>6}                                      ║
║  ├ Ganadores:        {metrics.winning_trades:>6}                                      ║
║  ├ Perdedores:       {metrics.losing_trades:>6}                                      ║
║  └ Win Rate:         {metrics.win_rate:>6.1f}%                                     ║
╠══════════════════════════════════════════════════════════════════╣
║  PNL                                                              ║
║  ├ Total:            ${metrics.total_pnl:>10.4f}                              ║
║  ├ Promedio:         ${metrics.avg_pnl:>10.4f}                              ║
║  ├ Promedio Win:     ${metrics.avg_win:>10.4f}                              ║
║  └ Promedio Loss:    ${metrics.avg_loss:>10.4f}                              ║
╠══════════════════════════════════════════════════════════════════╣
║  RATIOS                                                           ║
║  ├ Profit Factor:    {metrics.profit_factor:>10.2f}                              ║
║  ├ Risk/Reward:      {metrics.risk_reward_ratio:>10.2f}                              ║
║  ├ Expectancy:       ${metrics.expectancy:>10.4f}                              ║
║  ├ Sharpe Ratio:     {metrics.sharpe_ratio:>10.2f}                              ║
║  └ Sortino Ratio:    {metrics.sortino_ratio:>10.2f}                              ║
╠══════════════════════════════════════════════════════════════════╣
║  DRAWDOWN                                                         ║
║  ├ Max Drawdown:     ${metrics.max_drawdown:>10.4f}                              ║
║  └ Max DD %:         {metrics.max_drawdown_pct:>10.2f}%                             ║
╠══════════════════════════════════════════════════════════════════╣
║  PNL POR PERIODO                                                  ║
║  ├ Hoy:              ${metrics.pnl_today:>10.4f}                              ║
║  ├ Esta semana:      ${metrics.pnl_week:>10.4f}                              ║
║  └ Este mes:         ${metrics.pnl_month:>10.4f}                              ║
╠══════════════════════════════════════════════════════════════════╣
║  POR CASO                                                         ║
║  ├ Caso 1: {metrics.case_stats[1]['total']:>3} trades | WR: {metrics.case_stats[1]['win_rate']:>5.1f}% | PnL: ${metrics.case_stats[1]['total_pnl']:>8.4f}  ║
║  ├ Caso 3: {metrics.case_stats[3]['total']:>3} trades | WR: {metrics.case_stats[3]['win_rate']:>5.1f}% | PnL: ${metrics.case_stats[3]['total_pnl']:>8.4f}  ║
║  └ Caso 4: {metrics.case_stats[4]['total']:>3} trades | WR: {metrics.case_stats[4]['win_rate']:>5.1f}% | PnL: ${metrics.case_stats[4]['total_pnl']:>8.4f}  ║
╚══════════════════════════════════════════════════════════════════╝
"""
    
    def get_case_recommendation(self, metrics: PerformanceMetrics) -> str:
        """Obtener recomendación basada en performance por caso"""
        best_case = None
        best_expectancy = float('-inf')
        
        for case_num, stats in metrics.case_stats.items():
            if stats['total'] >= 5:  # Mínimo 5 trades para considerar
                exp = stats['avg_pnl']
                if exp > best_expectancy:
                    best_expectancy = exp
                    best_case = case_num
        
        if best_case:
            return f"📊 Mejor caso: Caso {best_case} (Expectancy: ${best_expectancy:.4f})"
        else:
            return "📊 Insuficientes datos para recomendar"


# Instancia global
performance_calculator = PerformanceCalculator()

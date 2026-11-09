#!/usr/bin/env python3
"""
Demo Paper Trading - Standalone simulation without external dependencies.

Shows how the paper trading loop will work on the VPS.
"""

import time
import random
import json
from datetime import datetime, timedelta

print("""
╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                 🚀 PAPER TRADING DEMO - EXNESS 🚀                         ║
║                      (Standalone Simulation)                              ║
║                                                                            ║
║  This demonstrates how paper trading will run 24/7 on VPS                 ║
║  Real version: python launch_paper_trading.py --mode live                 ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝
""")

def simulate_market_data():
    """Simulate live market data."""
    base_price = 2000.0
    volatility = 5.0
    return {
        "bid": base_price + random.uniform(-volatility, volatility),
        "ask": base_price + random.uniform(-volatility, volatility),
        "volume": random.randint(10000, 100000),
    }

def simulate_chart_analysis():
    """Simulate chart analysis."""
    sentiment = random.choice(["BULLISH", "BEARISH", "NEUTRAL"])
    confidence = 0.50 + random.random() * 0.25
    return {
        "sentiment": sentiment,
        "confidence": confidence,
        "patterns": ["breakout" if sentiment == "BULLISH" else "breakdown"],
        "support_resistance": {
            "support": [1990.0],
            "resistance": [2010.0],
        },
        "trading_setup": {
            "type": "breakout" if sentiment == "BULLISH" else "breakdown",
            "probability": confidence,
        },
        "risks": ["pullback", "news"],
    }

def simulate_trade_execution():
    """Simulate trade execution."""
    if random.random() < 0.3:  # 30% chance of trade
        return {
            "action": random.choice(["BUY", "SELL"]),
            "price": 2000.0 + random.uniform(-5, 5),
            "volume": 0.1,
            "confidence": 0.60 + random.random() * 0.15,
        }
    return None

def simulate_position_close():
    """Simulate position closing."""
    if random.random() < 0.2:  # 20% chance of close
        pnl = random.uniform(-50, 100)
        return {
            "pnl": pnl,
            "reason": "TP_HIT" if pnl > 0 else "SL_HIT",
        }
    return None

def run_demo(duration_minutes=5, cycle_seconds=10):
    """Run demo trading loop."""

    print(f"⚙️  Demo Settings:")
    print(f"  Duration: {duration_minutes} minutes")
    print(f"  Cycle: {cycle_seconds} seconds")
    print(f"  Account: Exness Demo 480578231")
    print(f"  Mode: MOCK (simulated MT5)")
    print("")

    # Initialize
    account_balance = 1000.0
    daily_pnl = 0.0
    trades_executed = 0
    trades_won = 0
    max_drawdown = 0.0
    peak_balance = account_balance
    open_positions = 0

    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=duration_minutes)
    cycle_count = 0

    print(f"🟢 Starting paper trading simulation...")
    print(f"Start: {start_time.strftime('%H:%M:%S')}")
    print(f"End:   {end_time.strftime('%H:%M:%S')}")
    print("")
    print("=" * 80)

    try:
        while datetime.now() < end_time:
            cycle_count += 1
            cycle_time = datetime.now().strftime('%H:%M:%S')

            # Simulate market data
            quote = simulate_market_data()
            mid_price = (quote["bid"] + quote["ask"]) / 2

            print(f"\n[{cycle_time}] CYCLE {cycle_count}")
            print(f"  {quote['bid']:.2f} | MID {mid_price:.2f} | {quote['ask']:.2f} | Vol: {quote['volume']}")

            # Chart analysis
            chart = simulate_chart_analysis()
            print(f"  Chart: {chart['sentiment']} ({chart['confidence']:.0%} confidence)")

            # Trade execution
            signal = simulate_trade_execution()
            if signal:
                trades_executed += 1
                open_positions += 1
                print(f"  ✅ SIGNAL: {signal['action']} {signal['volume']}L @ {signal['price']:.2f}")
                print(f"     Confidence: {signal['confidence']:.0%} | Positions open: {open_positions}")
            else:
                print(f"  No signal")

            # Position closing
            if open_positions > 0:
                close = simulate_position_close()
                if close:
                    open_positions -= 1
                    daily_pnl += close["pnl"]
                    account_balance += close["pnl"]

                    if close["pnl"] > 0:
                        trades_won += 1
                        print(f"  💰 CLOSED: {close['reason']} | P&L: +${close['pnl']:.2f}")
                    else:
                        print(f"  💸 CLOSED: {close['reason']} | P&L: -${abs(close['pnl']):.2f}")

                    # Track drawdown
                    peak_balance = max(peak_balance, account_balance)
                    drawdown = (peak_balance - account_balance) / peak_balance * 100
                    max_drawdown = max(max_drawdown, drawdown)

            # Metrics
            if cycle_count % 3 == 0:
                win_rate = (trades_won / trades_executed * 100) if trades_executed > 0 else 0
                print(f"\n  📊 Metrics:")
                print(f"     Trades: {trades_executed} | Won: {trades_won} ({win_rate:.0f}%)")
                print(f"     Daily P&L: ${daily_pnl:+.2f}")
                print(f"     Balance: ${account_balance:.2f}")
                print(f"     Max Drawdown: {max_drawdown:.1f}%")

            # Wait for next cycle
            time.sleep(cycle_seconds)

    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")

    finally:
        # Final report
        print("\n" + "=" * 80)
        print("\n📊 FINAL REPORT")
        print("=" * 80)
        duration = datetime.now() - start_time
        print(f"Duration: {duration}")
        print(f"Cycles: {cycle_count}")
        print(f"\nTrading:")
        print(f"  Signals Generated: {trades_executed}")
        print(f"  Trades Won: {trades_won}")
        print(f"  Win Rate: {(trades_won/trades_executed*100) if trades_executed > 0 else 0:.1f}%")
        print(f"  Open Positions: {open_positions}")
        print(f"\nP&L:")
        print(f"  Initial Balance: ${1000.00:.2f}")
        print(f"  Daily P&L: ${daily_pnl:+.2f}")
        print(f"  Final Balance: ${account_balance:.2f}")
        print(f"  Return: {(daily_pnl/1000)*100:+.2f}%")
        print(f"  Max Drawdown: {max_drawdown:.1f}%")
        print("\n" + "=" * 80)
        print("\n✅ Demo complete!")
        print("\nFor LIVE trading on VPS:")
        print("  1. SSH to: 144.91.76.28:2222")
        print("  2. Run: python launch_paper_trading.py --mode live --duration 1440")
        print("  3. Monitor: tail -f C:\\StatArb\\logs\\agents_trading.log")
        print("")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=5, help="Duration in minutes")
    parser.add_argument("--cycle", type=int, default=10, help="Cycle in seconds")
    args = parser.parse_args()

    run_demo(duration_minutes=args.duration, cycle_seconds=args.cycle)

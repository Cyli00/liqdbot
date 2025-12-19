
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from liqdbot.engine import engine
from liqdbot.config import DEFAULT_SYMBOL

async def main():
    print(f"Testing MACD Resonance Logic for {DEFAULT_SYMBOL}...")
    
    # Manually trigger data fetch
    try:
        df, lower_df, htf_df = await engine.fetch_data(DEFAULT_SYMBOL, limit=500, force_full=True)
        print(f"Fetched Data: 1h: {len(df)}, 4h: {len(htf_df)}")
        
        # Calculate indicators
        print("Calculating indicators...")
        df = engine.calculate_indicators(df, lower_df)
        htf_df = engine.calculate_htf_indicators(htf_df)
        
        # Check calculation columns
        print("1h Columns:", df.columns[-5:])
        print("4h Columns:", htf_df.columns[-5:])
        
        # Check last rows
        print("\nLast 1h Row:")
        print(df.iloc[-1][['timestamp', 'close', 'MACD_12_26_9', 'MACDs_12_26_9']].to_dict())
        
        print("\nLast 4h Row:")
        print(htf_df.iloc[-1][['timestamp', 'close', 'MACD', 'Signal', 'Hist_Color', 'Signal_Slope']].to_dict())
        
        # Perform Analysis
        print("\nPerforming Resonance Check...")
        res_val, res_slope, res_color = engine.check_macd_resonance(df, htf_df)
        print(f"Resonance Result: Val={res_val}, Slope={res_slope:.4f}, Color={res_color}")
        
        # Simulate an alert message generation if resonance was triggered
        if res_val != 0:
             from liqdbot.alerts import AlertMessages
             if res_val == 1:
                 msg = AlertMessages.macd_resonance_golden(DEFAULT_SYMBOL, df.iloc[-1]['close'], res_slope, res_color)
             else:
                 msg = AlertMessages.macd_resonance_death(DEFAULT_SYMBOL, df.iloc[-1]['close'], res_slope, res_color)
             print(f"\n[Generated Alert Message]:\n{msg}")
        else:
             print("\nNo resonance signal detected at current bar.")

    except Exception as e:
        print(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await engine.close_exchange()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

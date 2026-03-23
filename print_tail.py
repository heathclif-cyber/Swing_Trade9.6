import pandas as pd

df = pd.read_csv('d:/Apps Dev/Swing_Trade9.6/Data_Track_9.6_DOGEUSDT_4H_20260319_1429.csv')
cols_to_print = ['Timestamp','Close','EMA_21','EMA_50','EMA_200','CVD','Open_Interest','ATR_14','PDH','PDL','PWH','PWL','FVG_Up_Top','FVG_Up_Bottom','FVG_Down_Top','FVG_Down_Bottom','OB_Price','SFP_Sweep','CHoCH', 'BTC_Price']
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)
print(df.tail(10)[cols_to_print])

import os
import sys
import dotenv
from configparser import ConfigParser
from datetime import datetime
import datetime
import time
import pandas as pd
from ftx_client import FtxClient, instant_limit_order
from tech import check_ta
from log import *
import math

# load config.ini
config = ConfigParser()
config.read('./public/config.ini')
market_symbol = config['main']['market_symbol']
sub_account = config["main"]['sub_account']
init_max_zone = config["grid"]['init_max_zone']
init_min_zone = config["grid"]['init_min_zone']

# load .env
dotenv.load_dotenv('.env')
api_key = os.environ.get("API_FTX")
secret_key = os.environ.get("SECRET_FTX")

client = FtxClient(api_key,
                   secret_key, sub_account)


def get_balance(symbol):
    for a in client.get_balances():
        if a['coin'] == symbol:
            return a


# check market pair
client.get_single_market(market_symbol)

# init nav
base_symbol = market_symbol.split('/')[0]
quote_symbol = market_symbol.split('/')[1]
base_symbol_balance = get_balance(base_symbol)
quote_symbol_balance = get_balance(quote_symbol)
init_nav = float(0 if not base_symbol_balance else base_symbol_balance['usdValue']) + float(
    0 if not quote_symbol_balance else quote_symbol_balance['usdValue'])

# read csv to pandas
grid = pd.read_csv('./public/grid.csv', sep=',', index_col=0)
grid_trading = grid.query(
    'price >= {} & price <= {}'.format(init_min_zone, init_max_zone))
grid_trading.index = grid_trading.index - grid_trading.index[0]
print(grid_trading)

# check stablecoin balance amount
grid_init_posval_sum = grid_trading['value'].sum()
if (int(config['main']['check_funds'])) & (float(0 if not quote_symbol_balance else quote_symbol_balance['usdValue']) < grid_init_posval_sum):
    raise Exception("Insufficient funds!")

avg_buy_price = -1
while True:
    try:
        config.read('./public/config.ini')
        # check exhange pair and price
        market_info = client.get_single_market(market_symbol)
        if market_info['enabled'] == False:
            raise Exception("FTX suspended trading!")
        price = market_info['price']
        # cal nav
        base_symbol_balance = get_balance(base_symbol)
        quote_symbol_balance = get_balance(quote_symbol)
        nav = float(0 if not base_symbol_balance else base_symbol_balance['usdValue']) + float(
            0 if not quote_symbol_balance else quote_symbol_balance['usdValue'])
        nav_pct = nav/init_nav*100

        # check trailing up
        if int(config['grid']['trailing_up']) == 1:
            # trail up
            if price > grid_trading.iloc[0, 0] and price < grid.iloc[0, 0]:
                grid_trading = grid_trading.iloc[0:-1]
                new_grid = []
                _grid = grid.query(
                    'price > {}'.format(grid_trading.iloc[0, 0]))
                for i in range(len(_grid.index)-1, -1, -1):
                    if _grid.iloc[i, 0] < price:
                        new_grid.append(_grid.iloc[i].to_list())
                if len(new_grid) > 0:
                    for g in new_grid:
                        grid_trading.loc[-1] = g  # adding a row
                        grid_trading.index = grid_trading.index + 1  # shifting index
                        grid_trading.sort_index(inplace=True)

            # check stoploss
        if price < float(config['grid']['stop_loss']):
            # stop grid, sell all
            instant_limit_order(client, market_symbol, "sell",
                                float(base_symbol_balance['free']))
            sys.exit()

        # TRADE
        t = 0
        cf = 0
        # check ta signal
        ta_buy = check_ta(market_symbol, config['ta']['timeframe_buy'], int(
            config['ta']['ema1_len_buy']), int(config['ta']['ema2_len_buy']), name="buy")
        ta_sell = check_ta(market_symbol, config['ta']['timeframe_sell'], int(
            config['ta']['ema1_len_sell']), int(config['ta']['ema2_len_sell']), name="sell")
        # BUY CHECK
        if ta_buy == 1:
            pos_val = 0
            # check grid above price
            for i, r in grid_trading.iterrows():
                if r['price'] >= market_info['ask'] and r['hold'] == 0 and r['hold_price'] == -1:
                    # add pos together
                    pos_val += r['value']
                    # update grid
                    grid_trading.iloc[i, 2] = r['value']/market_info['ask']
                    grid_trading.iloc[i, 3] = market_info['ask']
             # buy
            if pos_val != 0:
                pos_unit = pos_val/market_info['ask']
                instant_limit_order(client, market_symbol, "buy", pos_unit)
                t = 1
        # SELL CHECK
        if ta_sell == 2:
            pos_hold = 0
            # check grid below price
            for i, r in grid_trading.iterrows():
                if r['hold'] > 0 and r['hold_price'] != -1 and price > r['price']:
                    # add pos together
                    pos_hold += r['hold']
                    # cf cal
                    cf += (price*r['hold'])-(r['hold_price']*r['hold'])
                    # update grid
                    grid_trading.iloc[i, 2] = 0
                    grid_trading.iloc[i, 3] = -1
                # sell
            if pos_hold != 0:
                instant_limit_order(
                    client, market_symbol, "sell", pos_hold)
                t = 1

        # LOG
        if t == 1:
            # update grid.csv
            grid_trading.to_csv('./public/grid_trading.csv')
            # cal nav
            base_symbol_balance = get_balance(base_symbol)
            quote_symbol_balance = get_balance(quote_symbol)
            nav = float(0 if not base_symbol_balance else base_symbol_balance['usdValue']) + float(
                0 if not quote_symbol_balance else quote_symbol_balance['usdValue'])
            nav_pct = nav/init_nav*100
            # avg buy price
            avg_buy_price = round((init_nav - float(0 if not quote_symbol_balance else quote_symbol_balance['free']))/float(
                -1 if not base_symbol_balance else base_symbol_balance['free']), 2)
            # update log
            dt = datetime.datetime.now()
            add_row(dt.strftime("%d/%m/%Y %H:%M:%S"),
                    price, nav, nav_pct, avg_buy_price, cf)
            if cf > 0:
                client.subaccount_transfer(
                    quote_symbol, math.floor(cf), sub_account, "main")

        # PRINT---
        #os.system('cls' if os.name == 'nt' else 'clear')
        print("--------------------")
        print("[CONFIG]")
        print("market_symbol:", market_symbol)
        print("sub_account:", sub_account)
        print("grid_zone_all:", round(
            grid.iloc[-1, 0], 2), "=>", grid.iloc[0, 0])
        print("grid_init_posval_sum:", round(grid_init_posval_sum, 2))
        print("-------------------")
        print("[STATUS]")
        print("{}: {}".format(market_symbol, price))
        print(base_symbol+" balance: " +
              str(round(float(0 if not base_symbol_balance else base_symbol_balance['free']), 4)))
        print(quote_symbol+" balance: " +
              str(round(float(0 if not quote_symbol_balance else quote_symbol_balance['free']), 2)))
        print("NAV: "+str(round(nav, 2))+"/" +
              str(round(init_nav, 2))+" ["+str(int(nav_pct))+"%]")
        print("grid_zone_trading:", round(
            grid_trading.iloc[-1, 0], 2), "=>", grid_trading.iloc[0, 0])
        print("avg_buy_price:", avg_buy_price)
    except Exception as err:
        print(err)
    print("--------------------")
    time.sleep(60)

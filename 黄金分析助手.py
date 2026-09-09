# -*- coding: utf-8 -*-
"""
黄金分析助手 v3.0 - 完整版
功能：实时行情、信号分析、自动交易、EA控制、价格预警、历史回测
"""
import MetaTrader5 as mt5
import numpy as np
import threading
import time
import configparser
import os
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print('tkinter missing'); exit(1)

try:
    import matplotlib
    matplotlib.use('TkAgg')
    matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
    matplotlib.rcParams['axes.unicode_minus'] = False
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
except ImportError:
    print('matplotlib not found'); exit(1)

try:
    import winsound
    HAS_SOUND = True
except ImportError:
    HAS_SOUND = False

try:
    from win10toast import ToastNotifier
    HAS_TOAST = True
except ImportError:
    HAS_TOAST = False

_cfg = configparser.ConfigParser()
_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
if os.path.exists(_cfg_path):
    _cfg.read(_cfg_path, encoding='utf-8')

TERMINAL_PATH = _cfg.get('MT5', 'terminal_path', fallback=r'D:\MetaTrader 5 EXNESS\terminal64.exe')
TARGET_BALANCE = float(_cfg.get('Target', 'target_balance', fallback='3000'))
INITIAL_BALANCE = float(_cfg.get('Target', 'initial_balance', fallback='1560'))
AUTO_LOT = float(_cfg.get('AutoTrade', 'lot_size', fallback='0.01'))
AUTO_RSI_BUY = int(_cfg.get('AutoTrade', 'rsi_buy', fallback='30'))
AUTO_RSI_SELL = int(_cfg.get('AutoTrade', 'rsi_sell', fallback='70'))
AUTO_MAX_POS = int(_cfg.get('AutoTrade', 'max_positions', fallback='3'))
WINDOW_WIDTH = int(_cfg.get('Window', 'width', fallback='1600'))
WINDOW_HEIGHT = int(_cfg.get('Window', 'height', fallback='800'))
REFRESH_MS = int(_cfg.get('Display', 'refresh_interval_ms', fallback='3000'))
ALERT_PCT = float(_cfg.get('Alerts', 'price_change_pct', fallback='1.0'))
ALERT_CD = int(_cfg.get('Alerts', 'cooldown_sec', fallback='120'))


class MT5Engine:
    SYMBOLS = {
        "XAUUSDc": "\u9ec4\u91d1",
        "EURUSDc": "\u6b27\u5143/\u7f8e\u5143",
        "USDJPYc": "\u7f8e\u5143/\u65e5\u5143",
        "BTCUSDc": "\u6bd4\u7279\u5e01",
    }
    TF_MAP = {'M1':mt5.TIMEFRAME_M1,'M5':mt5.TIMEFRAME_M5,'M15':mt5.TIMEFRAME_M15,
              'M30':mt5.TIMEFRAME_M30,'H1':mt5.TIMEFRAME_H1,'H4':mt5.TIMEFRAME_H4,'D1':mt5.TIMEFRAME_D1}

    def __init__(self):
        self.ok = mt5.initialize()
        self._connecting = False
        self._sym_digits = {}

    def shutdown(self):
        if self.ok: mt5.shutdown()

    def tick(self, s):
        return mt5.symbol_info_tick(s) if self.ok else None

    def rates(self, s, tf, n=100):
        if not self.ok: return None
        r = mt5.copy_rates_from_pos(s, self.TF_MAP.get(tf, mt5.TIMEFRAME_H1), 0, n)
        return np.array([(x['time'],x['open'],x['high'],x['low'],x['close'],x['tick_volume'])
                        for x in r], dtype=[('t','i8'),('o','f8'),('h','f8'),('l','f8'),('c','f8'),('v','i8')]) if r else None

    def account(self):
        i = mt5.account_info()
        return {'balance':i.balance,'equity':i.equity,'margin':i.margin,
                'free':i.margin_free,'profit':i.profit,'lev':i.leverage} if i else None

    def positions(self, s=None):
        p = mt5.positions_get(symbol=s) if s else mt5.positions_get()
        return list(p) if p else []

    def order(self, sym, act, lot, sl=0, tp=0):
        t = self.tick(sym)
        if not t: return None
        pr = t.ask if act=='buy' else t.bid
        ot = mt5.ORDER_TYPE_BUY if act=='buy' else mt5.ORDER_TYPE_SELL
        req = {"action":mt5.TRADE_ACTION_DEAL,"symbol":sym,"volume":lot,"type":ot,
               "price":pr,"sl":sl,"tp":tp,"deviation":20,"magic":20260908,
               "comment":"GoldAnalyzer","type_time":mt5.ORDER_TIME_GTC,
               "type_filling":mt5.ORDER_FILLING_IOC}
        return mt5.order_send(req)

    # ---- indicators ----
    def ma(self, c, p):
        return np.mean(c[-p:]) if len(c)>=p else np.mean(c)

    def rsi(self, c, p=14):
        if len(c)<p+1: return 50.0
        d = np.diff(c[-p-1:])
        g = np.mean(np.where(d>0,d,0))
        l = np.mean(np.where(d<0,-d,0))
        return 100-(100/(1+g/l)) if l>0 else 100

    def macd(self, c, f=12, s=26):
        if len(c)<s: return 0,0,0
        m = np.mean(c[-f:])-np.mean(c[-s:])
        return m, m*0.9, m*0.1

    def bb(self, c, p=20, k=2):
        if len(c)<p: return None
        m = np.mean(c[-p:]); sd = np.std(c[-p:])
        return m+k*sd, m, m-k*sd

    def atr(self, h, l, c, p=14):
        if len(h)<p+1: return 0
        tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(-p,0)]
        return np.mean(tr)

    def levels(self, rates):
        if not rates or len(rates)<20: return None, None
        h = rates['high']; l = rates['low']
        resist, supp = [], []
        for i in range(5, len(h)-5):
            if h[i] == max(h[i-5:i+6]): resist.append(h[i])
            if l[i] == min(l[i-5:i+6]): supp.append(l[i])
        return max(resist) if resist else None, min(supp) if supp else None

    def analyze(self, sym='XAUUSDc', tf='H1'):
        r = self.rates(sym, tf, 100)
        if not r: return None
        c,h,l = r['close'],r['high'],r['low']
        t = self.tick(sym)
        if not t: return None
        ma = {p:self.ma(c,p) for p in [5,10,20,50]}
        rsi = self.rsi(c)
        m, ms, mh = self.macd(c)
        bb = self.bb(c)
        atr = self.atr(h,l,c)
        res, sup = self.levels(r)
        sig, trend = [], ""
        if ma[5]>ma[10]>ma[20]: trend,sig="Strong Up",[("MA Bullish","Strong")]
        elif ma[5]<ma[10]<ma[20]: trend,sig="Strong Down",[("MA Bearish","Strong")]
        elif ma[5]>ma[10]: trend,sig="Bullish",[("MA Up","Neutral")]
        elif ma[5]<ma[10]: trend,sig="Bearish",[("MA Down","Neutral")]
        if rsi>70: sig.append((f"RSI={rsi:.0f} Overbought","Sell"))
        elif rsi<30: sig.append((f"RSI={rsi:.0f} Oversold","Buy"))
        elif rsi>60: sig.append((f"RSI={rsi:.0f}","Bearish"))
        elif rsi<40: sig.append((f"RSI={rsi:.0f}","Bullish"))
        else: sig.append((f"RSI={rsi:.0f}","Neutral"))
        if m>0 and mh>0: sig.append(("MACD Cross Up","Buy"))
        elif m<0 and mh<0: sig.append(("MACD Cross Down","Sell"))
        if bb and t.bid<bb[2]: sig.append(("Below BB Lower","Buy"))
        elif bb and t.bid>bb[0]: sig.append(("Above BB Upper","Sell"))
        if sup and t.bid-sup<2: sig.append((f"Support ${sup:.1f}","Watch"))
        if res and res-t.bid<2: sig.append((f"Resistance ${res:.1f}","Watch"))
        ap = atr/t.bid*100 if t.bid>0 else 0
        vl = "High" if ap>0.5 else ("Medium" if ap>0.2 else "Low")
        bs = sum(1 for x in sig if x[1] in ("Buy","Bullish","Strong"))
        ss = sum(1 for x in sig if x[1] in ("Sell","Bearish","Strong"))
        if bs>ss+2: ov=("STRONG BUY","green")
        elif bs>ss: ov=("BULLISH","lightgreen")
        elif ss>bs+2: ov=("STRONG SELL","red")
        elif ss>bs: ov=("BEARISH","orange")
        else: ov=("NEUTRAL","gray")
        return {'sym':sym,'name':self.SYMBOLS.get(sym,sym),'price':t.bid,'ask':t.ask,
                'spread':t.ask-t.bid,'trend':trend,'ma':ma,'rsi':rsi,'macd':m,
                'bb':bb,'atr':atr,'atr_pct':ap,'vol':vl,'signals':sig,'overall':ov,
                'bs':bs,'ss':ss,'res':res,'sup':sup,'closes':c,'rates':r}
    def connect(self):
        if getattr(self, "_connecting", False): return
        self._connecting = True
        try:
            self.ok = mt5.initialize()
            if self.ok:
                for sym in self.SYMBOLS:
                    si = mt5.symbol_info(sym)
                    if si:
                        self._sym_digits[sym] = si.digits if hasattr(si, "digits") else 2
                self._connecting = False
        except Exception:
            self._connecting = False

    def prev_close(self, sym):
        try:
            r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 1)
            return r[0]["close"] if r and len(r) > 0 else None
        except:
            return None

    def order_send(self, sym, act, lot, sl=0, tp=0):
        t = self.tick(sym)
        if not t: return None
        price = t.ask if act == "buy" else t.bid
        ot = mt5.ORDER_TYPE_BUY if act == "buy" else mt5.ORDER_TYPE_SELL
        req = {"action":mt5.TRADE_ACTION_DEAL,"symbol":sym,"volume":lot,"type":ot,
               "price":price,"sl":sl,"tp":tp,"deviation":20,"magic":20260908,
               "comment":"GoldAnalyzer","type_time":mt5.ORDER_TIME_GTC,
               "type_filling":mt5.ORDER_FILLING_IOC}
        return mt5.order_send(req)

    def backtest(self, sym="XAUUSDc", tf="H1"):
        r = self.rates(sym, tf, 200)
        if not r or len(r) < 50: return None
        c = r["close"]
        ma5 = [np.mean(c[max(0,i-4):i+1]) for i in range(len(c))]
        ma20 = [np.mean(c[max(0,i-19):i+1]) if i >= 19 else None for i in range(len(c))]
        initial = 10000; capital = initial; trades = 0; wins = 0; pos_vol = 0
        for i in range(40, len(c)):
            if ma5[i] and ma20[i] and ma5[i-1] and ma20[i-1]:
                if ma5[i-1] <= ma20[i-1] and ma5[i] > ma20[i] and pos_vol == 0:
                    if capital >= c[i] * 0.01:
                        pos_vol = (capital / c[i]) * 0.01
                        trades += 1
                elif ma5[i-1] >= ma20[i-1] and ma5[i] < ma20[i] and pos_vol > 0:
                    pnl = (c[i] - c[max(0,i-20)]) * pos_vol
                    capital += pnl
                    if pnl > 0: wins += 1
                    pos_vol = 0
        final = capital + (pos_vol * c[-1] if pos_vol > 0 else 0)
        ret = final - initial
        return {"initial_capital": initial, "final_capital": final,
                "total_return": ret/initial*100, "total_trades": trades,
                "win_rate": (wins/trades*100) if trades > 0 else 0,
                "profit_factor": abs(final/initial) if initial > 0 else 0}



class AlertSystem:
    def __init__(self):
        self.alerts = []
        self.last_alert_time = {}
        self.cooldown = ALERT_CD

    def add(self, sym, threshold_pct=ALERT_PCT):
        self.alerts.append({"sym": sym, "threshold": threshold_pct, "triggered": False})
        return len(self.alerts) - 1

    def remove(self, idx):
        if 0 <= idx < len(self.alerts): self.alerts.pop(idx)

    def check(self, sym, engine=None):
        import time as _tt
        now = _tt.time()
        if sym in self.last_alert_time and now - self.last_alert_time[sym] < self.cooldown: return None
        if engine:
            tick = engine.tick(sym)
        else:
            return None
        if not tick: return None
        prev = self._get_prev(sym)
        if prev is None: self._set_prev(sym, tick.bid); return None
        chg = abs(tick.bid - prev) / prev * 100
        if chg >= ALERT_PCT:
            self.last_alert_time[sym] = now
            self._set_prev(sym, tick.bid)
            return {"sym": sym, "price": tick.bid, "chg": chg, "dir": "up" if tick.bid > prev else "down"}
        self._set_prev(sym, tick.bid)
        return None

    def _get_prev(self, sym):
        for x in self.alerts:
            if x["sym"] == sym and "prev_price" in x: return x["prev_price"]
        return None

    def _set_prev(self, sym, price):
        for x in self.alerts:
            if x["sym"] == sym: x["prev_price"] = price; return
        self.alerts.append({"sym": sym, "threshold": ALERT_PCT, "prev_price": price})


class GoldAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("黄金分析助手 v3.0")
        self.stop = False
        self.auto_on = False
        self.ea_status_var = tk.StringVar(value='未部署')
        self.notifier = ToastNotifier() if HAS_TOAST else None
        self.C = {'bg': '#0d1117', 'card': '#161b22', 'bd': '#30363d',
                  'tx': '#c9d1d9', 'dim': '#8b949e', 'accent': '#58a6ff',
                  'green': '#3fb950', 'red': '#f85149', 'yellow': '#d29922',
                  'highlight': '#1f6feb', 'glow': '#39d353'}
        self.anz = MT5Engine()
        self.anz.connect()
        self.alert_system = AlertSystem()
        self._prev = {}
        self._prev_close = {}
        self._init_vars()
        self._build_ui()
        self._start_refresh()

    def _init_vars(self):
        self.price_vars = {}; self.pcl = {}; self.daily_vars = {}
        self.tv = tk.StringVar(value="H1")
        self.sl = tk.StringVar(value="分析中...")
        self.sl_label = None
        self.avars = {}
        for k in ['bal','eq','mg','free','prof']: self.avars[k] = tk.StringVar(value='--')
        self.ivars = {}
        for k in ['ma5','ma10','ma20','ma50','rsi','macd','bbu','bbl','atr']: self.ivars[k] = tk.StringVar(value='--')
        self.vv = tk.StringVar(value="--")
        self.target_var = tk.StringVar(value=f"目标 ${TARGET_BALANCE:.0f}")
        self.notify_popup_var = tk.BooleanVar(value=True)
        self.notify_sound_var = tk.BooleanVar(value=True)
        self.auto_status_var = tk.StringVar(value="已停止")
        self.auto_lot_var = tk.DoubleVar(value=0.01)
        self.auto_rsi_buy_var = tk.IntVar(value=30)
        self.auto_rsi_sell_var = tk.IntVar(value=70)
        self.alert_pct = tk.DoubleVar(value=ALERT_PCT)
        self.quick_lot_var = tk.DoubleVar(value=0.01)
        self.conn_var = tk.StringVar(value="连接中...")

    def _frame(self, parent, title):
        f = tk.Frame(parent, bg=self.C["card"], relief="solid", bd=1)
        f.pack(fill="x", padx=6, pady=4)
        tk.Label(f, text="● " + title, font=("Consolas", 9, "bold"),
                 fg=self.C["accent"], bg=self.C["card"]).pack(fill="x", padx=8, pady=(4, 2))
        return f

    def _build_ui(self):
        self.root.configure(bg=self.C["bg"])
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{(sw-WINDOW_WIDTH)//2}+{(sh-WINDOW_HEIGHT)//2}")
        tf = tk.Frame(self.root, bg=self.C["bg"])
        tf.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(tf, text="HJ ANALYZER  v3.0", font=("Consolas", 14, "bold"),
                 fg=self.C["accent"], bg=self.C["bg"]).pack(side="left")
        self.conn_lbl = tk.Label(tf, textvariable=self.conn_var, font=("Consolas", 8),
                 fg=self.C["yellow"], bg=self.C["bg"])
        self.conn_lbl.pack(side="left", padx=(20, 0))
        tk.Label(tf, text="XAUUSDc  REAL-TIME  AUTO  ALERTS", font=("Consolas", 8),
                 fg=self.C["dim"], bg=self.C["bg"]).pack(side="left", padx=(20, 0))
        tk.Label(tf, textvariable=self.target_var, font=("Consolas", 9),
                 fg=self.C["yellow"], bg=self.C["bg"]).pack(side="right", padx=(20, 0))
        main = tk.Frame(self.root, bg=self.C["bg"])
        main.pack(fill="both", expand=True, padx=12, pady=4)
        left = tk.Frame(main, bg=self.C["bg"])
        left.pack(side="left", fill="y", padx=(0, 6))
        self._panel_prices(left)
        self._panel_signal(left)
        self._panel_account(left)
        right = tk.Frame(main, bg=self.C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.right_canvas = tk.Canvas(right, bg=self.C["bg"], highlightthickness=0)
        self.right_scroll = tk.Scrollbar(right, orient="vertical", command=self.right_canvas.yview)
        self.right_scrollable = tk.Frame(self.right_canvas, bg=self.C["bg"])
        self.right_scrollable.bind("<Configure>", lambda e: self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all")))
        self.right_canvas.create_window((0, 0), window=self.right_scrollable, anchor="nw")
        self.right_canvas.configure(yscrollcommand=self.right_scroll.set)
        self.right_canvas.pack(side="left", fill="both", expand=True)
        self.right_scroll.pack(side="right", fill="y")
        self.right_canvas.bind("<MouseWheel>", lambda e: self.right_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self._panel_chart(self.right_scrollable)
        self._panel_ea(self.right_scrollable)
        self._panel_indicators(self.right_scrollable)
        self._panel_alerts(self.right_scrollable)
        self._panel_auto_trade(self.right_scrollable)
        self._panel_backtest(self.right_scrollable)

    def _panel_prices(self, parent):
        f = self._frame(parent, "实时行情")
        for sym, name in MT5Engine.SYMBOLS.items():
            row = tk.Frame(f, bg=self.C["card"])
            row.pack(fill="x", padx=4, pady=2)
            kf = tk.Frame(row, bg=self.C["card"])
            kf.pack(side="left")
            tk.Label(kf, text=sym, font=("Consolas", 9, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="left")
            tk.Label(kf, text=name, font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(4, 0))
            vv = tk.StringVar(value="--")
            self.price_vars[sym] = vv
            tk.Label(row, textvariable=vv, font=("Consolas", 10, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="right", padx=(10, 0))
            dv = tk.StringVar(value="--")
            self.daily_vars[sym] = dv
            tk.Label(row, textvariable=dv, font=("Consolas", 8), fg=self.C["yellow"], bg=self.C["card"]).pack(side="right", padx=(0, 4))
            cl = tk.Frame(row, width=8, height=8, bg=self.C["bg"])
            cl.pack(side="right", padx=4)
            self.pcl[sym] = cl
    def _refresh(self):
        if self.stop: return
        if not self.anz.ok:
            self._update_conn()
            if not self.anz._connecting:
                import time as _t
                if not hasattr(self, "_last_reconnect") or _t.time() - self._last_reconnect > 10:
                    self._last_reconnect = _t.time()
                    self.anz.connect()
            self.root.after(REFRESH_MS, self._refresh)
            return
        try:
            for sym, name in MT5Engine.SYMBOLS.items():
                t = self.anz.tick(sym)
                if t:
                    dig = self.anz._sym_digits.get(sym, 2)
                    prev = self._prev.get(sym)
                    if prev:
                        ch = t.bid - prev
                        pct = ch / prev * 100 if prev else 0
                        sg = "+" if ch >= 0 else ""
                        co = self.C["green"] if ch >= 0 else self.C["red"]
                        self.pcl[sym].config(bg=co)
                        self.price_vars[sym].set(f"{t.bid:.{dig}f}  {sg}{pct:.2f}%")
                    else:
                        self.price_vars[sym].set(f"{t.bid:.{dig}f}")
                    self._prev[sym] = t.bid
                    pc = self._prev_close.get(sym)
                    if pc:
                        dch = t.bid - pc
                        dpct = dch / pc * 100
                        dsg = "+" if dch >= 0 else ""
                        dco = self.C["green"] if dch >= 0 else self.C["red"]
                        self.daily_vars[sym].set(f"[日{dsg}{dpct:.2f}%]")
                        self.daily_vars[sym].config(fg=dco)
            self._signal()
            self._account()
            self._chart()
            self._check_alerts()
            if self.auto_on: self._auto_trade_step()
            self._check_ea_status()
        except Exception as e:
            _dbg(f"_refresh error: {e}")
        self._update_conn()
        self.root.after(REFRESH_MS, self._refresh)

    def _check_alerts(self):
        try:
            result = self.alert_system.check("XAUUSDc", engine=self.anz)
            if result:
                msg = f"黄金波动 {result['chg']:.2f}% @ "
                if self.notify_popup_var.get() and HAS_TOAST:
                    try: self.notifier.show_toast("价格预警", msg, duration=5)
                    except: pass
                if self.notify_sound_var.get() and HAS_SOUND:
                    try: winsound.Beep(800, 200)
                    except: pass
                self._auto_log(f"预警: {msg}")
        except Exception:
            pass

    def _start_refresh(self):
        self._prev = {}
        self._prev_close = {}
        for sym in MT5Engine.SYMBOLS.keys():
            pc = self.anz.prev_close(sym)
            if pc is not None:
                self._prev_close[sym] = pc
            else:
                t = self.anz.tick(sym)
                if t: self._prev[sym] = t.bid
        self._target(); self._refresh()

    def _close(self):
        self.stop = True; self.anz.shutdown(); self.root.destroy()

    def _update_conn(self):
        if self.anz.ok:
            acc = self.anz.account()
            self.conn_var.set("已连接" if acc else "MT5运行中")
            self.conn_lbl.config(fg=self.C["green"])
        else:
            self.conn_var.set("MT5未连接 - 等待恢复")
            self.conn_lbl.config(fg=self.C["red"])

    def _target(self):
        i = self.anz.account()
        if i:
            rem = TARGET_BALANCE - i["balance"]
            done = chr(27700) + chr(36229) + chr(25104) + chr(25104)
            self.target_var.set(f"目标 ${TARGET_BALANCE:.0f} | 当前 ${i['balance']:.0f} | {done if i['balance'] >= TARGET_BALANCE else '还需 +$' + str(int(rem))}")
    def _panel_alerts(self, parent):
        f = self._frame(parent, "价格预警")
        af = tk.Frame(f, bg=self.C["card"]); af.pack(fill="x", padx=8)
        tk.Label(af, text="波动阈值 %:", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        tk.Spinbox(af, from_=0.5, to=10, increment=0.5, textvariable=self.alert_pct, width=5,
                   font=("Consolas", 9), bg=self.C["bg"], fg=self.C["tx"], relief="flat").pack(side="left", padx=(0,8))
        tk.Button(af, text="添加预警", command=self._add_alert,
                  bg=self.C["accent"], fg=self.C["bg"], font=("Consolas", 9), cursor="hand2", relief="flat", width=8).pack(side="left")
        nf = tk.Frame(f, bg=self.C["card"]); nf.pack(fill="x", padx=8, pady=(4,0))
        self.notify_popup_var = tk.BooleanVar(value=True)
        self.notify_sound_var = tk.BooleanVar(value=True)
        tk.Checkbutton(nf, text="桌面弹窗通知", variable=self.notify_popup_var,
                       bg=self.C["card"], fg=self.C["tx"], selectcolor=self.C["bd"],
                       activebackground=self.C["card"], activeforeground=self.C["tx"],
                       font=("Consolas", 9)).pack(side="left", padx=(0,15))
        tk.Checkbutton(nf, text="声音告警", variable=self.notify_sound_var,
                       bg=self.C["card"], fg=self.C["tx"], selectcolor=self.C["bd"],
                       activebackground=self.C["card"], activeforeground=self.C["tx"],
                       font=("Consolas", 9)).pack(side="left")
        self.alert_list = tk.Listbox(f, height=5, font=("Consolas", 9),
                                     fg=self.C["tx"], bg=self.C["bg"],
                                     selectbackground=self.C["bd"], selectforeground=self.C["tx"],
                                     relief="flat", activestyle="none")
        self.alert_list.pack(fill="both", expand=True, padx=8, pady=(0,5))
        self.alert_list.insert(0, "XAUUSDc 黄金  预警阈值 1.0%")

    def _panel_auto_trade(self, parent):
        f = self._frame(parent, "自动交易")
        tf = tk.Frame(f, bg=self.C["card"]); tf.pack(fill="x", padx=8)
        tk.Label(tf, text="自动交易:", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,8))
        self.auto_on_var = tk.BooleanVar(value=False)
        tk.Checkbutton(tf, variable=self.auto_on_var, command=self._toggle_auto,
                       bg=self.C["card"], fg=self.C["tx"], selectcolor=self.C["accent"], font=("Consolas", 9)).pack(side="left")
        self.auto_status_var = tk.StringVar(value="已停止")
        tk.Label(tf, textvariable=self.auto_status_var, font=("Consolas", 9), fg=self.C["yellow"], bg=self.C["card"]).pack(side="left", padx=(10,0))
        lf = tk.Frame(f, bg=self.C["card"]); lf.pack(fill="x", padx=8, pady=(4,0))
        tk.Label(lf, text="手数:", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        tk.Spinbox(lf, from_=0.01, to=2.0, increment=0.01, textvariable=self.auto_lot_var, width=6,
                   font=("Consolas", 9), bg=self.C["bg"], fg=self.C["tx"], relief="flat").pack(side="left", padx=(0,15))
        tk.Label(lf, text="RSI买入<", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        tk.Spinbox(lf, from_=10, to=50, increment=1, textvariable=self.auto_rsi_buy_var, width=4,
                   font=("Consolas", 9), bg=self.C["bg"], fg=self.C["tx"], relief="flat").pack(side="left", padx=(0,10))
        tk.Label(lf, text="RSI卖出>", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        tk.Spinbox(lf, from_=50, to=90, increment=1, textvariable=self.auto_rsi_sell_var, width=4,
                   font=("Consolas", 9), bg=self.C["bg"], fg=self.C["tx"], relief="flat").pack(side="left", padx=(0,15))
        self.auto_log = tk.Text(f, height=5, font=("Consolas", 9), fg=self.C["tx"], bg=self.C["card"], relief="flat", state="disabled")
        self.auto_log.pack(fill="both", expand=True, padx=8, pady=(4,0))

    def _panel_ea(self, parent):
        f = self._frame(parent, "EA控制")
        ptf = tk.Frame(f, bg=self.C['card']); ptf.pack(fill='x', padx=8, pady=(4,0))
        tk.Label(ptf, text='MT5路径:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,4))
        self.ea_mt5_path_var = tk.StringVar(value=r'D:\MetaTrader 5 EXNESS')
        tk.Entry(ptf, textvariable=self.ea_mt5_path_var, font=('Consolas', 9),
                 bg=self.C['bg'], fg=self.C['tx'], relief='flat', width=45).pack(side='left', fill='x', expand=True, padx=(0,4))
        tk.Button(ptf, text='选择', command=self._select_mt5_path,
                  bg=self.C['card'], fg=self.C['accent'], font=('Consolas', 9),
                  cursor='hand2', relief='flat').pack(side='left', padx=2)
        bf = tk.Frame(f, bg=self.C['card']); bf.pack(fill='x', padx=8, pady=4)
        tk.Label(bf, text='EA状态:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,8))
        self.ea_status_var = tk.StringVar(value='未部署')
        tk.Label(bf, textvariable=self.ea_status_var, font=('Consolas', 9), fg=self.C['yellow'], bg=self.C['card']).pack(side='left', padx=(0,15))
        tk.Button(bf, text='编译部署EA', command=self._deploy_ea,
                  bg=self.C['accent'], fg=self.C['bg'], font=('Consolas', 9, 'bold'),
                  cursor='hand2', relief='flat', width=12).pack(side='left', padx=2)
        inf = tk.Frame(f, bg=self.C['card']); inf.pack(fill='x', padx=8, pady=(4,0))
        tk.Label(inf, text='1.选择MT5路径 -> 2.编译部署 -> 3.打开MT5 -> 4.导航窗口拖EA到图表 -> 5.勾选允许算法交易',
                 font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card'], wraplength=500).pack(anchor='w')
        self._check_ea_status()

    def _panel_backtest(self, parent):
        f = self._frame(parent, "历史回测 (MA交叉+RSI过滤)")
        tf = tk.Frame(f, bg=self.C["card"]); tf.pack(fill="x", padx=8)
        tk.Label(tf, text="周期:", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        bt_tf = tk.StringVar(value="H1")
        for opt in ["M15","M30","H1","H4"]:
            tk.Radiobutton(tf, text=opt, variable=bt_tf, value=opt, bg=self.C["card"], fg=self.C["tx"], selectcolor=self.C["bd"]).pack(side="left", padx=4)
        tk.Button(tf, text="执行回测", command=lambda: self._run_backtest(bt_tf),
                  bg=self.C["accent"], fg=self.C["bg"], font=("Consolas", 9), cursor="hand2", relief="flat", width=8).pack(side="left", padx=8)
        self.bt_result = tk.Text(f, height=8, font=("Consolas", 9), fg=self.C["tx"], bg=self.C["card"], relief="flat", state="disabled")
        self.bt_result.pack(fill="x", padx=8, pady=(4, 0))
    def _signal(self):
        a = self.anz.analyze("XAUUSDc", self.tv.get())
        if not a: return
        txt, col = a["overall"]
        cm = {"green": self.C["green"], "red": self.C["red"], "orange": self.C["yellow"], "lightgreen": self.C["green"], "gray": self.C["dim"]}
        if self.sl_label: self.sl_label.config(text=txt, fg=cm.get(col, self.C["yellow"]))
        else: self.sl.set(txt)
        d = f"Trend: {a['trend']}\nScore: Buy {a['bs']} | Sell {a['ss']}\n"
        if a["sup"]: d += f"Support: ${a['sup']:.1f}  Resistance: ${a['res']:.1f}\n"
        d += "\n"
        for n, l in a["sig"]:
            lc = self.C["green"] if l in ("买入", "偏多", "强势") else (self.C["red"] if l in ("卖出", "偏空", "强势") else self.C["yellow"])
            d += f"* {n}: {l}\n"
        self.sd.config(state="normal"); self.sd.delete("1.0", "end"); self.sd.insert("1.0", d); self.sd.config(state="disabled")
        self.ivars["ma5"].set(f"{a['ma']['5']:.2f}")
        self.ivars["ma10"].set(f"{a['ma']['10']:.2f}")
        self.ivars["ma20"].set(f"{a['ma']['20']:.2f}")
        self.ivars["ma50"].set(f"{a['ma']['50']:.2f}")
        self.ivars["rsi"].set(f"{a['rsi']:.1f}")
        self.ivars["macd"].set(f"{a['macd']:.2f}")
        if a["bb"]:
            self.ivars["bbu"].set(f"{a['bb'][0]:.2f}")
            self.ivars["bbl"].set(f"{a['bb'][2]:.2f}")
        self.ivars["atr"].set(f"{a['atr']:.2f}")
        self.vv.set(f"{a['atr'] / a['price'] * 100:.2f}% ({a['vol']})")

    def _account(self):
        try:
            i = self.anz.account()
            if not i: return
            for k, short in [("balance","bal"),("equity","eq"),("margin","mg"),("free","free")]:
                self.avars[short].set("${:,.2f}".format(i[k]))
            p = i["profit"]
            self.avars["prof"].set("${:+,.2f}".format(p))
            self.plbl.config(fg=self.C["green"] if p >= 0 else self.C["red"])
            pos = mt5.positions_get(symbol="XAUUSDc")
            ps = list(pos) if pos is not None and len(pos) > 0 else []
            txt = ""
            if ps:
                tick = mt5.symbol_info_tick("XAUUSDc")
                if tick:
                    for p in ps:
                        d = "SELL" if p.type == mt5.POSITION_TYPE_SELL else "BUY"
                        pnl = (tick.bid - p.price_open) * p.volume * 100 if p.type == mt5.POSITION_TYPE_BUY else (p.price_open - tick.bid) * p.volume * 100
                        st = "盈" if pnl >= 0 else "亏"
                        txt += f"{d} {p.volume:.2f}@{p.price_open:.2f} {st}${abs(pnl):.0f}\n"
                else:
                    txt = "行情数据获取失败"
            else:
                txt = "无持仓"
            self.pt.config(state="normal")
            self.pt.delete("1.0", "end")
            self.pt.insert("1.0", txt)
            self.pt.config(state="disabled")
            self.plbl.config(text="有持仓" if ps else "无持仓", fg=self.C["green"] if ps else self.C["dim"])
        except Exception as e:
            self.pt.config(state="normal")
            self.pt.delete("1.0", "end")
            self.pt.insert("1.0", "持仓获取错误: " + str(e))
            self.pt.config(state="disabled")

    def _chart(self):
        self.fig.clear(); a = self.anz.analyze("XAUUSDc", self.tv.get())
        if not a or a.get("rates") is None: return
        r = a["rates"]; n = min(len(r), 80)
        ti = np.arange(n); cl = r[-n:]["c"]; op = r[-n:]["o"]
        hi = r[-n:]["h"]; lo = r[-n:]["l"]
        m5 = np.convolve(cl, np.ones(5)/5, mode="valid")
        m10 = np.convolve(cl, np.ones(10)/10, mode="valid")
        m20 = np.convolve(cl, np.ones(20)/20, mode="valid")
        ax = self.fig.add_subplot(111); ax.set_facecolor(self.C["card"])
        for i in range(n):
            co = self.C["red"] if cl[i] >= op[i] else self.C["green"]
            ax.plot([ti[i], ti[i]], [lo[i], hi[i]], color=co, linewidth=0.8)
            ax.add_patch(Rectangle((ti[i]-0.3, min(cl[i], op[i])), 0.6, abs(cl[i]-op[i]), facecolor=co, edgecolor=co))
        o = n - len(m5); ax.plot(ti[o:], m5, "white", linewidth=1, label="MA5")
        o = n - len(m10); ax.plot(ti[o:], m10, "orange", linewidth=1, label="MA10")
        o = n - len(m20); ax.plot(ti[o:], m20, "blue", linewidth=1, label="MA20")
        if a["sup"]: ax.axhline(y=a["sup"], color="green", linestyle="--", alpha=0.5, label="支撑")
        if a["res"]: ax.axhline(y=a["res"], color="red", linestyle="--", alpha=0.5, label="阻力")
        ax.set_title(f"XAUUSDc {self.tv.get()}  当前: {a['price']:.2f}", color=self.C["tx"], fontsize=10)
        ax.tick_params(colors=self.C["dim"])
        for sp in ax.spines.values(): sp.set_color(self.C["bd"])
        ax.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
        self.fig.tight_layout(); self.canvas.draw()
    def _select_mt5_path(self):
        import tkinter.filedialog as fd
        path = fd.askdirectory(title='选择MT5终端目录')
        if path:
            self.ea_mt5_path_var.set(path)
            self._check_ea_status()

    def _deploy_ea(self):
        import subprocess, os, shutil
        mt5_dir = self.ea_mt5_path_var.get()
        src_ea = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'MQL5', 'Experts', 'GoldTrader_MTF_EA.mq5')
        dst_ea = os.path.join(mt5_dir, 'MQL5', 'Experts', 'GoldTrader_MTF_EA.mq5')
        if not os.path.exists(src_ea):
            self.ea_status_var.set('源码不存在')
            return
        os.makedirs(os.path.dirname(dst_ea), exist_ok=True)
        shutil.copy2(src_ea, dst_ea)
        editor = os.path.join(mt5_dir, 'MetaEditor64.exe')
        if os.path.exists(editor):
            result = subprocess.run([editor, '/compile:' + dst_ea, '/log'], capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and '0 errors' in result.stdout:
                self.ea_status_var.set('已部署(最新)')
            else:
                self.ea_status_var.set('编译失败')
        else:
            self.ea_status_var.set('未找到编译器')

    def _check_ea_status(self):
        ea_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'MQL5', 'Experts', 'GoldTrader_MTF_EA.ex5')
        if os.path.exists(ea_file):
            import time
            age = time.time() - os.path.getmtime(ea_file)
            if age < 3600:
                self.ea_status_var.set('已部署(最新)')
            else:
                self.ea_status_var.set('已部署')
        else:
            self.ea_status_var.set('未部署')

    def _toggle_auto(self):
        self.auto_on = self.auto_on_var.get()
        if self.auto_on:
            self.auto_status_var.set("运行中"); self._auto_log("已开启自动交易")
        else:
            self.auto_status_var.set("已停止"); self._auto_log("已关闭自动交易")

    def _auto_log(self, msg):
        self.auto_log.config(state="normal")
        self.auto_log.insert("end", f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
        self.auto_log.see("end")
        self.auto_log.config(state="disabled")

    def _add_alert(self):
        pct = self.alert_pct.get()
        self.alert_list.insert("end", f"XAUUSDc 黄金  预警阈值 {pct:.1f}%")
        self.alert_system.add("XAUUSDc", threshold_pct=pct)

    def _run_backtest(self, tf_var):
        tf = tf_var.get()
        self.bt_result.config(state="normal"); self.bt_result.delete("1.0", "end")
        self.bt_result.insert("1.0", "回测中..."); self.bt_result.config(state="disabled")
        threading.Thread(target=self._do_backtest, args=(tf,), daemon=True).start()

    def _do_backtest(self, tf):
        r = self.anz.backtest("XAUUSDc", tf)
        if r is None:
            txt = "数据不足，无法回测"
        else:
            txt = f"周期: {tf} | 初始资金: ${r['initial_capital']:,.0f}\n"
            txt += f"最终资金: ${r['final_capital']:,.0f}  收益: ${r['total_return']:+.2f}%\n"
            txt += f"交易次数: {r['total_trades']} | 胜率: {r['win_rate']:.1f}%\n"
            txt += f"盈亏比: {r['profit_factor']:.2f}"
        self.bt_result.config(state="normal")
        self.bt_result.delete("1.0", "end")
        self.bt_result.insert("1.0", txt); self.bt_result.config(state="disabled")

    def _quick_trade(self, action):
        try:
            lot = self.quick_lot_var.get()
            sym = "XAUUSDc"
            res = self.anz.order_send(sym, action, lot)
            if res and res.retcode == 0:
                co = self.C["red"] if action == "buy" else self.C["green"]
                txt = f"{action.upper()} {lot}手 @ {sym}\n"
                self.sd.config(state="normal")
                self.sd.insert("end", txt)
                self.sd.config(state="disabled", fg=co)
            else:
                err = res.error if res else "未知错误"
                self._auto_log(f"交易失败: {err}")
        except Exception as _e:
            self._auto_log(f"交易错误: {str(_e)[:50]}")

    def _auto_trade_step(self):
        try:
            if not self.auto_on: return
            acc = self.anz.account()
            if not acc: return
            if acc["balance"] >= TARGET_BALANCE:
                self.auto_on = False
                self.auto_status_var.set("目标达成!")
                self._auto_log(f"恭喜! 达到目标 ${TARGET_BALANCE:.0f}")
                return
            pos = self.anz.positions("XAUUSDc")
            if pos and len(pos) >= AUTO_MAX_POS: return
            h1 = self.anz.analyze("XAUUSDc", "H1")
            m5 = self.anz.analyze("XAUUSDc", "M5")
            tick = self.anz.tick("XAUUSDc")
            if not h1 or not m5 or not tick: return
            lot = self.auto_lot_var.get()
            price = tick.bid
            if h1["trend"] in ("强势上涨", "偏多") and m5["rsi"] < self.auto_rsi_buy_var.get():
                has_buy = any(p.type == mt5.POSITION_TYPE_BUY for p in (pos or []))
                if not has_buy:
                    sl = price * 0.98
                    tp = price * 1.04
                    res = self.anz.order_send("XAUUSDc", "buy", lot, sl=sl, tp=tp)
                    if res and res.retcode == 0:
                        self._auto_log(f"买入 {lot}手 @{price:.2f}")
            elif h1["trend"] in ("强势下跌", "偏空") and m5["rsi"] > self.auto_rsi_sell_var.get():
                has_sell = any(p.type == mt5.POSITION_TYPE_SELL for p in (pos or []))
                if not has_sell:
                    sl = price * 1.02
                    tp = price * 0.96
                    res = self.anz.order_send("XAUUSDc", "sell", lot, sl=sl, tp=tp)
                    if res and res.retcode == 0:
                        self._auto_log(f"卖出 {lot}手 @{price:.2f}")
            if pos:
                for p in pos:
                    pnl = (price - p.price_open) * p.volume * 100 if p.type == mt5.POSITION_TYPE_BUY else (p.price_open - price) * p.volume * 100
                    if pnl > 50:
                        close_act = "sell" if p.type == mt5.POSITION_TYPE_BUY else "buy"
                        res = self.anz.order_send("XAUUSDc", close_act, p.volume)
                        if res and res.retcode == 0:
                            self._auto_log(f"止盈平仓 ${pnl:+.0f}")
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = GoldAnalyzerApp(root)
    root.protocol("WM_DELETE_WINDOW", app._close)
    root.mainloop()

# -*- coding: utf-8 -*-
"""
黄金分析助手 v3.071 - 完整版
功能：实时行情、信号分析、自动交易、EA控制、价格预警、历史回测
"""
import MetaTrader5 as mt5
import numpy as np
import threading
import time
import configparser
import os

def _dbg(msg):
    try:
        with open(r"E:\MySoftware\黄金分析工具_Portable\debug.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime("%H:%M:%S")} {msg}\n")
    except: pass
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print('tkinter missing'); exit(1)

# 自适应选择matplotlib后端：优先Qt5Agg(GPU加速)，回退TkAgg(CPU)
def _setup_matplotlib_backend():
    try:
        import matplotlib
        matplotlib.use('Qt5Agg')
        from matplotlib.backends.backend_qt5agg import FigureCanvasTkAgg
        print('使用 Qt5Agg 后端 (GPU加速)')
        return FigureCanvasTkAgg
    except ImportError:
        pass
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    print('使用 TkAgg 后端 (CPU渲染)')
    return FigureCanvasTkAgg

FigureCanvasTkAgg = _setup_matplotlib_backend()
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

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
WINDOW_WIDTH = int(_cfg.get('Window', 'width', fallback='1920'))
WINDOW_HEIGHT = int(_cfg.get('Window', 'height', fallback='1080'))
REFRESH_MS = int(_cfg.get('Display', 'refresh_interval_ms', fallback='1000'))
COUNTDOWN_MS = 1000  # 倒计时更新频率
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
        self.ok = False
        self._connecting = False
        self._sym_digits = {}
        # 尝试连接但不强制初始化
        try:
            acc = mt5.account_info()
            if acc:
                self.ok = True
        except:
            pass

    def shutdown(self):
        if self.ok: mt5.shutdown()

    def tick(self, s):
        return mt5.symbol_info_tick(s) if self.ok else None

    def rates(self, s, tf, n=100):
        if not self.ok: return None
        r = mt5.copy_rates_from_pos(s, self.TF_MAP.get(tf, mt5.TIMEFRAME_H1), 0, n)
        return np.array([(x['time'],x['open'],x['high'],x['low'],x['close'],x['tick_volume'])
                        for x in r], dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8')]) if r is not None and len(r) > 0 else None

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
        if len(c)<s: return 0, [], []
        # 计算历史MACD - 使用EMA而非简单均值
        macd_hist = []
        for i in range(s-1, len(c)):
            window = c[i-s+1:i+1]
            m = np.mean(window[-f:]) - np.mean(window[-s:])
            macd_hist.append(m)
        # 计算信号线
        signal = []
        for i in range(len(macd_hist)):
            if i < 8:
                signal.append(sum(macd_hist[:i+1])/(i+1))
            else:
                signal.append(0.2*macd_hist[i] + 0.8*signal[-1])
        return macd_hist[-1], signal[-1], macd_hist

    def bb(self, c, p=20, k=2):
        if len(c)<p: return None
        m = np.mean(c[-p:]); sd = np.std(c[-p:])
        return m+k*sd, m, m-k*sd

    def atr_history(self, h, l, c, p=14):
        """计算历史ATR数组"""
        if len(h) < p+1: return []
        tr = []
        for i in range(len(h)):
            if i == 0:
                tr.append(h[i] - l[i])
            else:
                tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
        atr_arr = [tr[0]]
        for i in range(1, len(tr)):
            atr_arr.append((atr_arr[-1] * (p-1) + tr[i]) / p)
        return atr_arr

    def atr(self, h, l, c, p=14):
        if len(h)<p+1: return 0
        tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(-p,0)]
        return np.mean(tr)

    def levels(self, rates):
        if rates is None or len(rates)<20: return None, None
        h = rates['high']; l = rates['low']
        resist, supp = [], []
        for i in range(5, len(h)-5):
            if h[i] == max(h[i-5:i+6]): resist.append(h[i])
            if l[i] == min(l[i-5:i+6]): supp.append(l[i])
        return max(resist) if resist else None, min(supp) if supp else None

    def analyze(self, sym='XAUUSDc', tf='H1'):
        r = self.rates(sym, tf, 100)
        if r is None or len(r) == 0: return None
        c,h,l = r['close'],r['high'],r['low']
        t = self.tick(sym)
        if not t: return None
        ma = {p:self.ma(c,p) for p in [5,10,20,50]}
        rsi = self.rsi(c)
        m, ms, mh = self.macd(c)  # m=当前值, ms=信号线, mh=历史值列表
        bb = self.bb(c)
        atr = self.atr(h,l,c)
        atr_hist = self.atr_history(h,l,c)
        res, sup = self.levels(r)
        sig, trend = [], ""
        if ma[5]>ma[10]>ma[20]: trend,sig="强势上涨",[("MA Bullish","Strong")]
        elif ma[5]<ma[10]<ma[20]: trend,sig="强势下跌",[("MA Bearish","Strong")]
        elif ma[5]>ma[10]: trend,sig="偏多",[("MA Up","中性")]
        elif ma[5]<ma[10]: trend,sig="偏空",[("MA Down","中性")]
        if rsi>70: sig.append((f"RSI={rsi:.0f} 超买区域","卖出"))
        elif rsi<30: sig.append((f"RSI={rsi:.0f} 超卖区域","买入"))
        elif rsi>60: sig.append((f"RSI={rsi:.0f}","偏空"))
        elif rsi<40: sig.append((f"RSI={rsi:.0f}","偏多"))
        else: sig.append((f"RSI={rsi:.0f}","中性"))
        # mh现在是列表，用ms(信号线)做比较
        if m>0 and ms>0: sig.append(("MACD金叉","买入"))
        elif m<0 and ms<0: sig.append(("MACD死叉","卖出"))
        if bb and t.bid<bb[2]: sig.append(("触布林下轨","买入"))
        elif bb and t.bid>bb[0]: sig.append(("触布林上轨","卖出"))
        if sup and t.bid-sup<2: sig.append((f"支撑 ${sup:.1f}","关注"))
        if res and res-t.bid<2: sig.append((f"阻力 ${res:.1f}","关注"))
        ap = atr/t.bid*100 if t.bid>0 else 0
        vl = "高" if ap>0.5 else ("中" if ap>0.2 else "低")
        bs = sum(1 for x in sig if x[1] in ("买入","偏多","Strong"))
        ss = sum(1 for x in sig if x[1] in ("卖出","偏空","Strong"))
        if bs>ss+2: ov=("强力买入","green")
        elif bs>ss: ov=("偏多","lightgreen")
        elif ss>bs+2: ov=("强力卖出","red")
        elif ss>bs: ov=("偏空","orange")
        else: ov=("观望","gray")
        return {'sym':sym,'name':self.SYMBOLS.get(sym,sym),'price':t.bid,'ask':t.ask,
                'spread':t.ask-t.bid,'trend':trend,'ma':ma,'rsi':rsi,'macd':m,'macd_hist':mh,
                'bb':bb,'atr':atr,'atr_hist':atr_hist,'atr_pct':ap,'vol':vl,'signals':sig,'overall':ov,
                'bs':bs,'ss':ss,'res':res,'sup':sup,'closes':c,'rates':r}
    def connect(self):
        """连接MT5 - 只在未连接时初始化，避免影响登录缓存"""
        if getattr(self, "_connecting", False): return
        if self.ok: return  # 已连接则不重复初始化
        self._connecting = True
        try:
            # 先尝试获取账户信息，如果成功说明已连接
            acc = mt5.account_info()
            if acc:
                self.ok = True
                self._connecting = False
                for sym in self.SYMBOLS:
                    si = mt5.symbol_info(sym)
                    if si:
                        self._sym_digits[sym] = si.digits if hasattr(si, "digits") else 2
                return
            # 未连接时才尝试初始化，带超时保护
            import threading
            result = [None]
            def _init_thread():
                result[0] = mt5.initialize(path=TERMINAL_PATH)
            t = threading.Thread(target=_init_thread, daemon=True)
            t.start()
            t.join(timeout=8)
            if t.is_alive():
                self.ok = False
                _dbg("MT5 init timeout")
            else:
                self.ok = result[0]
                if self.ok:
                    for sym in self.SYMBOLS:
                        si = mt5.symbol_info(sym)
                        if si:
                            self._sym_digits[sym] = si.digits if hasattr(si, "digits") else 2
            self._connecting = False
        except Exception as e:
            self._connecting = False
            _dbg(f'connect error: {e}')

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
        if r is None or len(r) < 50: return None
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
        self.root.title("黄金分析助手 v3.071")
        self.stop = False
        self.auto_on = False
        self.ea_status_var = tk.StringVar(value='未部署')
        self.notifier = ToastNotifier() if HAS_TOAST else None
        self.C = {'bg': '#010810', 'card': '#081420', 'card2': '#0c1e30',
                  'bd': '#1a3550', 'bd_glow': '#00d4ff',
                  'tx': '#c8e6ff', 'dim': '#3a6a9b', 'accent': '#00d4ff',
                  'green': '#00e878', 'red': '#ff4060', 'yellow': '#ffaa00',
                  'highlight': '#7b68ee', 'glow': '#00ffcc'}
        self.anz = MT5Engine()
        self.anz.connect()
        self.alert_system = AlertSystem()
        self._prev = {}
        self._prev_close = {}
        self._init_vars()
        self._build_ui()
        self._initialized = True
        self._set_period_m1('M1')
        self._set_period_h1('H1')
        self._start_refresh()

    def _init_vars(self):
        self.price_vars = {}; self.pcl = {}; self.daily_vars = {}; self.daily_lbls = {}
        self.tv = tk.StringVar(value="M1")
        self.sl = tk.StringVar(value="分析中...")
        self.period_btns_m1 = []
        self.period_btns_h1 = []
        self.countdown_var = tk.StringVar(value="--:--")  # 周期倒计时
        self.countdown_annot = None  # 倒计时标注对象
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
        self.alert_thresh_var = tk.StringVar(value="1.0%")  # 实时阈值显示

    def _frame(self, parent, title):
        # 科技感面板 - 顶部发光边框
        f = tk.Frame(parent, bg=self.C["bd"], relief="flat")
        f.pack(fill="x", padx=10, pady=6)
        inner = tk.Frame(f, bg=self.C["card"], relief="flat")
        inner.pack(fill="x", padx=2, pady=2)
        # 标题栏 - 渐变效果
        hdr = tk.Frame(inner, bg=self.C["card2"], height=2)
        hdr.pack(fill="x")
        hdr_fr = tk.Frame(hdr, bg=self.C["card2"], height=2)
        hdr_fr.pack(fill="x", pady=(0, 4))
        tk.Label(inner, text="◆ " + title, font=("Consolas", 9, "bold"),
                 fg=self.C["accent"], bg=self.C["card"]).pack(fill="x", padx=12, pady=(4, 3))
        return inner

    def _build_ui(self):
        self.root.configure(bg=self.C["bg"])
        # 读取保存的窗口位置和大小
        try:
            import configparser as _cfg_mod
            _cfg_w = _cfg_mod.ConfigParser()
            _cfg_w.read(r'E:\MySoftware\黄金分析工具_Portable\config.ini', encoding='utf-8')
            _win_w = int(_cfg_w.get('Window', 'width', fallback=str(WINDOW_WIDTH)))
            _win_h = int(_cfg_w.get('Window', 'height', fallback=str(WINDOW_HEIGHT)))
            _win_x = _cfg_w.get('Window', 'x', fallback='center')
            _win_y = _cfg_w.get('Window', 'y', fallback='center')
        except:
            _win_w, _win_h = WINDOW_WIDTH, WINDOW_HEIGHT
            _win_x, _win_y = 'center', 'center'
        
        self.root.geometry(f'{_win_w}x{_win_h}')
        self.root.update_idletasks()
        # 居中或恢复保存的位置
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if _win_x == 'center':
            x_pos = (sw - _win_w) // 2
        else:
            x_pos = max(0, min(int(_win_x), sw - _win_w))
        if _win_y == 'center':
            y_pos = max(20, (sh - _win_h) // 2)
        else:
            y_pos = max(20, min(int(_win_y), sh - _win_h - 40))
        self.root.geometry(f'{_win_w}x{_win_h}+{x_pos}+{y_pos}')
        # 保存位置到配置
        try:
            _cfg_w.set('Window', 'x', str(x_pos))
            _cfg_w.set('Window', 'y', str(y_pos))
            _cfg_w.write(open(r'E:\MySoftware\黄金分析工具_Portable\config.ini', 'w', encoding='utf-8'))
        except: pass
        tf = tk.Frame(self.root, bg=self.C["bg"])
        tf.pack(fill="x", padx=20, pady=(10, 5))
        tk.Label(tf, text="\u26a1 HJ ANALYZER  v3.071 \u26a1", font=("Consolas", 14, "bold"),
                 fg=self.C["accent"], bg=self.C["bg"]).pack(side="left")
        self.conn_lbl = tk.Label(tf, textvariable=self.conn_var, font=("Consolas", 8, "bold"),
                 fg=self.C["green"], bg=self.C["bg"])
        self.conn_lbl.pack(side="left", padx=(20, 0))
        tk.Label(tf, text="XAUUSDc  REAL-TIME  AUTO  ALERTS", font=("Consolas", 8),
                 fg=self.C["dim"], bg=self.C["bg"]).pack(side="left", padx=(20, 0))
        # 目标进度条
        self.target_bar = tk.Frame(tf, bg=self.C["bd"], height=4, relief="flat")
        self.target_bar.pack(side="right", padx=(15, 0))
        self.target_bar_fr = tk.Frame(self.target_bar, bg=self.C["green"], height=4, relief="flat")
        self.target_bar_fr.pack(side="left", fill="y")
        tk.Label(tf, textvariable=self.target_var, font=("Consolas", 8, "bold"),
                 fg=self.C["yellow"], bg=self.C["bg"]).pack(side="right", padx=(5, 0))
        # 新三列布局：M1图表 | H1图表 | 可折叠面板
        main = tk.Frame(self.root, bg=self.C["bg"])
        main.pack(fill="both", expand=True, padx=16, pady=6)
        # 左列：M1 K线图 (固定宽度600px)
        left = tk.Frame(main, bg=self.C["bg"])
        left.pack(side="left", fill="both", padx=(0, 8))
        left.pack_propagate(False)
        left.configure(width=600)
        self._panel_chart_m1(left)
        # 中列：H1 K线图 (固定宽度600px)
        mid = tk.Frame(main, bg=self.C["bg"])
        mid.pack(side="left", fill="both", padx=(0, 8))
        mid.pack_propagate(False)
        mid.configure(width=600)
        self._panel_chart_h1(mid)
        # 右列：可滚动面板（可折叠区域）
        right = tk.Frame(main, bg=self.C["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.right_canvas = tk.Canvas(right, bg=self.C["bg"], highlightthickness=0)
        self.right_scroll = tk.Scrollbar(right, orient="vertical", command=self.right_canvas.yview)
        self.right_scrollable = tk.Frame(self.right_canvas, bg=self.C["bg"])
        self.right_scrollable.bind("<Configure>", lambda e: self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all")))
        self.right_canvas.create_window((0, 0), window=self.right_scrollable, anchor="nw")
        self.right_canvas.configure(yscrollcommand=self.right_scroll.set)
        self.right_canvas.pack(side="left", fill="both", expand=True)
        self.right_scroll.pack(side="right", fill="y")
        self.right_canvas.bind("<MouseWheel>", lambda e: self.right_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        
        # 右侧折叠面板
        self._build_right_panels()
        self.root.after(100, self._fix_right_scroll)
    
    def _fix_right_scroll(self):
        """修复右侧滚动区域"""
        try:
            self.right_canvas.configure(scrollregion=self.right_canvas.bbox('all'))
        except:
            pass

    def _panel_prices(self, parent):
        f = self._frame(parent, "实时行情")
        for sym, name in MT5Engine.SYMBOLS.items():
            row = tk.Frame(f, bg=self.C["card"])
            row.pack(fill="x", padx=8, pady=3)
            kf = tk.Frame(row, bg=self.C["card"])
            kf.pack(side="left")
            tk.Label(kf, text="● "+sym, font=("Consolas", 9, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="left")
            tk.Label(kf, text=name, font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(4, 0))
            vv = tk.StringVar(value="--")
            self.price_vars[sym] = vv
            tk.Label(row, textvariable=vv, font=("Consolas", 10, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="right", padx=(10, 0))
            dv = tk.StringVar(value="--")
            self.daily_vars[sym] = dv
            dl = tk.Label(row, textvariable=dv, font=("Consolas", 8), fg=self.C["yellow"], bg=self.C["card"])
            dl.pack(side="right", padx=(0, 4))
            self.daily_lbls[sym] = dl
            cl = tk.Frame(row, width=8, height=8, bg=self.C["bg"])
            cl.pack(side="right", padx=4)
            self.pcl[sym] = cl
    def _panel_signal(self, parent):
        f = self._frame(parent, '信号分析')
        tk.Label(f, textvariable=self.sl, font=('Consolas', 11, 'bold'), fg=self.C['yellow'], bg=self.C['card']).pack(pady=(0, 4))
        tf = tk.Frame(f, bg=self.C['card']); tf.pack(fill='x')
        self.period_btns = []
        for opt in ['M1','M5','M6','M15','M30','H1','H4','D1']:
            btn = tk.Button(tf, text='▸'+opt, font=('Consolas', 8, 'bold'), fg=self.C['accent'], bg=self.C['card'], highlightthickness=1, highlightcolor=self.C['bd'],
                      activebackground=self.C['accent'], relief='flat', cursor='hand2',
                      command=lambda o=opt: self._set_period(o))
            btn.pack(side='left', padx=4)
            self.period_btns.append(btn)
        # 初始化高亮当前周期
        self._set_period(self.tv.get())
        self.sd = tk.Text(f, height=1, font=('Consolas', 9), fg=self.C['tx'], bg=self.C['card'],
                          insertbackground=self.C['tx'], relief='flat', state='disabled')
        self.sd.pack(fill='x', padx=8, pady=(4, 0))
        qf = tk.Frame(f, bg=self.C['card']); qf.pack(fill='x', padx=8, pady=(4, 6))
        tk.Label(qf, text='快捷交易:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left')
        self.quick_lot_var = tk.DoubleVar(value=0.01)
        tk.Spinbox(qf, from_=0.01, to=2.0, increment=0.01, textvariable=self.quick_lot_var, width=6,
                 font=('Consolas', 9), bg=self.C['bg'], fg=self.C['tx'], relief='flat').pack(side='left', padx=(0,4))
        tk.Button(qf, text='▲ BUY', font=('Consolas', 9, 'bold'), fg='white', bg=self.C['green'], relief='flat', cursor='hand2',
                 command=lambda: self._quick_trade('buy'), highlightthickness=2, highlightbackground=self.C['glow']).pack(side='left', padx=4)
        tk.Button(qf, text='▼ SELL', font=('Consolas', 9, 'bold'), fg='white', bg=self.C['red'], relief='flat', cursor='hand2',
                 command=lambda: self._quick_trade('sell'), highlightthickness=2, highlightbackground=self.C['glow']).pack(side='left', padx=4)

    def _panel_account(self, parent):
        f = self._frame(parent, '账户信息')
        
        # 账户概览 - 横向卡片布局
        overview = tk.Frame(f, bg=self.C['card'])
        overview.pack(fill='x', padx=8, pady=6)
        
        # 四宫格显示主要指标
        # 第一行：余额 | 权益
        row1 = tk.Frame(overview, bg=self.C['card'])
        row1.pack(fill='x', pady=2)
        
        # 余额
        bal_card = tk.Frame(row1, bg=self.C['card2'], relief='solid', bd=1)
        bal_card.pack(side='left', fill='both', expand=True, padx=3)
        tk.Label(bal_card, text='账户余额', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=(4,0))
        self.avars['bal'] = tk.StringVar(value='--')
        tk.Label(bal_card, textvariable=self.avars['bal'], font=('Consolas', 13, 'bold'), fg=self.C['accent'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=2)
        
        # 权益
        eq_card = tk.Frame(row1, bg=self.C['card2'], relief='solid', bd=1)
        eq_card.pack(side='left', fill='both', expand=True, padx=3)
        tk.Label(eq_card, text='账户权益', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=(4,0))
        self.avars['eq'] = tk.StringVar(value='--')
        tk.Label(eq_card, textvariable=self.avars['eq'], font=('Consolas', 13, 'bold'), fg=self.C['glow'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=2)
        
        # 第二行：保证金 | 可用 | 盈亏
        row2 = tk.Frame(overview, bg=self.C['card'])
        row2.pack(fill='x', pady=4)
        
        # 保证金
        mg_card = tk.Frame(row2, bg=self.C['card2'], relief='solid', bd=1)
        mg_card.pack(side='left', fill='both', expand=True, padx=3)
        tk.Label(mg_card, text='保证金占用', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=(4,0))
        self.avars['mg'] = tk.StringVar(value='--')
        tk.Label(mg_card, textvariable=self.avars['mg'], font=('Consolas', 11, 'bold'), fg=self.C['yellow'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=2)
        
        # 可用资金
        free_card = tk.Frame(row2, bg=self.C['card2'], relief='solid', bd=1)
        free_card.pack(side='left', fill='both', expand=True, padx=3)
        tk.Label(free_card, text='可用资金', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=(4,0))
        self.avars['free'] = tk.StringVar(value='--')
        tk.Label(free_card, textvariable=self.avars['free'], font=('Consolas', 11, 'bold'), fg=self.C['green'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=2)
        
        # 盈亏
        prof_card = tk.Frame(row2, bg=self.C['card2'], relief='solid', bd=1)
        prof_card.pack(side='left', fill='both', expand=True, padx=3)
        tk.Label(prof_card, text='当日盈亏', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card2']).pack(anchor='w', padx=6, pady=(4,0))
        self.avars['prof'] = tk.StringVar(value='--')
        self.prof_lbl = tk.Label(prof_card, textvariable=self.avars['prof'], font=('Consolas', 11, 'bold'), bg=self.C['card2'])
        self.prof_lbl.pack(anchor='w', padx=6, pady=2)
        
        # 分割线
        tk.Frame(f, bg=self.C['bd'], height=1).pack(fill='x', pady=4)
        
        # 持仓信息 - 自适应高度（无滚动条）
        pos_header = tk.Frame(f, bg=self.C['card'])
        pos_header.pack(fill='x', padx=8)
        tk.Label(pos_header, text='当前持仓', font=('Consolas', 9, 'bold'), fg=self.C['accent'], bg=self.C['card']).pack(side='left')
        tk.Label(pos_header, text='•••', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card']).pack(side='right')
        
        # 持仓文本框 - 根据内容自动调整高度
        self.pt = tk.Text(f, height=2, font=('Consolas', 9), fg=self.C['tx'], bg=self.C['card'], 
                         relief='flat', state='disabled', wrap='word')
        self.pt.pack(fill='x', padx=8, pady=(2, 6))

    def _panel_chart(self, parent):
        f = self._frame(parent, 'K线图表')
        tf = tk.Frame(f, bg=self.C['card']); tf.pack(fill='x')
        self.chart_tv_m1 = tk.StringVar(value='M1')
        self.chart_tv_h1 = tk.StringVar(value='H1')
        for opt in ['M1','M5','M6','M15','M30','H1','H4','D1']:
            tk.Button(tf, text='▸'+opt, font=('Consolas', 8, 'bold'), fg=self.C['accent'], bg=self.C['card'], highlightthickness=1, highlightcolor=self.C['bd'],
                      activebackground=self.C['accent'], relief='flat', cursor='hand2',
                      command=lambda o=opt: self.chart_tv.set(o) or self.tv.set(o) or (self._chart_m1(), self._chart_h1(), self._signal())[-1]).pack(side='left', padx=4)
        self.fig = Figure(figsize=(12, 14), facecolor=self.C['card'])
        self.canvas = FigureCanvasTkAgg(self.fig, master=f)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)


    def _panel_chart_m1(self, parent):
        """M1周期图表面板"""
        f = self._frame(parent, 'M1 K线图表')
        tf = tk.Frame(f, bg=self.C['card']); tf.pack(fill='x')
        self.chart_tv_m1 = tk.StringVar(value='M1')
        for opt in ['M1','M5','M6','M15','M30','H1','H4','D1']:
            btn = tk.Button(tf, text='▸'+opt, font=('Consolas', 8, 'bold'), fg=self.C['accent'], bg=self.C['card'], highlightthickness=1, highlightcolor=self.C['bd'],
                      activebackground=self.C['accent'], relief='flat', cursor='hand2',
                      command=lambda o=opt: self._set_period_m1(o))
            btn.pack(side='left', padx=4)
            self.period_btns_m1.append(btn)
        self._set_period_m1('M1')
        self.fig_m1 = Figure(figsize=(6, 7), facecolor=self.C['card'])
        self.canvas_m1 = FigureCanvasTkAgg(self.fig_m1, master=f)
        self.canvas_m1.get_tk_widget().pack(fill='both', expand=True)

    def _panel_chart_h1(self, parent):
        """H1周期图表面板"""
        f = self._frame(parent, 'H1 K线图表')
        tf = tk.Frame(f, bg=self.C['card']); tf.pack(fill='x')
        self.chart_tv_h1 = tk.StringVar(value='H1')
        for opt in ['M1','M5','M6','M15','M30','H1','H4','D1']:
            btn = tk.Button(tf, text='▸'+opt, font=('Consolas', 8, 'bold'), fg=self.C['accent'], bg=self.C['card'], highlightthickness=1, highlightcolor=self.C['bd'],
                      activebackground=self.C['accent'], relief='flat', cursor='hand2',
                      command=lambda o=opt: self._set_period_h1(o))
            btn.pack(side='left', padx=4)
            self.period_btns_h1.append(btn)
        self._set_period_h1('H1')
        self.fig_h1 = Figure(figsize=(6, 7), facecolor=self.C['card'])
        self.canvas_h1 = FigureCanvasTkAgg(self.fig_h1, master=f)
        self.canvas_h1.get_tk_widget().pack(fill='both', expand=True)

    def _set_period_m1(self, tf):
        """设置M1周期"""
        self.chart_tv_m1.set(tf)
        if hasattr(self, '_initialized') and self._initialized:
            self._chart_m1()
        periods = ['M1','M5','M6','M15','M30','H1','H4','D1']
        for j, btn in enumerate(self.period_btns_m1):
            if j < len(periods):
                if periods[j] == tf:
                    btn.config(bg=self.C['accent'], fg=self.C['bg'])
                else:
                    btn.config(bg=self.C['card'], fg=self.C['accent'])

    def _set_period_h1(self, tf):
        """设置H1周期"""
        self.chart_tv_h1.set(tf)
        if hasattr(self, '_initialized') and self._initialized:
            self._chart_h1()
        periods = ['M1','M5','M6','M15','M30','H1','H4','D1']
        for j, btn in enumerate(self.period_btns_h1):
            if j < len(periods):
                if periods[j] == tf:
                    btn.config(bg=self.C['accent'], fg=self.C['bg'])
                else:
                    btn.config(bg=self.C['card'], fg=self.C['accent'])

    def _chart_m1(self):
        """绘制M1图表"""
        self.fig_m1.clear()
        a = self.anz.analyze("XAUUSDc", self.chart_tv_m1.get())
        if not a or a.get("rates") is None: return
        self._draw_chart(self.fig_m1, a, "M1", self.chart_tv_m1)

    def _chart_h1(self):
        """绘制H1图表"""
        self.fig_h1.clear()
        a = self.anz.analyze("XAUUSDc", self.chart_tv_h1.get())
        if not a or a.get("rates") is None: return
        self._draw_chart(self.fig_h1, a, "H1", self.chart_tv_h1)

    def _draw_chart(self, fig, a, title_prefix, chart_tv):
        """通用图表绘制方法"""
        from matplotlib import gridspec
        r = a["rates"]; n = min(len(r), 80)
        ti = np.arange(n); cl = r[-n:]["close"]; op = r[-n:]["open"]
        hi = r[-n:]["high"]; lo = r[-n:]["low"]
        m5 = np.convolve(cl, np.ones(5)/5, mode="valid")
        m10 = np.convolve(cl, np.ones(10)/10, mode="valid")
        m20 = np.convolve(cl, np.ones(20)/20, mode="valid")
        gs = gridspec.GridSpec(3, 1, height_ratios=[8, 3, 3], hspace=0.08)
        ax = fig.add_subplot(gs[0]); ax.set_facecolor(self.C["card"])
        ax_atr = fig.add_subplot(gs[1]); ax_atr.set_facecolor(self.C["card"])
        ax_macd = fig.add_subplot(gs[2]); ax_macd.set_facecolor(self.C["card"])
        for i in range(n):
            co = self.C["red"] if cl[i] >= op[i] else self.C["green"]
            ax.plot([ti[i], ti[i]], [lo[i], hi[i]], color=co, linewidth=1.2)
            ax.add_patch(Rectangle((ti[i]-0.4, min(cl[i], op[i])), 0.8, abs(cl[i]-op[i]), facecolor=co, edgecolor=co, linewidth=0.5))
        o = n - len(m5); ax.plot(ti[o:], m5, "white", linewidth=1, label="MA5")
        o = n - len(m10); ax.plot(ti[o:], m10, "orange", linewidth=1, label="MA10")
        o = n - len(m20); ax.plot(ti[o:], m20, "blue", linewidth=1, label="MA20")
        # 布林带
        if a.get("bb") and len(r) > 20:
            # 计算历史布林带
            closes = r["close"]
            bb_mid_hist = []
            bb_std_hist = []
            for i in range(19, len(closes)):
                window = closes[i-19:i+1]
                bb_mid_hist.append(float(np.mean(window)))
                bb_std_hist.append(float(np.std(window)))
            bb_upper_hist = [bb_mid_hist[i] + 2*bb_std_hist[i] for i in range(len(bb_mid_hist))]
            bb_lower_hist = [bb_mid_hist[i] - 2*bb_std_hist[i] for i in range(len(bb_mid_hist))]
            # 截取与n匹配的长度
            start_idx = len(bb_upper_hist) - n
            bb_upper_hist = bb_upper_hist[start_idx:]
            bb_mid_hist = bb_mid_hist[start_idx:]
            bb_lower_hist = bb_lower_hist[start_idx:]
            ax.plot(ti, bb_upper_hist, "cyan", linewidth=0.8, alpha=0.7, label="BOLL上轨")
            ax.plot(ti, bb_mid_hist, "cyan", linewidth=0.5, alpha=0.5, label="BOLL中轨")
            ax.plot(ti, bb_lower_hist, "cyan", linewidth=0.8, alpha=0.7, label="BOLL下轨")
            ax.fill_between(ti, bb_upper_hist, bb_lower_hist, alpha=0.1, color="cyan")
        # 价格横线
        if a.get("price"):
            ax.axhline(y=a["price"], color=self.C["yellow"], linestyle="-", linewidth=1.5, alpha=0.8, label="当前价")
        if a["sup"]: ax.axhline(y=a["sup"], color="green", linestyle="--", alpha=0.5, label="支撑")
        if a["res"]: ax.axhline(y=a["res"], color="red", linestyle="--", alpha=0.5, label="阻力")
        ax.set_title(title_prefix + " " + chart_tv.get() + " 当前: " + f"{a['price']:.2f}", color=self.C["tx"], fontsize=10)
        ax.tick_params(colors=self.C["tx"])
        for sp in ax.spines.values(): sp.set_color(self.C["bd"])
        ax.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
        ax.set_ylabel("价格", color=self.C["tx"])
        ax.tick_params(axis='y', labelcolor=self.C["tx"])
        # ATR
        atr_val = a.get("atr", 0)
        atr_hist = a.get("atr_hist", [])
        if atr_hist and len(atr_hist) > 0:
            disp_len = min(len(atr_hist), len(ti))
            ax_atr.plot(ti[-disp_len:], atr_hist[-disp_len:], "purple", linewidth=1.5, label="ATR")
            ax_atr.fill_between(ti[-disp_len:], 0, atr_hist[-disp_len:], alpha=0.3, color="purple")
            ax_atr.axhline(y=atr_val, color="yellow", linewidth=1, linestyle="--", alpha=0.7)
            ax_atr.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
            ax_atr.set_ylabel("ATR", color=self.C["tx"])
            ax_atr.tick_params(axis='y', labelcolor=self.C["tx"])
            ax_atr.tick_params(axis='x', labelcolor=self.C['tx'])
            ax_atr.set_title("ATR 平均真实波幅", color=self.C["tx"], fontsize=9)
        # MACD
        macd_hist = a.get("macd_hist", [])
        if macd_hist and len(macd_hist) > 0:
            macd_line = macd_hist[-n:] if len(macd_hist) >= n else macd_hist
            ti_macd = np.arange(len(macd_line))
            signal_line = []
            for i in range(len(macd_line)):
                if i < 8:
                    signal_line.append(sum(macd_line[:i+1])/(i+1))
                else:
                    signal_line.append(0.2*macd_line[i] + 0.8*signal_line[-1])
            colors = [self.C["green"] if v >= 0 else self.C["red"] for v in macd_line]
            ax_macd.bar(ti_macd, macd_line, color=colors, alpha=0.6, width=0.6)
            ax_macd.plot(ti_macd, macd_line, "cyan", linewidth=1, label="MACD")
            ax_macd.plot(ti_macd, signal_line, "orange", linewidth=1, label="Signal")
            ax_macd.axhline(y=0, color=self.C["bd"], linewidth=0.5)
            ax_macd.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
            ax_macd.set_ylabel("MACD", color=self.C["tx"])
            ax_macd.tick_params(axis='y', labelcolor=self.C["tx"])
            ax_macd.tick_params(axis='x', labelcolor=self.C['tx'])
            ax_macd.set_title("MACD 指数平滑异同", color=self.C["tx"], fontsize=9)
            ax_macd.set_ylim(min(macd_line)*1.2 if macd_line else -1, max(macd_line)*1.2 if macd_line else 1)
        fig.subplots_adjust(hspace=0.08)
        fig.canvas.draw()

    def _build_right_panels(self):
        """构建右侧可折叠面板"""
        # 上方折叠区
        self.top_collapser = tk.Frame(self.right_scrollable, bg=self.C["bg"])
        self.top_collapser.pack(fill="x", pady=(0, 6))
        self.top_header = tk.Frame(self.top_collapser, bg=self.C["card"])
        self.top_header.pack(fill="x", padx=8, pady=4)
        self.top_toggle = tk.Button(self.top_header, text="▼", font=("Consolas", 8), 
                                     fg=self.C["accent"], bg=self.C["card"], relief="flat",
                                     cursor="hand2", command=self._toggle_top)
        self.top_toggle.pack(side="left")
        tk.Label(self.top_header, text="实时数据", font=("Consolas", 9, "bold"),
                 fg=self.C["tx"], bg=self.C["card"]).pack(side="left", padx=6)
        self.top_content = tk.Frame(self.right_scrollable, bg=self.C["card"])
        self.top_content.pack(fill="x", padx=8, pady=2)
        self._panel_prices(self.top_content)
        self._panel_signal(self.top_content)
        self._panel_account(self.top_content)
        # 下方折叠区
        self.bottom_collapser = tk.Frame(self.right_scrollable, bg=self.C["bg"])
        self.bottom_collapser.pack(fill="x", pady=(6, 0))
        self.bottom_header = tk.Frame(self.bottom_collapser, bg=self.C["card"])
        self.bottom_header.pack(fill="x", padx=8, pady=4)
        self.bottom_toggle = tk.Button(self.bottom_header, text="▼", font=("Consolas", 8), 
                                        fg=self.C["accent"], bg=self.C["card"], relief="flat",
                                        cursor="hand2", command=self._toggle_bottom)
        self.bottom_toggle.pack(side="left")
        tk.Label(self.bottom_header, text="分析与交易", font=("Consolas", 9, "bold"),
                 fg=self.C["tx"], bg=self.C["card"]).pack(side="left", padx=6)
        self.bottom_content = tk.Frame(self.right_scrollable, bg=self.C["card"])
        self.bottom_content.pack(fill="x", padx=8, pady=2)
        self._panel_indicators(self.bottom_content)
        self._panel_alerts(self.bottom_content)
        self._panel_auto_trade(self.bottom_content)
        self._panel_settings(self.bottom_content)

    def _toggle_top(self):
        if self.top_content.winfo_ismapped():
            self.top_content.pack_forget()
            self.top_toggle.config(text="▶")
        else:
            self.top_content.pack(fill="x", padx=8, pady=2)
            self.top_toggle.config(text="▼")

    def _toggle_bottom(self):
        if self.bottom_content.winfo_ismapped():
            self.bottom_content.pack_forget()
            self.bottom_toggle.config(text="▶")
        else:
            self.bottom_content.pack(fill="x", padx=8, pady=2)
            self.bottom_toggle.config(text="▼")

    def _panel_indicators(self, parent):
        f = self._frame(parent, "技术指标")
        
        # ===== 布林带可视化面板 =====
        bbf = tk.LabelFrame(f, text="◎ BOLL 布林带 [20,2]", font=("Consolas", 9, "bold"),
                           fg=self.C["accent"], bg=self.C["card"], labelanchor="n", padx=8, pady=6,
                           relief="flat", bd=0)
        bbf.pack(fill="x", padx=10, pady=6)
        
        # 布林带三轨显示
        bf = tk.Frame(bbf, bg=self.C["card"])
        bf.pack(fill="x")
        
        # 上轨
        uf = tk.Frame(bf, bg=self.C["card"])
        uf.pack(fill="x", pady=2)
        tk.Label(uf, text="上轨 UPPER:", font=("Consolas", 8), fg=self.C["red"], bg=self.C["card"]).pack(side="left")
        self.bb_upper_var = tk.StringVar(value="--")
        tk.Label(uf, textvariable=self.bb_upper_var, font=("Consolas", 9, "bold"), fg=self.C["red"], bg=self.C["card"]).pack(side="right")
        
        # 中轨
        mf = tk.Frame(bf, bg=self.C["card"])
        mf.pack(fill="x", pady=2)
        tk.Label(mf, text="中轨 MIDDLE:", font=("Consolas", 8), fg=self.C["accent"], bg=self.C["card"]).pack(side="left")
        self.bb_mid_var = tk.StringVar(value="--")
        tk.Label(mf, textvariable=self.bb_mid_var, font=("Consolas", 9, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="right")
        
        # 下轨
        lf = tk.Frame(bf, bg=self.C["card"])
        lf.pack(fill="x", pady=2)
        tk.Label(lf, text="下轨 LOWER:", font=("Consolas", 8), fg=self.C["green"], bg=self.C["card"]).pack(side="left")
        self.bb_lower_var = tk.StringVar(value="--")
        tk.Label(lf, textvariable=self.bb_lower_var, font=("Consolas", 9, "bold"), fg=self.C["green"], bg=self.C["card"]).pack(side="right")
        
        # 价格位置指示器
        pf = tk.Frame(bbf, bg=self.C["card"])
        pf.pack(fill="x", pady=4)
        tk.Label(pf, text="价格位置:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        self.bb_pos_var = tk.StringVar(value="--")
        tk.Label(pf, textvariable=self.bb_pos_var, font=("Consolas", 9, "bold"), fg=self.C["yellow"], bg=self.C["card"]).pack(side="right")
        
        # 布林带宽度
        wf = tk.Frame(bbf, bg=self.C["card"])
        wf.pack(fill="x", pady=2)
        tk.Label(wf, text="带宽 WIDTH:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        self.bb_width_var = tk.StringVar(value="--")
        tk.Label(wf, textvariable=self.bb_width_var, font=("Consolas", 9), fg=self.C["tx"], bg=self.C["card"]).pack(side="right")
        
        # ===== RSI 可视化 =====
        rsif = tk.LabelFrame(f, text="⚡ RSI 相对强弱指数", font=("Consolas", 9, "bold"),
                            fg=self.C["accent"], bg=self.C["card"], labelanchor="n", padx=8, pady=6)
        rsif.pack(fill="x", padx=10, pady=6)
        
        rsif2 = tk.Frame(rsif, bg=self.C["card"])
        rsif2.pack(fill="x")
        tk.Label(rsif2, text="数值:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        self.rsi_val_var = tk.StringVar(value="--")
        tk.Label(rsif2, textvariable=self.rsi_val_var, font=("Consolas", 11, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="left", padx=(0, 20))
        
        # RSI状态标签
        self.rsi_state_var = tk.StringVar(value="中性")
        tk.Label(rsif2, textvariable=self.rsi_state_var, font=("Consolas", 9, "bold"), 
                bg=self.C["card"], relief="solid", bd=1, padx=8, pady=2).pack(side="right")
        
        # ===== MACD 可视化 =====
        macdf = tk.LabelFrame(f, text="⚛ MACD 指数平滑异同", font=("Consolas", 9, "bold"),
                             fg=self.C["accent"], bg=self.C["card"], labelanchor="n", padx=8, pady=6,
                             relief="flat", bd=0)
        macdf.pack(fill="x", padx=10, pady=6)
        
        macdf2 = tk.Frame(macdf, bg=self.C["card"])
        macdf2.pack(fill="x")
        tk.Label(macdf2, text="MACD值:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        self.macd_val_var = tk.StringVar(value="--")
        tk.Label(macdf2, textvariable=self.macd_val_var, font=("Consolas", 11, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="left", padx=(0, 20))
        
        self.macd_state_var = tk.StringVar(value="观望")
        tk.Label(macdf2, textvariable=self.macd_state_var, font=("Consolas", 9, "bold"),
                bg=self.C["card"], relief="solid", bd=1, padx=8, pady=2).pack(side="right")
        
        # ===== MA系统可视化 =====
        maf = tk.LabelFrame(f, text="◆ MA 移动平均系统", font=("Consolas", 9, "bold"),
                           fg=self.C["accent"], bg=self.C["card"], labelanchor="n", padx=8, pady=6,
                           relief="flat", bd=0)
        maf.pack(fill="x", padx=10, pady=6)
        
        ma_grid = tk.Frame(maf, bg=self.C["card"])
        ma_grid.pack(fill="x")
        
        # MA排列状态
        self.ma_trend_var = tk.StringVar(value="--")
        tk.Label(ma_grid, text="排列:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        tk.Label(ma_grid, textvariable=self.ma_trend_var, font=("Consolas", 10, "bold"),
                bg=self.C["card"], relief="solid", bd=1, padx=10, pady=3).pack(side="left", padx=(0, 20))
        
        # MA数值
        for i, (name, var) in enumerate([("MA5", self.ivars["ma5"]), ("MA10", self.ivars["ma10"]), 
                                          ("MA20", self.ivars["ma20"]), ("MA50", self.ivars["ma50"])]):
            mf2 = tk.Frame(ma_grid, bg=self.C["card"])
            mf2.pack(side="left", padx=5)
            tk.Label(mf2, text=name+":", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack()
            tk.Label(mf2, textvariable=var, font=("Consolas", 9, "bold"), fg=self.C["tx"], bg=self.C["card"]).pack()
        
        # ===== ATR波动率 =====
        atrf = tk.LabelFrame(f, text="⚡ ATR 波动率分析", font=("Consolas", 9, "bold"),
                            fg=self.C["accent"], bg=self.C["card"], labelanchor="n", padx=8, pady=6,
                            relief="flat", bd=0)
        atrf.pack(fill="x", padx=10, pady=6)
        
        atrf2 = tk.Frame(atrf, bg=self.C["card"])
        atrf2.pack(fill="x")
        tk.Label(atrf2, text="ATR:", font=("Consolas", 8), fg=self.C["dim"], bg=self.C["card"]).pack(side="left")
        self.atr_val_var = tk.StringVar(value="--")
        tk.Label(atrf2, textvariable=self.atr_val_var, font=("Consolas", 11, "bold"), fg=self.C["accent"], bg=self.C["card"]).pack(side="left", padx=(0, 20))
        
        self.vol_state_var = tk.StringVar(value="--")
        tk.Label(atrf2, textvariable=self.vol_state_var, font=("Consolas", 9, "bold"),
                bg=self.C["card"], relief="solid", bd=1, padx=8, pady=2).pack(side="right")


    def _set_period(self, tf):
        self.tv.set(tf)
        if hasattr(self, '_initialized') and self._initialized:
            self._signal()
            # 只在数据变化时才重绘图表，减少卡顿
            if not hasattr(self, '_last_chart_time') or (datetime.now().timestamp() - self._last_chart_time) > 5:
                self._chart_m1()
                self._chart_h1()
                self._last_chart_time = datetime.now().timestamp()
        # 更新按钮样式
        periods = ['M1','M5','M6','M15','M30','H1','H4','D1']
        for j, btn in enumerate(self.period_btns):
            if j < len(periods):
                if periods[j] == tf:
                    btn.config(bg=self.C['accent'], fg=self.C['bg'])
                else:
                    btn.config(bg=self.C['card'], fg=self.C['accent'])

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
            # 优化：减少不必要的tick调用，只在需要时获取
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
                        self.daily_lbls[sym].config(fg=dco)
            self._signal()
            self._account()
            # 只在数据变化时才重绘图表，减少卡顿
            if not hasattr(self, '_last_chart_time') or (datetime.now().timestamp() - self._last_chart_time) > 5:
                self._chart_m1()
                self._chart_h1()
                self._last_chart_time = datetime.now().timestamp()
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
        self._start_countdown_timer()
    
    def _start_countdown_timer(self):
        """启动倒计时定时器 - 每秒更新，但只在实际变化时更新标题"""
        self._update_countdown()
        self.root.after(1000, self._start_countdown_timer)

    def _update_countdown(self):
        """更新周期倒计时 - 优化版，减少不必要的标题更新"""
        try:
            now = datetime.now()
            m1_tf = getattr(self, "chart_tv_m1", None)
            h1_tf = getattr(self, "chart_tv_h1", None)
            m1_tf = m1_tf.get() if m1_tf else "M1"
            h1_tf = h1_tf.get() if h1_tf else "H1"
            period_secs = {"M1": 60, "M5": 300, "M6": 360, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
            m1_secs = period_secs.get(m1_tf, 60)
            h1_secs = period_secs.get(h1_tf, 3600)
            epoch = now.timestamp()
            m1_rem = int(m1_secs - (epoch % m1_secs))
            h1_rem = int(h1_secs - (epoch % h1_secs))
            m1_str = f"{m1_rem//60:02d}:{m1_rem%60:02d}"
            h1_str = f"{h1_rem//60:02d}:{h1_rem%60:02d}"
            # 只在实际变化时更新标题，减少重绘
            new_title = f"HJ ANALYZER v3.071 - M1:{m1_str} H1:{h1_str}"
            if self.root.title() != new_title:
                self.root.title(new_title)
            self.countdown_var.set(m1_str)
        except Exception as e:
            _dbg(f"_update_countdown error: {e}")

    def _check_ea_status(self):
        """检查EA状态"""
        try:
            status = "未部署"
            if hasattr(self, "ea_status_var"):
                self.ea_status_var.set(status)
        except:
            pass

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
        af = tk.Frame(f, bg=self.C["card"]); af.pack(fill="x", padx=10)
        tk.Label(af, text="波动阈值 %:", font=("Consolas", 9), fg=self.C["dim"], bg=self.C["card"]).pack(side="left", padx=(0,4))
        tk.Spinbox(af, from_=0.5, to=10, increment=0.5, textvariable=self.alert_pct, width=5,
                   font=("Consolas", 9), bg=self.C["bg"], fg=self.C["tx"], relief="flat").pack(side="left", padx=(0,8))
        self.alert_pct.trace_add("write", lambda *args: self.alert_thresh_var.set("{:.1f}%".format(self.alert_pct.get())))
        tk.Button(af, text="添加预警", command=self._add_alert,
                  bg=self.C["accent"], fg=self.C["bg"], font=("Consolas", 9), cursor="hand2", relief="flat", width=8).pack(side="left")
        # 实时阈值显示
        self.alert_thresh_lbl = tk.Label(af, textvariable=self.alert_thresh_var, font=("Consolas", 9, "bold"),
                                         fg=self.C["yellow"], bg=self.C["card"])
        self.alert_thresh_lbl.pack(side="left", padx=(10, 0))
        nf = tk.Frame(f, bg=self.C["card"]); nf.pack(fill="x", padx=10, pady=(4,0))
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

    def _add_alert(self):
        """添加价格预警"""
        try:
            pct = float(self.alert_pct.get())
            sym = "XAUUSDc"
            name = "黄金"
            self.alert_list.insert(tk.END, f"{sym} {name} 预警阈值 {pct:.1f}%")
            self.alert_list.see(tk.END)
        except:
            pass

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

    def _toggle_auto(self):
        """切换自动交易开关"""
        self.auto_on = self.auto_on_var.get()
        if self.auto_on:
            self.auto_status_var.set("运行中")
            self._log_auto("自动交易已开启")
        else:
            self.auto_status_var.set("已停止")
            self._log_auto("自动交易已关闭")

    def _log_auto(self, msg):
        """记录自动交易日志"""
        try:
            self.auto_log.config(state='normal')
            self.auto_log.insert(tk.END, msg + "\n")
            self.auto_log.see(tk.END)
            self.auto_log.config(state='disabled')
        except:
            pass

    def _panel_ea(self, parent):
        f = self._frame(parent, "EA控制")
        ptf = tk.Frame(f, bg=self.C['card']); ptf.pack(fill='x', padx=8, pady=(4,0))
        tk.Label(ptf, text='MT5路径:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,4))
        self.ea_mt5_path_var = tk.StringVar(value=r'D:\MetaTrader 5 EXNESS')
        tk.Entry(ptf, textvariable=self.ea_mt5_path_var, font=('Consolas', 9),
                 bg=self.C['bg'], fg=self.C['tx'], relief='flat', width=45).pack(side='left', fill='x', expand=True, padx=(0,4))
        tk.Button(ptf, text='选择', command=self._select_mt5_path,
                  bg=self.C['card'], fg=self.C['accent'], font=('Consolas', 9),
                  cursor='hand2', relief='flat').pack(side='left', padx=4)
        bf = tk.Frame(f, bg=self.C['card']); bf.pack(fill='x', padx=8, pady=4)
        tk.Label(bf, text='EA状态:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,8))
        self.ea_status_var = tk.StringVar(value='未部署')
        tk.Label(bf, textvariable=self.ea_status_var, font=('Consolas', 9), fg=self.C['yellow'], bg=self.C['card']).pack(side='left', padx=(0,15))
        tk.Button(bf, text='编译部署EA', command=self._deploy_ea,
                  bg=self.C['accent'], fg=self.C['bg'], font=('Consolas', 9, 'bold'),
                  cursor='hand2', relief='flat', width=12).pack(side='left', padx=4)
        inf = tk.Frame(f, bg=self.C['card']); inf.pack(fill='x', padx=8, pady=(4, 6))
        tk.Label(inf, text='INFO 1.选择MT5路径 -> 2.编译部署 -> 3.打开MT5 -> 4.拖EA到图表 -> 5.勾选允许算法交易',
                 font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card'], wraplength=500).pack(anchor='w')
        self._check_ea_status()


    def _panel_settings(self, parent):
        """设置面板"""
        f = self._frame(parent, "设置")
        # MT5连接设置
        stf = tk.Frame(f, bg=self.C['card']); stf.pack(fill='x', padx=8, pady=(4,0))
        tk.Label(stf, text='MT5终端:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,4))
        self.settings_mt5_path_var = tk.StringVar(value=r'D:\\MetaTrader 5 EXNESS')
        tk.Entry(stf, textvariable=self.settings_mt5_path_var, font=('Consolas', 9),
                 bg=self.C['bg'], fg=self.C['tx'], relief='flat', width=40).pack(side='left', fill='x', expand=True, padx=(0,4))
        tk.Button(stf, text='选择', command=self._select_mt5_path,
                  bg=self.C['card'], fg=self.C['accent'], font=('Consolas', 9),
                  cursor='hand2', relief='flat').pack(side='left', padx=4)
        tk.Button(stf, text='重连', command=self._reconnect_mt5,
                  bg=self.C['accent'], fg=self.C['bg'], font=('Consolas', 9, 'bold'),
                  cursor='hand2', relief='flat', width=6).pack(side='left', padx=4)
        # 连接状态
        sf = tk.Frame(f, bg=self.C['card']); sf.pack(fill='x', padx=8, pady=(4,0))
        tk.Label(sf, text='连接状态:', font=('Consolas', 9), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(0,8))
        self.settings_conn_var = tk.StringVar(value='未连接')
        tk.Label(sf, textvariable=self.settings_conn_var, font=('Consolas', 9), fg=self.C['yellow'], bg=self.C['card']).pack(side='left', padx=(0,15))
        # 保存按钮
        bf = tk.Frame(f, bg=self.C['card']); bf.pack(fill='x', padx=8, pady=4)
        tk.Button(bf, text='保存设置', command=self._save_settings,
                  bg=self.C['accent'], fg=self.C['bg'], font=('Consolas', 9, 'bold'),
                  cursor='hand2', relief='flat', width=10).pack(side='left')
        tk.Label(bf, text='提示: 修改路径后需点击重连', font=('Consolas', 8), fg=self.C['dim'], bg=self.C['card']).pack(side='left', padx=(10,0))

    
    def _select_mt5_path(self):
        """选择MT5终端路径"""
        from tkinter import filedialog
        fpath = filedialog.askopenfilename(
            title='选择MT5终端',
            filetypes=[('EXE文件', '*.exe'), ('所有文件', '*.*')],
            initialdir=r'D:\\'
        )
        if fpath:
            dir_path = os.path.dirname(fpath)
            self.settings_mt5_path_var.set(dir_path)
            _dbg(f'Selected MT5 path: {dir_path}')

    def _reconnect_mt5(self):
        """重新连接MT5 - 使用线程避免阻塞UI，支持切换MT5终端"""
        import threading
        def _do_reconnect():
            try:
                mt5_path = self.settings_mt5_path_var.get()
                self.anz.shutdown()
                import time; time.sleep(1)
                import MetaTrader5 as mt5_module
                result = mt5_module.initialize(path=mt5_path)
                def _update_ui():
                    if result:
                        self.anz.ok = True
                        self.settings_conn_var.set('已连接')
                        if hasattr(self, 'conn_lbl'):
                            self.conn_lbl.config(fg=self.C['green'])
                    else:
                        self.settings_conn_var.set('连接失败')
                        if hasattr(self, 'conn_lbl'):
                            self.conn_lbl.config(fg=self.C['red'])
                self.root.after(0, _update_ui)
            except Exception as e:
                def _update_err():
                    self.settings_conn_var.set('错误')
                    _dbg(f'_reconnect_mt5 error: {e}')
                self.root.after(0, _update_err)
        threading.Thread(target=_do_reconnect, daemon=True).start()
        self.settings_conn_var.set('连接中...')

    def _save_settings(self):
        """保存设置到配置文件"""
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read(r'E:\MySoftware\黄金分析工具_Portable\config.ini', encoding='utf-8')
            if 'MT5' not in config:
                config['MT5'] = {}
            config['MT5']['terminal_path'] = self.settings_mt5_path_var.get()
            config.write(open(r'E:\MySoftware\黄金分析工具_Portable\config.ini', 'w', encoding='utf-8'))
            self.settings_conn_var.set('已保存')
            _dbg('设置已保存')
        except Exception as e:
            _dbg(f'_save_settings error: {e}')

    def _signal(self):
        a = self.anz.analyze("XAUUSDc", self.tv.get())
        if not a: return
        txt, col = a["overall"]
        cm = {"green": self.C["green"], "red": self.C["red"], "orange": self.C["yellow"], "lightgreen": self.C["green"], "gray": self.C["dim"]}
        if self.sl_label: self.sl_label.config(text=txt, fg=cm.get(col, self.C["yellow"]))
        else: self.sl.set(txt)
        # 简体中文 + 科技感样式
        trend_cn = {"上涨": "\U0001f4c8 上升趋势", "下跌": "\U0001f4c9 下降趋势", "盘整": "\u27a1\uFE0F 横盘整理"}.get(a["trend"], a["trend"])
        d = "\u250c\u2500 趋势分析 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n"
        d += "| " + trend_cn + " " * (20 - len(trend_cn)) + " |\n"
        d += "| 多头得分: {:<3} |  空头得分: {:<3}   |\n".format(a["bs"], a["ss"])
        d += "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n\n"
        if a["sup"]: d += "[支撑位] ${:.1f}      [阻力位] ${:.1f}\n\n".format(a["sup"], a["res"])
        d += "\u250c\u2500 技术指标 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n"
        for n, l in a["signals"]:
            icon = "\u25cf" if l in ("\u4e70\u5165", "\u504f\u591a") else ("\u25cf" if l in ("\u5356\u51fa", "\u504f\u7a7a") else "\u25cb")
            d += "| {} {:<16} {}  |\n".format(icon, n, l)
        d += "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518"
        # MA系统
        ma5, ma10, ma20, ma50 = a['ma'][5], a['ma'][10], a['ma'][20], a['ma'][50]
        ma5_str = f"{ma5:.2f}"
        ma10_str = f"{ma10:.2f}"
        ma20_str = f"{ma20:.2f}"
        ma50_str = f"{ma50:.2f}"
        # 添加MA状态
        if ma5 > ma10 > ma20:
            ma5_str += " 多头"
        elif ma5 < ma10 < ma20:
            ma5_str += " 空头"
        self.ivars['ma5'].set(ma5_str)
        self.ivars['ma10'].set(ma10_str)
        self.ivars['ma20'].set(ma20_str)
        self.ivars['ma50'].set(ma50_str)
        
        # RSI - 超买超卖
        rsi = a['rsi']
        rsi_str = f"{rsi:.1f}"
        if rsi > 70: rsi_str += " 🔴超买"
        elif rsi < 30: rsi_str += " 🟢超卖"
        else: rsi_str += " ⚪中性"
        self.ivars['rsi'].set(rsi_str)
        
        # MACD - 金叉死叉
        macd_val = a['macd']
        macd_str = f"{macd_val:.2f}"
        if macd_val > 0: macd_str += " ↑金叉"
        elif macd_val < 0: macd_str += " ↓死叉"
        self.ivars['macd'].set(macd_str)
        
        # 布林带
        if a.get("bb") and isinstance(a["bb"], tuple) and len(a["bb"]) == 3:
            self.bb_upper_var.set(f"{a['bb'][0]:.2f}")
            self.bb_mid_var.set(f"{a['bb'][1]:.2f}")
            self.bb_lower_var.set(f"{a['bb'][2]:.2f}")
            # 价格位置
            price = a.get('price', 0)
            if price > 0:
                pos_pct = (price - a['bb'][2]) / (a['bb'][0] - a['bb'][2]) * 100 if a['bb'][0] != a['bb'][2] else 50
                self.bb_pos_var.set(f"{pos_pct:.1f}%")
            # 带宽
            bw = (a['bb'][0] - a['bb'][2]) / a['bb'][1] * 100 if a['bb'][1] > 0 else 0
            self.bb_width_var.set(f"{bw:.2f}%")
        
        # RSI
        rsi = a.get('rsi', 0)
        rsi_str = f"{rsi:.1f}"
        if rsi > 70: 
            rsi_str += " 🔴超买"
            self.rsi_state_var.set("超买")
        elif rsi < 30: 
            rsi_str += " 🟢超卖"
            self.rsi_state_var.set("超卖")
        else: 
            self.rsi_state_var.set("中性")
        self.rsi_val_var.set(rsi_str)
        
        # MACD
        macd_val = a.get('macd', 0)
        macd_str = f"{macd_val:.2f}"
        if macd_val > 0: 
            macd_str += " ↑金叉"
            self.macd_state_var.set("金叉")
        elif macd_val < 0: 
            macd_str += " ↓死叉"
            self.macd_state_var.set("死叉")
        else:
            self.macd_state_var.set("观望")
        self.macd_val_var.set(macd_str)
        
        # MA状态
        ma5, ma10, ma20, ma50 = a['ma'][5], a['ma'][10], a['ma'][20], a['ma'][50]
        if ma5 > ma10 > ma20:
            self.ma_trend_var.set("多头 ↑↑↑")
        elif ma5 < ma10 < ma20:
            self.ma_trend_var.set("空头 ↓↓↓")
        else:
            self.ma_trend_var.set("横盘 →")
        
        # ATR波动率
        atr = a.get('atr', 0)
        vol_pct = atr / a.get('price', 1) * 100 if a.get('price', 0) > 0 else 0
        vol_str = f"{vol_pct:.2f}%"
        vol_state = a.get('vol', '中')
        if vol_state == '高': 
            vol_str += " 🔥高波"
            self.vol_state_var.set("高波")
        elif vol_state == '低': 
            vol_str += " ❄️低波"
            self.vol_state_var.set("低波")
        else:
            self.vol_state_var.set("中波")
        self.atr_val_var.set(f"{atr:.2f}")
        self.vv.set(vol_str)
        # 更新信号分析文本框
        self.sd.config(state="normal")
        self.sd.delete("1.0", "end")
        self.sd.insert("1.0", d)
        self.sd.config(state="disabled")
        # 自适应高度
        lines_count = d.count('\n') + 1
        self.sd.config(height=min(max(lines_count, 3), 15))

    def _account(self):
        try:
            i = self.anz.account()
            if not i: return
            # 只在数值变化时更新UI
            bal = '\$' + '{:,.2f}'.format(i['balance'])
            if getattr(self, '_last_bal', None) != bal:
                self.avars['bal'].set(bal)
                self._last_bal = bal
            eq = '\$' + '{:,.2f}'.format(i['equity'])
            if getattr(self, '_last_eq', None) != eq:
                self.avars['eq'].set(eq)
                self._last_eq = eq
            mg = '\$' + '{:,.2f}'.format(i['margin'])
            if getattr(self, '_last_mg', None) != mg:
                self.avars['mg'].set(mg)
                self._last_mg = mg
            free = '\$' + '{:,.2f}'.format(i['free'])
            if getattr(self, '_last_free', None) != free:
                self.avars['free'].set(free)
                self._last_free = free
            p = i['profit']
            prof = '\$' + '{:+,.2f}'.format(p)
            if getattr(self, '_last_prof', None) != prof:
                self.avars['prof'].set(prof)
                self._last_prof = prof
                self.prof_lbl.config(fg=self.C['green'] if p >= 0 else self.C['red'])
            # 持仓每2秒更新一次
            now = datetime.now().timestamp()
            if not hasattr(self, '_last_pos_time') or (now - self._last_pos_time) > 2:
                self._last_pos_time = now
                pos = mt5.positions_get(symbol='XAUUSDc')
                ps = list(pos) if pos is not None and len(pos) > 0 else []
                txt = ''
                if ps:
                    tick = mt5.symbol_info_tick('XAUUSDc')
                    if tick:
                        for p in ps:
                            d = 'SELL' if p.type == mt5.POSITION_TYPE_SELL else 'BUY'
                            pnl = (tick.bid - p.price_open) * p.volume * 100 if p.type == mt5.POSITION_TYPE_BUY else (p.price_open - tick.bid) * p.volume * 100
                            st = '盈' if pnl >= 0 else '亏'
                            txt += f'{d} {p.volume:.2f}@{p.price_open:.2f} {st}\
'
                    else:
                        txt = '行情数据获取失败'
                else:
                    txt = '无持仓'
                self.pt.config(state='normal')
                self.pt.delete('1.0', 'end')
                self.pt.insert('1.0', txt)
                self.pt.config(state='disabled')

        except Exception as e:
            self.pt.config(state='normal')
            self.pt.delete('1.0', 'end')
            self.pt.insert('1.0', '持仓获取错误: ' + str(e))
            self.pt.config(state='disabled')

    def _chart(self):
        self.countdown_annot = None  # 重置倒计时标注
        self.fig.clear(); a = self.anz.analyze("XAUUSDc", self.tv.get())
        if not a or a.get("rates") is None: return
        r = a["rates"]; n = min(len(r), 80)
        ti = np.arange(n); cl = r[-n:]["close"]; op = r[-n:]["open"]
        hi = r[-n:]["high"]; lo = r[-n:]["low"]
        m5 = np.convolve(cl, np.ones(5)/5, mode="valid")
        m10 = np.convolve(cl, np.ones(10)/10, mode="valid")
        m20 = np.convolve(cl, np.ones(20)/20, mode="valid")
        # 创建三面板：K线图占70%，ATR和MACD各占15%
        from matplotlib import gridspec
        gs = gridspec.GridSpec(3, 1, height_ratios=[8, 4, 4], hspace=0.08)
        ax = self.fig.add_subplot(gs[0]); ax.set_facecolor(self.C["card"])
        ax_atr = self.fig.add_subplot(gs[1]); ax_atr.set_facecolor(self.C["card"])
        ax_macd = self.fig.add_subplot(gs[2]); ax_macd.set_facecolor(self.C["card"])
        for i in range(n):
            co = self.C["red"] if cl[i] >= op[i] else self.C["green"]
            ax.plot([ti[i], ti[i]], [lo[i], hi[i]], color=co, linewidth=1.2)
            ax.add_patch(Rectangle((ti[i]-0.4, min(cl[i], op[i])), 0.8, abs(cl[i]-op[i]), facecolor=co, edgecolor=co, linewidth=0.5))
        o = n - len(m5); ax.plot(ti[o:], m5, "white", linewidth=1, label="MA5")
        o = n - len(m10); ax.plot(ti[o:], m10, "orange", linewidth=1, label="MA10")
        o = n - len(m20); ax.plot(ti[o:], m20, "blue", linewidth=1, label="MA20")
        
        # 绘制布林带
        if a.get("bb"):
            bb_upper, bb_mid, bb_lower = a["bb"]
            # 计算布林带历史数据 - 使用r中的完整数据
            bb_mids = []
            bb_stds = []
            for i in range(19, len(r["close"])):
                window = r["close"][i-19:i+1]
                bb_mids.append(np.mean(window))
                bb_stds.append(np.std(window))
            bb_uppers = [bb_mids[i] + 2*bb_stds[i] for i in range(len(bb_mids))]
            bb_lowers = [bb_mids[i] - 2*bb_stds[i] for i in range(len(bb_mids))]
            # 只取最后n个数据点
            bb_mids = bb_mids[-n:]
            bb_uppers = bb_uppers[-n:]
            bb_lowers = bb_lowers[-n:]
            ax.plot(ti, bb_uppers, "cyan", linewidth=0.8, alpha=0.7, label="BOLL上轨")
            ax.plot(ti, bb_mids, "cyan", linewidth=0.5, alpha=0.5, label="BOLL中轨")
            ax.plot(ti, bb_lowers, "cyan", linewidth=0.8, alpha=0.7, label="BOLL下轨")
            ax.fill_between(ti, bb_uppers, bb_lowers, alpha=0.1, color="cyan")
        
        # 添加价格横线
        if a.get("price"):
            ax.axhline(y=a["price"], color=self.C["yellow"], linestyle="-", linewidth=1.5, alpha=0.8, label="当前价")
        if a["sup"]: ax.axhline(y=a["sup"], color="green", linestyle="--", alpha=0.5, label="支撑")
        if a["res"]: ax.axhline(y=a["res"], color="red", linestyle="--", alpha=0.5, label="阻力")
        # 添加倒计时显示
        countdown_str = self.countdown_var.get()
        if self.countdown_annot:
            self.countdown_annot.set_text(f"倒计时: {countdown_str}")
        else:
            self.countdown_annot = ax.annotate(f"倒计时: {countdown_str}", xy=(1, 0.95), xycoords="axes fraction", fontsize=10,
                    ha="right", va="top", color=self.C["yellow"], fontweight="bold")
        ax.set_title("XAUUSDc " + self.tv.get() + "  当前: " + f"{a['price']:.2f}", color=self.C["tx"], fontsize=10)
        ax.tick_params(colors=self.C["tx"])
        for sp in ax.spines.values(): sp.set_color(self.C["bd"])
        ax.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
        ax.set_ylabel("价格", color=self.C["tx"])
        ax.tick_params(axis='y', labelcolor=self.C["tx"])
        
        # 绘制ATR波动率
        atr_val = a.get("atr", 0)
        atr_pct = a.get("atr_pct", 0)
        atr_hist = a.get("atr_hist", [])
        if atr_hist and len(atr_hist) > 0:
            disp_len = min(len(atr_hist), len(ti))
            ax_atr.plot(ti[-disp_len:], atr_hist[-disp_len:], "purple", linewidth=1.5, label="ATR")
            ax_atr.fill_between(ti[-disp_len:], 0, atr_hist[-disp_len:], alpha=0.3, color="purple")
            ax_atr.axhline(y=atr_val, color="yellow", linewidth=1, linestyle="--", alpha=0.7, label=f"当前={atr_val:.2f}")
            ax_atr.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
            ax_atr.set_ylabel("ATR", color=self.C["tx"])
            ax_atr.tick_params(axis='y', labelcolor=self.C["tx"])
            ax_atr.tick_params(axis='x', labelcolor=self.C['tx'])
            ax_atr.set_title("ATR 平均真实波幅", color=self.C["tx"], fontsize=9)
        
        # 绘制MACD
        macd_hist = a.get("macd_hist", [])
        if macd_hist and len(macd_hist) > 0:
            macd_line = macd_hist[-n:] if len(macd_hist) >= n else macd_hist
            # 调整ti长度以匹配macd_line
            ti = np.arange(len(macd_line))
            # 计算信号线
            signal_line = []
            for i in range(len(macd_line)):
                if i < 8:
                    signal_line.append(sum(macd_line[:i+1])/(i+1))
                else:
                    signal_line.append(0.2*macd_line[i] + 0.8*signal_line[-1])
            
            # MACD柱状图
            colors = [self.C["green"] if v >= 0 else self.C["red"] for v in macd_line]
            ax_macd.bar(ti, macd_line, color=colors, alpha=0.6, width=0.6)
            ax_macd.plot(ti, macd_line, "cyan", linewidth=1, label="MACD")
            ax_macd.plot(ti, signal_line, "orange", linewidth=1, label="Signal")
            ax_macd.axhline(y=0, color=self.C["bd"], linewidth=0.5)
            ax_macd.legend(loc="upper left", facecolor=self.C["card"], edgecolor=self.C["bd"], labelcolor=self.C["tx"])
            ax_macd.set_ylabel("MACD", color=self.C["tx"])
            ax_macd.tick_params(axis='y', labelcolor=self.C["tx"])
            ax_macd.tick_params(axis='x', labelcolor=self.C['tx'])
            ax_macd.set_title("MACD 指数平滑异同", color=self.C["tx"], fontsize=9)
            ax_macd.set_ylim(min(macd_line)*1.2 if macd_line else -1, max(macd_line)*1.2 if macd_line else 1)
        
        # 手动调整子图间距，避免tight_layout警告
        self.fig.subplots_adjust(hspace=1)
        self.canvas.draw()
    def _update_countdown(self):
        """更新周期倒计时 - 每秒刷新，显示在窗口标题"""
        try:
            now = datetime.now()
            m1_tf = getattr(self, "chart_tv_m1", None)
            h1_tf = getattr(self, "chart_tv_h1", None)
            m1_tf = m1_tf.get() if m1_tf else "M1"
            h1_tf = h1_tf.get() if h1_tf else "H1"
            period_secs = {"M1": 60, "M5": 300, "M6": 360, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
            m1_secs = period_secs.get(m1_tf, 60)
            h1_secs = period_secs.get(h1_tf, 3600)
            epoch = now.timestamp()
            m1_rem = int(m1_secs - (epoch % m1_secs))
            h1_rem = int(h1_secs - (epoch % h1_secs))
            m1_str = f"{m1_rem//60:02d}:{m1_rem%60:02d}"
            h1_str = f"{h1_rem//60:02d}:{h1_rem%60:02d}"
            self.countdown_var.set(m1_str)
            self.root.title(f"HJ ANALYZER v3.071 - M1:{m1_str} H1:{h1_str}")
        except Exception as e:
            _dbg(f"_update_countdown error: {e}")



def _kill_old_instances():
    """启动前清理旧进程，避免缓存问题 - 使用taskkill强制关闭"""
    import subprocess, time, os
    try:
        # 方法1: 使用taskkill关闭所有python.exe进程（除了当前进程）
        current_pid = os.getpid()
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'], 
                              capture_output=True, text=True, timeout=5)
        pids_to_kill = []
        for line in result.stdout.split('\n'):
            if 'python.exe' in line.lower():
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1].strip('"'))
                        if pid != current_pid:
                            pids_to_kill.append(pid)
                    except: pass
        # 强制关闭所有找到的进程
        for pid in pids_to_kill:
            try:
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                             capture_output=True, timeout=3)
            except: pass
        time.sleep(2)  # 等待进程完全退出
        # 方法2: 再次检查，确保没有残留
        result2 = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'], 
                                capture_output=True, text=True, timeout=5)
        for line in result2.stdout.split('\n'):
            if 'python.exe' in line.lower():
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1].strip('"'))
                        if pid != os.getpid():
                            subprocess.run(['taskkill', '/F', '/IM', 'python.exe'], 
                                         capture_output=True, timeout=5)
                            break
                    except: pass
    except: pass

if __name__ == "__main__":
    _kill_old_instances()
    root = tk.Tk()
    app = GoldAnalyzerApp(root)
    root.protocol("WM_DELETE_WINDOW", app._close)
    root.mainloop()








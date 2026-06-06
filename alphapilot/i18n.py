"""中文文案集中管理。

为方便未来 i18n 化或文案统一调整，把所有用户可见的中文消息集中在此。
策略/回测模块应使用这些常量而非硬编码字符串。
"""

# ----- 策略消息 --------------------------------------------------------

MSG_INSUFFICIENT_DATA = "历史数据不足，至少需要 65 个交易日"
MSG_LOSS_STREAK_COOLDOWN = "连续亏损达到阈值，进入暂停观察期"
MSG_MARKET_BELOW_MA20 = "大盘在 MA20 下方，不开新仓"
MSG_SECTOR_NOT_RESONANT = "板块未共振，不生成新仓信号"
MSG_POSITION_EXIT_TRIGGERED = "持仓触发退出提醒"
MSG_MARKET_SECTOR_STOCK_RESONANT = "大盘、板块、个股共振"
MSG_FILTERS_RELAXED = "已按配置放宽大盘或板块过滤"
MSG_LEADER_STRONGER_THAN_SECTOR = "龙头强于板块"
MSG_STOCK_LOW_POSITION_CROSS_MA20 = "个股低位站上 MA20"
MSG_STOCK_JUST_CROSSED_MA20 = "个股刚站上 MA20，适合小仓观察"
MSG_STOCK_NOT_CROSSED_OR_RESONANT = "个股未满足低位站上或共振条件"
# 放宽后的入场理由
MSG_STOCK_TREND_FOLLOWING = "个股已站上 MA20 持续运行，趋势确认入场"
MSG_STOCK_SECTOR_RELAXED_TRIAL = "板块弱但个股强势突破，小仓试探"

# ----- 回测退出原因 ----------------------------------------------------

EXIT_REASON_TAKE_PROFIT = "盈利提醒"
EXIT_REASON_STOP_LOSS = "风险阈值触发"
EXIT_REASON_BELOW_MA20 = "跌破 MA20"
EXIT_REASON_MAX_HOLD = "达到最长持仓周期"

# ----- 数据状态消息 ----------------------------------------------------

CACHE_MSG_OK = "本地行情缓存可用。"
CACHE_MSG_EMPTY = "尚未初始化行情缓存。"
CACHE_MSG_FAILED = "最近一次行情抓取全部失败，系统保留并使用此前可用的本地缓存。"
CACHE_MSG_PARTIAL = "最近一次行情抓取部分失败，失败标的已继续使用本地缓存兜底。"
CACHE_MSG_UPDATING = "行情数据正在更新。"

# ----- HTTP 错误消息 ----------------------------------------------------

HTTP_ERR_BAD_PARAMS = "参数错误: {exc}"
HTTP_ERR_SERVICE_UNAVAILABLE = "服务暂不可用: {exc}"
HTTP_ERR_INTERNAL = "内部错误"

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "alphapilot.sqlite"

# HTTP 服务配置
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765

# 信号分页限制
DEFAULT_SIGNAL_LIMIT = 100
MAX_SIGNAL_LIMIT = 1000
SIGNAL_PAGE_SIZE = 20


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    asset_type: str = "stock"
    sector: str = "机器人"


BENCHMARKS = {
    "market": Instrument("000001.SH", "上证指数", "index", "市场"),
    "style": Instrument("399006.SZ", "创业板指", "index", "风格"),
    "sector": Instrument("159770.SZ", "机器人 ETF", "etf", "机器人"),
}


# 三个槽位各自的可选基准（用户可在 Web UI 切换）
# 28 个候选：6 宽基（市场维度）+ 风格/海外 + 19 行业主题 ETF
BENCHMARK_CANDIDATES = {
    "market": [
        # 宽基 / 综合
        Instrument("000001.SH", "上证指数", "index", "市场"),
        Instrument("399001.SZ", "深证成指", "index", "市场"),
        Instrument("000300.SH", "沪深 300", "index", "市场"),
        Instrument("000905.SH", "中证 500", "index", "市场"),
        Instrument("000852.SH", "中证 1000", "index", "市场"),
        Instrument("000016.SH", "上证 50", "index", "市场"),
    ],
    "style": [
        # 风格 / 板块指数
        Instrument("399006.SZ", "创业板指", "index", "风格"),
        Instrument("000688.SH", "科创 50", "index", "风格"),
    ],
    "sector": [
        # 行业 / 主题 ETF
        Instrument("159770.SZ", "机器人 ETF", "etf", "机器人"),
        Instrument("159995.SZ", "半导体 ETF", "etf", "半导体"),
        Instrument("159819.SZ", "人工智能 ETF", "etf", "AI"),
        Instrument("159779.SZ", "消费电子 ETF", "etf", "消费电子"),
        Instrument("515030.SH", "新能源车 ETF", "etf", "新能源车"),
        Instrument("159840.SZ", "锂电池 ETF", "etf", "锂电池"),
        Instrument("515790.SH", "光伏 ETF", "etf", "光伏"),
        Instrument("159885.SZ", "储能 ETF", "etf", "储能"),
        Instrument("159992.SZ", "创新药 ETF", "etf", "医药"),
        Instrument("512170.SH", "医疗 ETF", "etf", "医疗"),
        Instrument("159883.SZ", "医疗器械 ETF", "etf", "医疗器械"),
        Instrument("512800.SH", "银行 ETF", "etf", "银行"),
        Instrument("512880.SH", "证券 ETF", "etf", "证券"),
        Instrument("512200.SH", "房地产 ETF", "etf", "房地产"),
        Instrument("512660.SH", "军工 ETF", "etf", "军工"),
        Instrument("515220.SH", "煤炭 ETF", "etf", "煤炭"),
        Instrument("512400.SH", "有色金属 ETF", "etf", "有色"),
        Instrument("515050.SH", "通信 ETF", "etf", "通信"),
        # 159987.SZ 软件 ETF 已下架/数据源不可达，从候选移除
        Instrument("512690.SH", "酒 ETF", "etf", "酒"),
        Instrument("159996.SZ", "家电 ETF", "etf", "家电"),
        Instrument("159825.SZ", "农业 ETF", "etf", "农业"),
    ],
}


WATCHLIST = [
    Instrument("300124.SZ", "汇川技术", sector="机器人"),
    Instrument("002230.SZ", "科大讯飞", sector="AI"),
    Instrument("601138.SH", "工业富联", sector="算力"),
    Instrument("688256.SH", "寒武纪", sector="AI"),
    Instrument("002415.SZ", "海康威视", sector="机器人"),
    Instrument("002475.SZ", "立讯精密", sector="消费电子"),
    Instrument("300014.SZ", "亿纬锂能", sector="新能源"),
]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_instrument(symbol: str) -> Instrument:
    # 优先查默认激活基准（兼容老调用），再查所有候选（覆盖用户切到非默认基准的情况）
    for item in [*BENCHMARKS.values(), *WATCHLIST]:
        if item.symbol == symbol:
            return item
    for slot_candidates in BENCHMARK_CANDIDATES.values():
        for item in slot_candidates:
            if item.symbol == symbol:
                return item
    return Instrument(symbol=symbol, name=symbol)

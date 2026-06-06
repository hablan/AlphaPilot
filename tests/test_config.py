from __future__ import annotations

import unittest
from pathlib import Path

from alphapilot import config


class TestConfigConstants(unittest.TestCase):
    def test_data_paths_are_path_objects(self) -> None:
        self.assertIsInstance(config.ROOT_DIR, Path)
        self.assertIsInstance(config.DATA_DIR, Path)
        self.assertIsInstance(config.DEFAULT_DB_PATH, Path)

    def test_default_db_path_is_inside_data_dir(self) -> None:
        self.assertTrue(str(config.DEFAULT_DB_PATH).startswith(str(config.DATA_DIR)))

    def test_server_constants(self) -> None:
        self.assertEqual(config.SERVER_HOST, "127.0.0.1")
        self.assertIsInstance(config.SERVER_PORT, int)
        self.assertGreater(config.SERVER_PORT, 0)
        self.assertLess(config.SERVER_PORT, 65536)

    def test_signal_limit_constants(self) -> None:
        self.assertGreater(config.DEFAULT_SIGNAL_LIMIT, 0)
        self.assertGreaterEqual(config.MAX_SIGNAL_LIMIT, config.DEFAULT_SIGNAL_LIMIT)
        self.assertGreater(config.SIGNAL_PAGE_SIZE, 0)
        self.assertLessEqual(config.SIGNAL_PAGE_SIZE, config.DEFAULT_SIGNAL_LIMIT)


class TestInstrument(unittest.TestCase):
    def test_default_instrument(self) -> None:
        inst = config.Instrument(symbol="000001.SZ", name="平安银行")
        self.assertEqual(inst.symbol, "000001.SZ")
        self.assertEqual(inst.name, "平安银行")
        self.assertEqual(inst.asset_type, "stock")
        self.assertEqual(inst.sector, "机器人")

    def test_custom_instrument(self) -> None:
        inst = config.Instrument("600000.SH", "浦发银行", "stock", "银行")
        self.assertEqual(inst.asset_type, "stock")
        self.assertEqual(inst.sector, "银行")

    def test_instrument_is_hashable_and_frozen(self) -> None:
        inst = config.Instrument("000001.SZ", "测试")
        with self.assertRaises(Exception):  # FrozenInstanceError
            inst.symbol = "000002.SZ"  # type: ignore[misc]
        # hashable
        self.assertEqual(len({inst, inst}), 1)


class TestBenchmarksAndWatchlist(unittest.TestCase):
    def test_benchmarks_contains_three_indices(self) -> None:
        self.assertEqual(len(config.BENCHMARKS), 3)
        for key, inst in config.BENCHMARKS.items():
            self.assertTrue(key)
            self.assertTrue(inst.symbol)
            self.assertTrue(inst.name)

    def test_watchlist_is_non_empty(self) -> None:
        self.assertGreater(len(config.WATCHLIST), 0)
        for inst in config.WATCHLIST:
            self.assertTrue(inst.symbol)
            self.assertTrue(inst.name)
            self.assertNotEqual(inst.sector, "")

    def test_watchlist_symbols_are_unique(self) -> None:
        symbols = [inst.symbol for inst in config.WATCHLIST]
        self.assertEqual(len(symbols), len(set(symbols)))


class TestGetInstrument(unittest.TestCase):
    def test_lookup_existing_watchlist_symbol(self) -> None:
        first = config.WATCHLIST[0]
        result = config.get_instrument(first.symbol)
        self.assertEqual(result.symbol, first.symbol)
        self.assertEqual(result.name, first.name)

    def test_lookup_existing_benchmark_symbol(self) -> None:
        first_key = next(iter(config.BENCHMARKS))
        first = config.BENCHMARKS[first_key]
        result = config.get_instrument(first.symbol)
        self.assertEqual(result.symbol, first.symbol)
        self.assertEqual(result.name, first.name)

    def test_lookup_missing_symbol_returns_fallback(self) -> None:
        result = config.get_instrument("999999.SZ")
        self.assertEqual(result.symbol, "999999.SZ")
        self.assertEqual(result.name, "999999.SZ")
        self.assertEqual(result.asset_type, "stock")


class TestEnsureDataDir(unittest.TestCase):
    def test_ensure_data_dir_creates_directory(self) -> None:
        # 确保调用不会抛错（目录已存在的情况）
        config.ensure_data_dir()
        self.assertTrue(config.DATA_DIR.exists())


if __name__ == "__main__":
    unittest.main()

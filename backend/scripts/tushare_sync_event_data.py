"""CLI：同步 Tushare 事件类 / 筹码类 / 融资融券数据。

核心逻辑在 services/tushare_event_sync.py；本脚本仅做命令行解析与结果打印。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tushare_event_sync import sync_tushare_event_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="事件类数据：最近 N 天（默认10）")
    parser.add_argument("--start-date", type=str, default=None, help="开始日期 YYYYMMDD")
    parser.add_argument("--end-date", type=str, default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--full-backfill", action="store_true", help="全历史回填（事件类从 2020-01-01）")
    parser.add_argument("--stock-ids", type=int, nargs="+", default=None, help="仅同步指定 stock_id")
    parser.add_argument("--skip-pledge", action="store_true")
    parser.add_argument("--skip-pledge-stat", action="store_true")
    parser.add_argument("--skip-share-float", action="store_true")
    parser.add_argument("--skip-repurchase", action="store_true")
    parser.add_argument("--skip-holder-trade", action="store_true")
    parser.add_argument("--skip-holdernumber", action="store_true")
    parser.add_argument("--skip-cyq-perf", action="store_true")
    parser.add_argument("--skip-margin", action="store_true")
    parser.add_argument(
        "--run-per-stock", action="store_true",
        help="执行逐股接口（holdernumber/cyq/pledge_stat），较慢，默认跳过"
    )
    parser.add_argument("--broker-recommend", action="store_true", help="同步券商金股（需 6000 积分）")
    parser.add_argument("--broker-month", type=str, default=None, help="券商金股月份 YYYYMM，默认上月")
    args = parser.parse_args()

    result = sync_tushare_event_data(
        stock_ids=args.stock_ids,
        days=args.days,
        start_date=args.start_date,
        end_date=args.end_date,
        full_backfill=args.full_backfill,
        skip_pledge=args.skip_pledge,
        skip_pledge_stat=args.skip_pledge_stat,
        skip_share_float=args.skip_share_float,
        skip_repurchase=args.skip_repurchase,
        skip_holder_trade=args.skip_holder_trade,
        skip_holdernumber=args.skip_holdernumber,
        skip_cyq_perf=args.skip_cyq_perf,
        skip_margin=args.skip_margin,
        skip_per_stock=not args.run_per_stock,
        broker_recommend=args.broker_recommend,
        broker_month=args.broker_month,
    )
    print(result)


if __name__ == "__main__":
    main()

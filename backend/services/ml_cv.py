"""Purged walk-forward 窗口与样本掩码（时序 ML 验证）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class WalkForwardWindow:
    """一组 WF 折：训练截止日、OOS 预测日（截面）、标签实现截止日索引。"""

    fold: int
    train_start_idx: int
    train_end_idx: int
    test_feature_idx: int
    label_end_idx: int
    train_start: str
    train_end: str
    test_date: str
    label_end_date: str


def date_index(dates: list[str], d: str) -> int:
    try:
        return dates.index(d)
    except ValueError:
        return -1


def label_end_index(feature_idx: int, horizon: int) -> int:
    return feature_idx + horizon


def sample_label_end_overlaps_test(
    feature_idx: int,
    horizon: int,
    test_feature_idx: int,
    label_end_idx: int | None = None,
) -> bool:
    """训练样本标签区间 [feature_idx, label_end] 是否与测试预测日 test_feature_idx 重叠。"""
    end = label_end_idx if label_end_idx is not None else label_end_index(feature_idx, horizon)
    return feature_idx <= test_feature_idx <= end


def purge_train_mask(
    feature_indices: list[int],
    horizon: int,
    test_feature_idx: int,
    *,
    embargo_days: int = 5,
) -> list[bool]:
    """
    保留可用于训练的样本（True=保留）。
    Purge: 标签覆盖 test 日的样本剔除。
    Embargo: feature_idx >= test_feature_idx - embargo_days 的样本剔除。
    """
    keep: list[bool] = []
    embargo_cut = test_feature_idx - embargo_days
    for fi in feature_indices:
        if sample_label_end_overlaps_test(fi, horizon, test_feature_idx):
            keep.append(False)
            continue
        if fi >= embargo_cut:
            keep.append(False)
            continue
        keep.append(True)
    return keep


def iter_walkforward_windows(
    dates: list[str],
    *,
    train_window_days: int,
    step_days: int,
    horizon: int,
    embargo_days: int = 5,
    min_folds: int = 1,
) -> Iterator[WalkForwardWindow]:
    """
    滚动 WF：每折在 test_feature_idx 做截面预测，标签在 label_end_idx 实现。
    训练集特征日范围为 [train_start_idx, train_end_idx]（后续由 purge 再筛样本）。
    """
    need = train_window_days + horizon + embargo_days + step_days + 5
    if len(dates) < need:
        return

    fold = 0
    i = 0
    while True:
        train_end_idx = i + train_window_days - 1
        test_feature_idx = train_end_idx + 1 + embargo_days
        label_end_idx = test_feature_idx + horizon
        if label_end_idx >= len(dates):
            break
        yield WalkForwardWindow(
            fold=fold,
            train_start_idx=i,
            train_end_idx=train_end_idx,
            test_feature_idx=test_feature_idx,
            label_end_idx=label_end_idx,
            train_start=dates[i],
            train_end=dates[train_end_idx],
            test_date=dates[test_feature_idx],
            label_end_date=dates[label_end_idx],
        )
        fold += 1
        i += step_days
        if fold < min_folds and i + train_window_days >= len(dates):
            break

    if fold < min_folds:
        return


def split_valid_purged(
    feature_indices: list[int],
    horizon: int,
    valid_start_feature_idx: int,
    *,
    embargo_days: int = 5,
) -> tuple[list[int], list[int]]:
    """
    将训练样本索引拆为 fit / valid；valid 从 valid_start_feature_idx 起。
    对 fit 部分施加 purge（以 valid 首日为 test）。
    """
    fit_idx: list[int] = []
    valid_idx: list[int] = []
    for fi in feature_indices:
        if fi >= valid_start_feature_idx:
            valid_idx.append(fi)
        else:
            fit_idx.append(fi)
    if not valid_idx:
        split = int(len(feature_indices) * 0.85)
        fit_idx = feature_indices[:split]
        valid_idx = feature_indices[split:]
        valid_start_feature_idx = valid_idx[0] if valid_idx else feature_indices[-1]

    mask = purge_train_mask(fit_idx, horizon, valid_start_feature_idx, embargo_days=embargo_days)
    fit_idx = [fi for fi, ok in zip(fit_idx, mask) if ok]
    return fit_idx, valid_idx

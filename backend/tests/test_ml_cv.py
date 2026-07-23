"""ml_cv purge / walk-forward 窗口单元测试。"""
from services.ml_cv import purge_train_mask, sample_label_end_overlaps_test


def test_label_overlap_detects_h20():
    assert sample_label_end_overlaps_test(100, 20, 110) is True
    assert sample_label_end_overlaps_test(100, 20, 121) is False
    assert sample_label_end_overlaps_test(100, 20, 80) is False


def test_purge_removes_overlap_and_embargo():
    indices = list(range(90, 121))
    test_idx = 115
    mask = purge_train_mask(indices, 20, test_idx, embargo_days=5)
    kept = [i for i, ok in zip(indices, mask) if ok]
    assert 115 not in kept
    assert 114 not in kept
    assert all(i <= 110 - 20 for i in kept) or all(
        not sample_label_end_overlaps_test(i, 20, test_idx) for i in kept
    )

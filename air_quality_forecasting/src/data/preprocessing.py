"""Timestamp holdout and expanding validation with purged future labels."""
import numpy as np
import pandas as pd
from src.features.engineering import feature_names


def prepare_splits(frame, cfg):
    horizons = cfg['forecast']['horizons']
    names = feature_names(cfg)
    targets = [f'target_{h}h' for h in horizons]
    clean = frame.dropna(subset=names + targets).sort_values(['timestamp', 'city']).copy()
    if not np.isfinite(clean[names + targets].to_numpy()).all():
        raise ValueError('Non-finite feature/target values')
    times = pd.DatetimeIndex(clean.timestamp.unique()).sort_values()
    ratios = cfg['data']
    i_val = int(len(times) * ratios['train_ratio'])
    i_test = int(len(times) * (ratios['train_ratio'] + ratios['val_ratio']))
    count = cfg['validation']['n_splits']
    if i_val < 2 or i_test <= i_val or len(times) - i_test < 2 or i_test - i_val < count:
        raise ValueError('Not enough complete hourly origins for train/validation/test')
    val_start, test_start = times[i_val], times[i_test]
    delta = pd.Timedelta(hours=max(horizons))
    label_end = clean.timestamp + delta
    dev = clean.loc[label_end < test_start].copy()
    test = clean.loc[clean.timestamp >= test_start].copy()
    edges = np.linspace(i_val, i_test, count + 1, dtype=int)
    folds = []
    for start, end in zip(edges[:-1], edges[1:]):
        left, right = times[start], times[end]
        train = clean.loc[label_end < left].copy()
        validation = clean.loc[(clean.timestamp >= left) & (label_end < right)].copy()
        if len(train) < 2 or len(validation) < 2:
            raise ValueError('Validation window too short after horizon purge')
        folds.append((train, validation))
    if len(dev) < 2 or len(test) < 2:
        raise ValueError('Empty development or test split')
    metadata = {
        'validation_start': str(val_start), 'test_start': str(test_start),
        'max_horizon_purge_hours': max(horizons), 'eligible_rows': len(clean),
        'dropped_rows': len(frame) - len(clean), 'development_rows': len(dev), 'test_rows': len(test),
        'test_origin_end': str(test.timestamp.max()),
        'folds': [{'train_rows': len(tr), 'val_rows': len(va),
                   'last_train_target': str((tr.timestamp + delta).max()),
                   'first_val_origin': str(va.timestamp.min()),
                   'last_val_target': str((va.timestamp + delta).max())} for tr, va in folds],
    }
    return dev, test, folds, metadata

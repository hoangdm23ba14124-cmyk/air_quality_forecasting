"""Metrics on aligned future observations, per horizon and per city.
The binary threshold is an experimental hourly cutoff, NOT an EPA AQI.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix


def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((np.asarray(y_pred) - np.asarray(y_true)) ** 2)))


def metric_row(frame, threshold):
    yt, yp, base = [frame[c].to_numpy(dtype=float) for c in ('y_true', 'y_pred', 'y_baseline')]
    if not len(yt) or not np.isfinite(np.column_stack([yt, yp, base])).all():
        raise ValueError('Metrics require aligned, finite observations')
    mse = float(np.mean((yt - yp) ** 2))
    baseline_rmse = rmse(yt, base)
    total = float(np.sum((yt - yt.mean()) ** 2))
    tn, fp, fn, tp = confusion_matrix(yt >= threshold, yp >= threshold, labels=[False, True]).ravel()
    return {
        'n': len(yt), 'rmse': np.sqrt(mse), 'mae': float(np.mean(np.abs(yp - yt))),
        'r2': 1 - float(np.sum((yt - yp) ** 2)) / total if total > 0 else np.nan,
        'baseline_rmse': baseline_rmse,
        'improvement_pct': 100 * (1 - np.sqrt(mse) / baseline_rmse) if baseline_rmse > 0 else np.nan,
        'threshold': threshold, 'n_above_threshold': int(tp + fn),
        'precision_high': tp / (tp + fp) if tp + fp else 0.,
        'recall_high': tp / (tp + fn) if tp + fn else np.nan,
        'f1_high': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.,
        'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp),
        'negative_predictions': int((yp < 0).sum()),
    }


def summarize(predictions, threshold):
    overall, by_city = [], []
    for (family, horizon), group in predictions.groupby(['model', 'horizon_hours'], sort=True):
        overall.append({'model': family, 'horizon_hours': horizon, **metric_row(group, threshold)})
        for city, city_group in group.groupby('city', sort=True):
            by_city.append({'model': family, 'horizon_hours': horizon, 'city': city,
                            **metric_row(city_group, threshold)})
    return pd.DataFrame(overall), pd.DataFrame(by_city)

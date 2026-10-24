"""Comparison charts and separate per-city, per-horizon forecast traces."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


def generate_all_figures(predictions, metrics, selected, save_dir):
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    table = metrics.pivot(index='horizon_hours', columns='model', values='rmse')
    ax = table.plot.bar(figsize=(10, 5), rot=0)
    ax.set(xlabel='Forecast horizon (hours)', ylabel='RMSE (µg/m³)',
           title='Held-out test RMSE — same origins for every model')
    ax.figure.tight_layout()
    ax.figure.savefig(save_dir / 'rmse_by_horizon.png', dpi=150)
    plt.close(ax.figure)
    for h, family in selected.items():
        group = predictions[(predictions.horizon_hours == int(h)) & (predictions.model == family)]
        for city, rows in group.groupby('city'):
            rows = rows.sort_values('target_time')
            end = rows.target_time.max()
            rows = rows[rows.target_time > end - pd.Timedelta(days=7)]
            fig, ax = plt.subplots(figsize=(12, 4))
            ax.plot(rows.target_time, rows.y_true, label='Observed', lw=1.5)
            label = {'simple_linear': 'Simple Linear Prediction',
                     'multiple_linear': 'Multiple Linear Prediction',
                     'polynomial': 'Polynomial Prediction'}[family]
            ax.plot(rows.target_time, rows.y_pred, label=label, lw=1.2)
            ax.set(title=f'{city} | +{h}h | last 7 days (rolling forecast origins)',
                   xlabel='Target time (UTC+7)', ylabel='PM2.5 (µg/m³)')
            ax.legend()
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig(save_dir / f'{city}_{h}h.png', dpi=150)
            plt.close(fig)

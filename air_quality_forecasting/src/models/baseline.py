"""Persistence: every direct horizon predicts the origin-hour PM2.5."""
import numpy as np


class PersistenceBaseline:
    def predict(self, current_pm25):
        values = np.asarray(current_pm25, dtype=float)
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError('Baseline requires finite origin-hour PM2.5 values')
        return values.copy()

"""Direct forecasting models and validation-only model selection."""
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from src.features.engineering import feature_names
from src.models.baseline import PersistenceBaseline
from src.models.ridge_regression import RidgeRegression

FAMILIES = ['persistence', 'simple_linear', 'multiple_linear', 'polynomial', 'ridge']


class ForecastModel:
    """One family/horizon, including feature order and train-fitted transforms."""
    def __init__(self, family, horizon, cfg, parameter=None):
        if family not in FAMILIES:
            raise ValueError(f'Unknown model family: {family}')
        self.family, self.horizon, self.parameter = family, horizon, parameter
        self.feature_names = ['pm25_current'] if family in ('persistence', 'simple_linear') else feature_names(cfg)
        self.poly_columns = cfg['model']['polynomial_features'] if family == 'polynomial' else []
        if len(set(self.poly_columns)) != len(self.poly_columns) or not set(self.poly_columns) <= set(self.feature_names):
            raise ValueError('Polynomial columns must be unique, allowed features')
        self.other_columns = [c for c in self.feature_names if c not in self.poly_columns]
        self.scaler = StandardScaler()
        self.polynomial = PolynomialFeatures(degree=int(parameter), include_bias=False) if family == 'polynomial' else None
        self.estimator = None
        self.training_end = None

    def _design(self, frame, fit=False):
        if self.polynomial is None:
            return frame[self.feature_names].to_numpy(dtype=float)
        block = frame[self.poly_columns].to_numpy(dtype=float)
        expanded = self.polynomial.fit_transform(block) if fit else self.polynomial.transform(block)
        return np.column_stack([expanded, frame[self.other_columns].to_numpy(dtype=float)])

    def fit(self, frame):
        y = frame[f'target_{self.horizon}h'].to_numpy(dtype=float)
        self.training_end = frame.timestamp.max() + pd.Timedelta(hours=self.horizon)
        if self.family == 'persistence':
            self.estimator = PersistenceBaseline()
            return self
        X = self.scaler.fit_transform(self._design(frame, fit=True))
        if self.family == 'ridge':
            self.estimator = RidgeRegression(alpha=float(self.parameter)).fit_closed_form(X, y)
        else:
            self.estimator = LinearRegression().fit(X, y)
        return self

    def predict(self, frame):
        if self.estimator is None:
            raise RuntimeError('Model is not fitted')
        X = frame[self.feature_names].to_numpy(dtype=float)
        if not np.isfinite(X).all():
            raise ValueError('Incomplete/non-finite history at forecast origin')
        if self.family == 'persistence':
            return self.estimator.predict(frame.pm25_current.to_numpy())
        return self.estimator.predict(self.scaler.transform(self._design(frame)))

    def coefficient_table(self):
        if self.family == 'persistence':
            return None
        names = self.feature_names
        if self.polynomial is not None:
            names = list(self.polynomial.get_feature_names_out(self.poly_columns)) + self.other_columns
        w = self.estimator.w_ if self.family == 'ridge' else self.estimator.coef_
        return pd.DataFrame({'feature': names, 'coefficient_on_scaled_feature': w})


def select_models(folds, horizon, cfg):
    """Pooled validation RMSE, with preprocessing refit inside every fold."""
    records, choices, family_scores = [], {}, {}
    for family in FAMILIES:
        if family == 'ridge':
            candidates = cfg['model']['ridge_alphas']
        elif family == 'polynomial':
            candidates = cfg['model']['polynomial_degrees']
        else:
            candidates = [None]
        best_score = np.inf
        for parameter in candidates:
            sse, count = 0., 0
            for fold, (train, validation) in enumerate(folds, 1):
                model = ForecastModel(family, horizon, cfg, parameter).fit(train)
                residuals = model.predict(validation) - validation[f'target_{horizon}h'].to_numpy()
                error = float(residuals @ residuals)
                sse += error
                count += len(residuals)
                records.append({'horizon_hours': horizon, 'model': family,
                                'parameter': parameter, 'fold': fold, 'n': len(residuals),
                                'validation_rmse': float(np.sqrt(error / len(residuals)))})
            score = float(np.sqrt(sse / count))
            records.append({'horizon_hours': horizon, 'model': family,
                            'parameter': parameter, 'fold': 'pooled', 'n': count,
                            'validation_rmse': score})
            if score < best_score:
                best_score, choices[family] = score, parameter
        family_scores[family] = best_score
    winner = min(family_scores, key=family_scores.get)
    return choices, family_scores, winner, records

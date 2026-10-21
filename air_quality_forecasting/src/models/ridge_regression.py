"""NumPy Ridge: mean squared error + alpha * sum(w**2), unpenalized bias.

Closed form is used in experiments for reproducible convergence. Mini-batch
GD remains available for teaching and is checked against the same objective.
For sklearn's sum-of-squares convention, alpha_sklearn = n * alpha_here.
"""
import pickle
from pathlib import Path
import numpy as np


class RidgeRegression:
    def __init__(self, alpha=1.0, learning_rate=0.01, max_epochs=2000,
                 batch_size=512, patience=30, tol=1e-6, random_state=42):
        if not np.isfinite(alpha) or alpha < 0:
            raise ValueError('alpha must be finite and nonnegative')
        if not np.isfinite(learning_rate) or learning_rate <= 0 or not np.isfinite(tol) or tol < 0:
            raise ValueError('Invalid learning_rate or tol')
        if any(type(v) is not int or v < 1 for v in [max_epochs, batch_size, patience]):
            raise ValueError('Epochs, batch size and patience must be positive integers')
        self.alpha, self.learning_rate = alpha, learning_rate
        self.max_epochs, self.batch_size, self.patience = max_epochs, batch_size, patience
        self.tol, self.random_state = tol, random_state
        self.w_, self.b_ = None, 0.0
        self.train_losses_, self.val_losses_, self.objectives_ = [], [], []
        self.best_epoch_ = 0

    @staticmethod
    def _validate(X, y):
        X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        if X.ndim != 2 or y.ndim != 1 or len(X) != len(y) or len(y) < 2 or X.shape[1] == 0:
            raise ValueError('Expected nonempty X[n,p] and y[n] with at least two rows')
        if not np.isfinite(X).all() or not np.isfinite(y).all():
            raise ValueError('Training data must be finite')
        return X, y

    def _predict_raw(self, X):
        return X @ self.w_ + self.b_

    def _mse_loss(self, residuals):
        return float(np.mean(residuals ** 2))

    def _ridge_loss(self, residuals):
        return self._mse_loss(residuals) + self.alpha * float(self.w_ @ self.w_)

    def _gradients(self, X, residuals):
        return 2 * X.T @ residuals / len(residuals) + 2 * self.alpha * self.w_, 2 * residuals.mean()

    def fit_closed_form(self, X, y):
        X, y = self._validate(X, y)
        self.train_losses_, self.val_losses_, self.objectives_ = [], [], []
        self.best_epoch_ = 0
        xm, ym = X.mean(axis=0), y.mean()
        xc, yc = X - xm, y - ym
        if self.alpha == 0:
            self.w_ = np.linalg.lstsq(xc, yc, rcond=None)[0]
        else:
            self.w_ = np.linalg.solve(xc.T @ xc + len(y) * self.alpha * np.eye(X.shape[1]), xc.T @ yc)
        self.b_ = float(ym - xm @ self.w_)
        return self

    def fit(self, X_train, y_train, X_val, y_val):
        X_train, y_train = self._validate(X_train, y_train)
        X_val, y_val = self._validate(X_val, y_val)
        if X_train.shape[1] != X_val.shape[1]:
            raise ValueError('Train/validation feature counts differ')
        rng = np.random.default_rng(self.random_state)
        n, p = X_train.shape
        self.w_, self.b_ = rng.normal(0, .01, p), 0.0
        self.train_losses_, self.val_losses_, self.objectives_ = [], [], []
        self.best_epoch_ = 0
        best_loss, no_improve = np.inf, 0
        best_w, best_b = self.w_.copy(), self.b_
        for epoch in range(self.max_epochs):
            indices = rng.permutation(n)
            for start in range(0, n, self.batch_size):
                ix = indices[start:start + self.batch_size]
                residuals = self._predict_raw(X_train[ix]) - y_train[ix]
                gw, gb = self._gradients(X_train[ix], residuals)
                self.w_ -= self.learning_rate * gw
                self.b_ -= self.learning_rate * gb
            train_res = self._predict_raw(X_train) - y_train
            val_loss = self._mse_loss(self._predict_raw(X_val) - y_val)
            if not np.isfinite(val_loss) or not np.isfinite(train_res).all():
                raise FloatingPointError('Gradient descent diverged; reduce learning_rate')
            self.train_losses_.append(self._mse_loss(train_res))
            self.val_losses_.append(val_loss)
            self.objectives_.append(self._ridge_loss(train_res))
            if val_loss < best_loss - self.tol:
                best_loss, best_w, best_b = val_loss, self.w_.copy(), self.b_
                self.best_epoch_, no_improve = epoch, 0
            else:
                no_improve += 1
            if no_improve >= self.patience:
                break
        self.w_, self.b_ = best_w, best_b
        return self

    def predict(self, X):
        if self.w_ is None:
            raise RuntimeError('Model has not been fitted')
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or X.shape[1] != len(self.w_) or not np.isfinite(X).all():
            raise ValueError('Invalid prediction features')
        return self._predict_raw(X)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as stream:
            pickle.dump(self, stream)

    @classmethod
    def load(cls, path):
        """Load only artifacts you created/trust; pickle is executable."""
        with open(path, 'rb') as stream:
            return pickle.load(stream)

    @property
    def feature_weights(self):
        if self.w_ is None:
            raise RuntimeError('Model has not been fitted')
        return self.w_.copy()

    def training_history(self):
        return {'train_loss': self.train_losses_, 'val_loss': self.val_losses_,
                'ridge_objective': self.objectives_}

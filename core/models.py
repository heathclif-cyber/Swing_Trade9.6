"""
core/models.py — Stub untuk kompatibilitas pickle calibrator.pkl
Hanya berisi ProbabilityCalibrator (class yang di-pickle saat training).
TradingLSTM didefinisikan di ml/ml_signal.py (tidak perlu di sini).
"""

import pickle
from pathlib import Path


class ProbabilityCalibrator:
    """
    Kalibrasi probabilitas post-hoc untuk output ensemble.
    Class ini harus identik dengan versi di pipeline training
    agar calibrator.pkl bisa di-unpickle tanpa error.
    """

    def __init__(self, method: str = "isotonic"):
        self.method      = method
        self.calibrators = {}  # {class_idx: fitted_estimator}

    def fit(self, proba, y) -> None:
        import numpy as np
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression

        for c in range(proba.shape[1]):
            y_bin = (y == c).astype(int)
            if self.method == "isotonic":
                est = IsotonicRegression(out_of_bounds="clip")
                est.fit(proba[:, c], y_bin)
            else:
                est = LogisticRegression(C=1.0)
                est.fit(proba[:, c].reshape(-1, 1), y_bin)
            self.calibrators[c] = est

    def transform(self, proba):
        import numpy as np

        cal = proba.copy().astype(float)
        for c, est in self.calibrators.items():
            if self.method == "isotonic":
                cal[:, c] = est.predict(proba[:, c])
            else:
                cal[:, c] = est.predict_proba(proba[:, c].reshape(-1, 1))[:, 1]

        row_sum = cal.sum(axis=1, keepdims=True)
        row_sum = np.where(row_sum == 0, 1, row_sum)
        return cal / row_sum

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path) -> "ProbabilityCalibrator":
        with open(Path(path), "rb") as f:
            return pickle.load(f)
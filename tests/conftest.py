"""Fixtures compartidos con datos sintéticos — sin dependencia de la API FMS."""

import numpy as np
import pandas as pd
import pytest


def _make_measurements(n: int = 20, n_routes: int = 3, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    route_ids = rng.integers(1, n_routes + 1, size=n)
    result_ids = np.arange(1, n + 1)
    baseline_ids = rng.integers(1, n + 1, size=n)

    start = pd.Timestamp("2024-01-01", tz="UTC")
    times = [start + pd.Timedelta(days=int(d)) for d in rng.integers(0, 365, size=n)]

    categories = rng.choice(
        ["Monitoring", "Baseline", "VeryFirstReference"], size=n, p=[0.7, 0.2, 0.1]
    )
    fault_status = rng.choice(["Normal", "Detected"], size=n, p=[0.85, 0.15])
    loss_1310 = rng.uniform(0.5, 8.0, size=n)
    loss_1550 = rng.uniform(0.5, 7.5, size=n)
    fms_dev_1310 = rng.uniform(-1.0, 3.0, size=n)
    element_count = rng.integers(0, 15, size=n).astype(float)
    elements_loss_sum = rng.uniform(0.3, 7.0, size=n)

    return pd.DataFrame(
        {
            "resultid": result_ids,
            "route_id": route_ids,
            "TestTime": times,
            "TestCategory": categories,
            "FaultStatus": fault_status,
            "BaselineId": baseline_ids,
            "loss_1310": loss_1310,
            "loss_1550": loss_1550,
            "fms_deviation_db_1310": fms_dev_1310,
            "element_count": element_count,
            "elements_loss_sum_db": elements_loss_sum,
        }
    )


@pytest.fixture
def raw_measurements() -> pd.DataFrame:
    return _make_measurements()


@pytest.fixture
def measurements_with_null_loss() -> pd.DataFrame:
    df = _make_measurements(seed=99)
    df.loc[df.index[:5], ["loss_1310", "loss_1550"]] = np.nan
    return df

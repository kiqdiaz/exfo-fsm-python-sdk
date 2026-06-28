"""Tests para src/features.py — sin dependencia de la API FMS."""

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from features import (
    MODEL_FEATURE_COLUMNS,
    _loss_columns,
    _fms_deviation_columns,
    build_baseline_lookup,
    build_delta_features,
    build_measurement_features,
    build_route_timeline_features,
)


# ---------------------------------------------------------------------------
# _loss_columns
# ---------------------------------------------------------------------------

class TestLossColumns:
    def test_detecta_entero(self):
        df = pd.DataFrame(columns=["loss_1310", "loss_1550", "other"])
        assert _loss_columns(df) == ["loss_1310", "loss_1550"]

    def test_detecta_decimal(self):
        df = pd.DataFrame(columns=["loss_1310.5", "loss_1625"])
        assert set(_loss_columns(df)) == {"loss_1310.5", "loss_1625"}

    def test_no_captura_columnas_derivadas(self):
        df = pd.DataFrame(columns=["loss_1310", "elements_loss_sum_db", "delta_loss_db"])
        assert _loss_columns(df) == ["loss_1310"]

    def test_df_sin_columnas_loss(self):
        df = pd.DataFrame(columns=["route_id", "TestTime"])
        assert _loss_columns(df) == []


# ---------------------------------------------------------------------------
# _fms_deviation_columns
# ---------------------------------------------------------------------------

class TestFmsDeviationColumns:
    def test_detecta_wavelength(self):
        df = pd.DataFrame(columns=["fms_deviation_db_1310", "fms_deviation_db_1550", "other"])
        assert set(_fms_deviation_columns(df)) == {"fms_deviation_db_1310", "fms_deviation_db_1550"}

    def test_df_sin_deviation(self):
        df = pd.DataFrame(columns=["loss_1310"])
        assert _fms_deviation_columns(df) == []


# ---------------------------------------------------------------------------
# build_measurement_features
# ---------------------------------------------------------------------------

class TestBuildMeasurementFeatures:
    def test_columnas_producidas(self, raw_measurements):
        out = build_measurement_features(raw_measurements)
        for col in ("total_loss_db", "avg_loss_db", "is_baseline", "fms_deviation_db",
                    "event_count", "distance_capped"):
            assert col in out.columns, f"Falta columna: {col}"

    def test_filas_conservadas(self, raw_measurements):
        out = build_measurement_features(raw_measurements)
        assert len(out) == len(raw_measurements)

    def test_is_baseline_correcto(self, raw_measurements):
        out = build_measurement_features(raw_measurements)
        expected = raw_measurements["TestCategory"].isin(("Baseline", "VeryFirstReference"))
        pd.testing.assert_series_equal(out["is_baseline"], expected, check_names=False)

    def test_total_loss_es_max_wavelengths(self, raw_measurements):
        out = build_measurement_features(raw_measurements)
        expected_max = raw_measurements[["loss_1310", "loss_1550"]].max(axis=1)
        pd.testing.assert_series_equal(out["total_loss_db"], expected_max, check_names=False)

    def test_distance_capped_con_loss_nulo(self, measurements_with_null_loss):
        out = build_measurement_features(measurements_with_null_loss)
        # Las filas con loss NaN deben usar elements_loss_sum_db como fallback
        capped_rows = out[out["distance_capped"]]
        assert not capped_rows.empty
        assert capped_rows["total_loss_db"].notna().all()

    def test_sin_loss_columns(self):
        df = pd.DataFrame({
            "resultid": [1],
            "TestTime": [pd.Timestamp("2024-01-01", tz="UTC")],
            "TestCategory": ["Monitoring"],
            "FaultStatus": ["Normal"],
            "BaselineId": [1],
            "element_count": [3.0],
        })
        out = build_measurement_features(df)
        assert np.isnan(out["total_loss_db"].iloc[0])
        assert np.isnan(out["avg_loss_db"].iloc[0])


# ---------------------------------------------------------------------------
# build_baseline_lookup
# ---------------------------------------------------------------------------

class TestBuildBaselineLookup:
    def test_devuelve_columnas_renombradas(self, raw_measurements):
        features = build_measurement_features(raw_measurements)
        lookup = build_baseline_lookup(features)
        for col in ("baseline_total_loss_db", "baseline_avg_loss_db", "baseline_test_time"):
            assert col in lookup.columns, f"Falta columna renombrada: {col}"

    def test_resultid_unico(self, raw_measurements):
        features = build_measurement_features(raw_measurements)
        lookup = build_baseline_lookup(features)
        assert lookup["resultid"].is_unique


# ---------------------------------------------------------------------------
# build_delta_features
# ---------------------------------------------------------------------------

class TestBuildDeltaFeatures:
    def _pipeline(self, df):
        features = build_measurement_features(df)
        lookup = build_baseline_lookup(features)
        return build_delta_features(features, lookup)

    def test_columnas_delta(self, raw_measurements):
        out = self._pipeline(raw_measurements)
        for col in ("delta_loss_db", "delta_loss_pct", "days_since_baseline",
                    "baseline_found", "baseline_resolved", "new_event_count"):
            assert col in out.columns, f"Falta columna: {col}"

    def test_filas_conservadas(self, raw_measurements):
        out = self._pipeline(raw_measurements)
        assert len(out) == len(raw_measurements)

    def test_delta_loss_cuando_baseline_resuelto(self, raw_measurements):
        out = self._pipeline(raw_measurements)
        resolved = out[out["baseline_resolved"]]
        computed = resolved["total_loss_db"] - resolved["baseline_total_loss_db"]
        pd.testing.assert_series_equal(
            resolved["delta_loss_db"].reset_index(drop=True),
            computed.reset_index(drop=True),
            check_names=False,
        )

    def test_baseline_not_found_da_nan_delta(self):
        df = pd.DataFrame({
            "resultid": [1],
            "route_id": [1],
            "TestTime": [pd.Timestamp("2024-06-01", tz="UTC")],
            "TestCategory": ["Monitoring"],
            "FaultStatus": ["Normal"],
            "BaselineId": [999],  # no existe en lookup
            "loss_1310": [3.0],
            "loss_1550": [2.8],
            "fms_deviation_db_1310": [0.5],
            "element_count": [4.0],
            "elements_loss_sum_db": [2.5],
        })
        out = self._pipeline(df)
        assert not out["baseline_found"].iloc[0]
        assert np.isnan(out["delta_loss_db"].iloc[0])


# ---------------------------------------------------------------------------
# build_route_timeline_features
# ---------------------------------------------------------------------------

class TestBuildRouteTimelineFeatures:
    def _full_pipeline(self, df):
        features = build_measurement_features(df)
        lookup = build_baseline_lookup(features)
        delta = build_delta_features(features, lookup)
        return build_route_timeline_features(delta)

    def test_columnas_rolling(self, raw_measurements):
        out = self._full_pipeline(raw_measurements)
        for col in ("rolling_mean_delta", "rolling_std_delta", "slope_delta",
                    "consecutive_increasing_delta", "n_fault_detected_last_30d"):
            assert col in out.columns, f"Falta columna: {col}"

    def test_filas_conservadas(self, raw_measurements):
        out = self._full_pipeline(raw_measurements)
        assert len(out) == len(raw_measurements)

    def test_dataframe_vacio(self):
        df_delta = pd.DataFrame(columns=[
            "route_id", "TestTime", "delta_loss_db", "FaultStatus",
            "total_loss_db", "baseline_total_loss_db",
        ])
        out = build_route_timeline_features(df_delta)
        assert out.empty

    def test_model_feature_columns_presentes(self, raw_measurements):
        out = self._full_pipeline(raw_measurements)
        for col in MODEL_FEATURE_COLUMNS:
            assert col in out.columns, f"MODEL_FEATURE_COLUMNS falta en output: {col}"

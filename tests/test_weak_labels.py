"""Tests para src/weak_labels.py — sin dependencia de la API FMS."""

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from weak_labels import (
    DEFAULT_MARGIN_DB,
    DEFAULT_THRESHOLD_DB,
    WARNING_THRESHOLD_DB,
    label_at_risk,
    label_severity,
)


def _df(delta_loss_db, fault_status="Normal"):
    """Helper: DataFrame mínimo para las funciones de etiquetado."""
    vals = [delta_loss_db] if not hasattr(delta_loss_db, "__iter__") else list(delta_loss_db)
    statuses = (
        [fault_status] * len(vals)
        if isinstance(fault_status, str)
        else list(fault_status)
    )
    return pd.DataFrame({"delta_loss_db": vals, "FaultStatus": statuses})


# ---------------------------------------------------------------------------
# label_at_risk
# ---------------------------------------------------------------------------

class TestLabelAtRisk:
    def test_ok_bajo_umbral(self):
        df = _df(1.0)
        assert label_at_risk(df).iloc[0] == 0

    def test_at_risk_supera_umbral(self):
        df = _df(DEFAULT_THRESHOLD_DB)
        assert label_at_risk(df).iloc[0] == 1

    def test_at_risk_dentro_margen(self):
        # threshold_db - margin_db = 3.0 - 0.5 = 2.5 → debe ser 1
        df = _df(DEFAULT_THRESHOLD_DB - DEFAULT_MARGIN_DB)
        assert label_at_risk(df).iloc[0] == 1

    def test_fault_detected_fuerza_at_risk(self):
        df = _df(0.1, fault_status="Detected")
        assert label_at_risk(df).iloc[0] == 1

    def test_normal_con_bajo_delta_es_cero(self):
        df = _df(0.1, fault_status="Normal")
        assert label_at_risk(df).iloc[0] == 0

    def test_serie_mixta(self):
        deltas = [0.5, 2.5, 4.0, 1.0]
        statuses = ["Normal", "Normal", "Normal", "Detected"]
        df = _df(deltas, statuses)
        result = label_at_risk(df).tolist()
        assert result == [0, 1, 1, 1]

    def test_nombre_serie(self):
        df = _df(1.0)
        assert label_at_risk(df).name == "at_risk"

    def test_tipo_int(self):
        df = _df([0.5, 3.0])
        assert label_at_risk(df).dtype in (np.dtype("int32"), np.dtype("int64"))

    def test_umbral_personalizado(self):
        df = _df(2.0)
        assert label_at_risk(df, threshold_db=2.0, margin_db=0.0).iloc[0] == 1
        assert label_at_risk(df, threshold_db=3.0, margin_db=0.0).iloc[0] == 0


# ---------------------------------------------------------------------------
# label_severity
# ---------------------------------------------------------------------------

class TestLabelSeverity:
    def test_ok(self):
        df = _df(0.5)
        assert label_severity(df).iloc[0] == "ok"

    def test_warning(self):
        df = _df(WARNING_THRESHOLD_DB)
        assert label_severity(df).iloc[0] == "warning"

    def test_critical_por_delta(self):
        df = _df(DEFAULT_THRESHOLD_DB)
        assert label_severity(df).iloc[0] == "critical"

    def test_critical_por_fault(self):
        df = _df(0.1, fault_status="Detected")
        assert label_severity(df).iloc[0] == "critical"

    def test_critical_tiene_prioridad_sobre_warning(self):
        # Si el delta supera el umbral crítico Y warning, resultado debe ser "critical"
        df = _df(DEFAULT_THRESHOLD_DB + 1.0)
        assert label_severity(df).iloc[0] == "critical"

    def test_serie_completa(self):
        deltas = [0.3, WARNING_THRESHOLD_DB, DEFAULT_THRESHOLD_DB]
        statuses = ["Normal", "Normal", "Normal"]
        df = _df(deltas, statuses)
        result = label_severity(df).tolist()
        assert result == ["ok", "warning", "critical"]

    def test_nombre_serie(self):
        df = _df(1.0)
        assert label_severity(df).name == "severity"

    def test_valores_posibles(self):
        deltas = np.linspace(-1, 5, 50)
        df = _df(deltas.tolist())
        result = label_severity(df)
        assert set(result.unique()).issubset({"ok", "warning", "critical"})

    def test_umbral_personalizado(self):
        df = _df(2.5)
        # Con threshold 2.0 → critical
        assert label_severity(df, threshold_db=2.0).iloc[0] == "critical"
        # Con threshold 5.0 y warning 3.0 → ok
        assert label_severity(df, threshold_db=5.0, warning_threshold_db=3.0).iloc[0] == "ok"

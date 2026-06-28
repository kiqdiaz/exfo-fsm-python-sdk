"""
weak_labels.py — Etiquetas débiles (weak supervision) derivadas de la regla de
dominio de atenuación (~3dB) y de la señal de FaultStatus reportada por el FMS.

IMPORTANTE: no hay ground truth confirmado de incidentes reales (ver Contexto
del plan). Estas etiquetas son una aproximación basada en reglas, no un hecho
verificado por el equipo de NOC — los modelos entrenados/evaluados contra ellas
deben interpretarse con esa limitación en mente (ver notebook 04_evaluation).
"""

import pandas as pd

DEFAULT_THRESHOLD_DB = 3.0
DEFAULT_MARGIN_DB = 0.5

WARNING_THRESHOLD_DB = 1.5


def label_at_risk(
    df: pd.DataFrame,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    margin_db: float = DEFAULT_MARGIN_DB,
) -> pd.Series:
    """Weak label binaria por medición: 1 (at_risk) si delta_loss_db se acerca o
    supera el umbral de dominio (threshold_db - margin_db), o si FaultStatus ya
    marcó 'Detected'. 0 en otro caso."""
    near_threshold = df["delta_loss_db"] >= (threshold_db - margin_db)
    fault_detected = df["FaultStatus"].eq("Detected")
    return (near_threshold | fault_detected).astype(int).rename("at_risk")


def label_severity(
    df: pd.DataFrame,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    warning_threshold_db: float = WARNING_THRESHOLD_DB,
) -> pd.Series:
    """Weak label categórica de 3 niveles para EDA y para el ranking de
    prioridad final (más informativa para un humano que una etiqueta binaria)."""
    critical = (df["delta_loss_db"] >= threshold_db) | df["FaultStatus"].eq("Detected")
    warning = df["delta_loss_db"] >= warning_threshold_db

    severity = pd.Series("ok", index=df.index, name="severity")
    severity[warning] = "warning"
    severity[critical] = "critical"
    return severity

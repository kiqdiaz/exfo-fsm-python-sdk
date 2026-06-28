"""
features.py — Feature engineering tabular sobre mediciones OTDR/iOLM extraídas
del EXFO FMS (ver fms_extract.flatten_result).

Punto clave de diseño (ver Contexto del plan): el delta de atenuación de cada
medición se calcula contra SU PROPIO baseline referenciado (metadata.BaselineId),
no contra "el baseline más reciente de la ruta" — el manual de la API documenta
que un baseline puede renovarse (update/reset) a mitad del historial de una
ruta, así que usar el más reciente sería incorrecto para mediciones antiguas.
"""

import re
from typing import List

import numpy as np
import pandas as pd

_LOSS_COLUMN_RE = re.compile(r"^loss_\d+(\.\d+)?$")

BASELINE_CATEGORIES = ("Baseline", "VeryFirstReference")

# Columnas numéricas canónicas usadas para modelado (notebooks 03/04) y para
# scoring.py en inferencia — mantenerlas en un solo lugar evita que el set de
# features usado para entrenar diverja del usado para predecir.
MODEL_FEATURE_COLUMNS = [
    "delta_loss_db",
    "delta_loss_pct",
    "fms_deviation_db",
    "rolling_mean_delta",
    "rolling_std_delta",
    "slope_delta",
    "new_event_count",
    "n_fault_detected_last_30d",
    "consecutive_increasing_delta",
    "days_since_baseline",
    "distance_capped",
]


def _loss_columns(df: pd.DataFrame) -> List[str]:
    """Columnas loss_<wavelength> (ej. loss_1650), NO cualquier columna que
    empiece con 'loss_' — evita capturar por accidente columnas derivadas
    como elements_loss_sum_db si alguna vez se renombraran con ese prefijo."""
    return [c for c in df.columns if _LOSS_COLUMN_RE.match(c)]


def _fms_deviation_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c.startswith("fms_deviation_db_")]


def build_measurement_features(df_results: pd.DataFrame) -> pd.DataFrame:
    """Una fila por medición (resultid). Requiere las columnas producidas por
    fms_extract.flatten_result (esquema confirmado contra el servidor real,
    route_id=41, FMS 8.5)."""
    df = df_results.copy()
    df["TestTime"] = pd.to_datetime(df["TestTime"], errors="coerce", utc=True)
    df["is_baseline"] = df["TestCategory"].isin(BASELINE_CATEGORIES)

    loss_cols = _loss_columns(df)
    if loss_cols:
        df["total_loss_db"] = df[loss_cols].max(axis=1, skipna=True)
        df["avg_loss_db"]   = df[loss_cols].mean(axis=1, skipna=True)
    else:
        df["total_loss_db"] = float("nan")
        df["avg_loss_db"]   = float("nan")

    # Respaldo para rutas cuya distancia óptica real excede el alcance
    # confiable del OTDR (hallazgo real: ruta 60/ICA-P2-Poroma, baseline a
    # ~135km contra un alcance confiable de ~110km — ver
    # fms_extract.MAX_RELIABLE_DISTANCE_KM). En esos casos el Loss end-to-end
    # que reporta el FMS viene NaN porque la medición completa no es
    # confiable, pero la suma de pérdidas de los elementos dentro del rango
    # confiable (elements_loss_sum_db) sigue siendo una señal válida.
    # distance_capped marca en qué filas se usó este respaldo, para que
    # quede explícito en vez de mezclarse silenciosamente con el Loss normal.
    if "elements_loss_sum_db" in df.columns:
        df["distance_capped"] = df["total_loss_db"].isna() & df["elements_loss_sum_db"].notna()
        df["total_loss_db"] = df["total_loss_db"].fillna(df["elements_loss_sum_db"])
    else:
        df["distance_capped"] = False

    # Desviación que el propio FMS ya calcula contra el baseline (por
    # wavelength) — útil como cross-check del delta calculado más abajo vía
    # join por BaselineId (build_delta_features).
    dev_cols = _fms_deviation_columns(df)
    df["fms_deviation_db"] = df[dev_cols].max(axis=1, skipna=True) if dev_cols else float("nan")

    # event_count = elementos de enlace (conectores/splices/splitters) dentro
    # del rango confiable de la medición iOLM (brief.Measurement.Elements,
    # ya filtrados por MAX_RELIABLE_DISTANCE_KM en fms_extract). Vacío/NaN
    # para mediciones OTDR puras, que no traen ese desglose.
    df["event_count"] = df["element_count"] if "element_count" in df.columns else float("nan")

    return df


def build_baseline_lookup(df_features: pd.DataFrame) -> pd.DataFrame:
    """Indexa df_features por resultid para poder resolver, para cualquier
    medición, los valores de SU baseline referenciado (BaselineId)."""
    cols = [c for c in ("resultid", "TestTime", "total_loss_db", "avg_loss_db", "event_count")
            if c in df_features.columns]
    lookup = df_features[cols].drop_duplicates(subset="resultid", keep="last")
    return lookup.rename(columns={
        "TestTime":      "baseline_test_time",
        "total_loss_db": "baseline_total_loss_db",
        "avg_loss_db":   "baseline_avg_loss_db",
        "event_count":   "baseline_event_count",
    })


def build_delta_features(df_features: pd.DataFrame, df_baseline_lookup: pd.DataFrame) -> pd.DataFrame:
    """Join BaselineId == resultid (no por ruta+categoría más reciente) para
    obtener el delta real de atenuación respecto al baseline que el FMS usó
    para cada medición específica."""
    df = df_features.merge(
        df_baseline_lookup,
        how="left",
        left_on="BaselineId",
        right_on="resultid",
        suffixes=("", "_baseline"),
    )
    if "resultid_baseline" in df.columns:
        df = df.drop(columns=["resultid_baseline"])

    # baseline_found: el BaselineId encontró una medición con ese resultid.
    # baseline_resolved: además esa medición tiene un total_loss_db utilizable
    # (no NaN) — confirmado contra datos reales que algunas mediciones, incluso
    # baselines, traen Loss="NaN" de fábrica (falla de medición del lado del
    # FMS/instrumento, no un problema del join). Son cosas distintas: un
    # BaselineId puede estar "encontrado" pero no ser "resuelto" si su propio
    # valor de pérdida no es usable para calcular el delta.
    df["baseline_found"] = df["baseline_test_time"].notna()
    df["baseline_resolved"] = df["baseline_total_loss_db"].notna()
    df["delta_loss_db"] = df["total_loss_db"] - df["baseline_total_loss_db"]
    df["delta_loss_pct"] = df["delta_loss_db"] / df["baseline_total_loss_db"].replace(0, np.nan)
    df["new_event_count"] = df["event_count"] - df["baseline_event_count"]
    df["days_since_baseline"] = (
        (df["TestTime"] - df["baseline_test_time"]).dt.total_seconds() / 86400
    )

    return df


def _slope(arr: np.ndarray) -> float:
    mask = ~np.isnan(arr)
    if mask.sum() < 2:
        return float("nan")
    x = np.arange(len(arr), dtype=float)
    return float(np.polyfit(x[mask], arr[mask], 1)[0])


def build_route_timeline_features(df_delta: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Por ruta, ordenado por TestTime, calcula rolling stats sobre
    delta_loss_db (señal de degradación gradual vs salto abrupto) y señales de
    fallos recientes (FaultStatus == 'Detected')."""
    df = df_delta.copy()
    df["TestTime"] = pd.to_datetime(df["TestTime"], errors="coerce", utc=True)
    df["is_fault_detected"] = df["FaultStatus"].eq("Detected")

    pieces = []
    for _, g in df.groupby("route_id", sort=False):
        g = g.sort_values("TestTime").copy()

        g["rolling_mean_delta"] = g["delta_loss_db"].rolling(window, min_periods=1).mean()
        g["rolling_std_delta"]  = g["delta_loss_db"].rolling(window, min_periods=2).std()
        g["slope_delta"] = g["delta_loss_db"].rolling(window, min_periods=2).apply(_slope, raw=True)

        increasing = g["delta_loss_db"].diff().gt(0)
        g["consecutive_increasing_delta"] = increasing.groupby((~increasing).cumsum()).cumsum()

        s_fault = g.set_index("TestTime")["is_fault_detected"].astype(float)
        g["n_fault_detected_last_30d"] = s_fault.rolling("30D").sum().to_numpy()

        last_fault_time = g["TestTime"].where(g["is_fault_detected"]).ffill().shift()
        g["time_since_last_fault_days"] = (
            (g["TestTime"] - last_fault_time).dt.total_seconds() / 86400
        )

        pieces.append(g)

    return pd.concat(pieces, ignore_index=True) if pieces else df

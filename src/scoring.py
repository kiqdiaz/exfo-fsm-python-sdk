#!/usr/bin/env python3
"""
scoring.py — Scoring batch de riesgo OTDR (deployment ligero, no productivo).

Carga el modelo registrado en el MLflow Model Registry (por alias, ej.
'production'), aplica las features ya calculadas por el pipeline de notebooks
(02_data_preparation) sobre la medición más reciente de cada ruta, y genera un
ranking de prioridad de intervención/mantenimiento.

No vuelve a extraer datos del FMS — asume que data/processed/ ya está
actualizado. Para refrescar los datos, correr antes los notebooks
02_data_preparation (extracción) y 03/04 (entrenamiento/registro del modelo).

Uso:
  python -m src.scoring --output alerts.csv --top 20
"""

import argparse
import sys
from pathlib import Path

import mlflow
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import mlflow_utils  # noqa: E402
from features import MODEL_FEATURE_COLUMNS  # noqa: E402

DEFAULT_FEATURES_PATH = Path("data/processed/features_by_route_timeline.parquet")


def score_latest_measurements(
    model_name: str = "otdr_risk_classifier",
    alias: str = "production",
    features_path: Path = DEFAULT_FEATURES_PATH,
) -> pd.DataFrame:
    """Devuelve un DataFrame con una fila por ruta (la medición más reciente),
    ordenado por risk_score descendente."""
    df = pd.read_parquet(features_path)
    if df.empty:
        return df

    latest = (
        df.sort_values("TestTime")
        .groupby("route_id", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )

    mlflow_utils.set_tracking_uri()
    model = mlflow.sklearn.load_model(f"models:/{model_name}@{alias}")

    cols = [c for c in MODEL_FEATURE_COLUMNS if c in latest.columns]
    X = latest[cols].fillna(0)

    if hasattr(model, "predict_proba"):
        latest["risk_score"] = model.predict_proba(X)[:, 1]
    else:
        latest["risk_score"] = model.predict(X)

    out_cols = [c for c in
                ("route_id", "route_name", "TestTime", "delta_loss_db", "severity", "risk_score")
                if c in latest.columns]

    return latest[out_cols].sort_values("risk_score", ascending=False).reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-name", default="otdr_risk_classifier",
                         help="Nombre del modelo en el Model Registry")
    parser.add_argument("--alias", default="production",
                         help="Alias de la versión a cargar (ej. production, staging)")
    parser.add_argument("--features-path", default=str(DEFAULT_FEATURES_PATH),
                         help="Parquet con features por medición/ruta ya calculado")
    parser.add_argument("--top", type=int, default=20, help="Filas a mostrar en pantalla")
    parser.add_argument("--output", help="Guardar el ranking completo en CSV")
    args = parser.parse_args()

    ranking = score_latest_measurements(
        model_name=args.model_name,
        alias=args.alias,
        features_path=Path(args.features_path),
    )

    if ranking.empty:
        print("[INFO] No hay datos en features_path para puntuar.")
        return

    print(ranking.head(args.top).to_string(index=False))

    if args.output:
        ranking.to_csv(args.output, index=False)
        print(f"\n[+] Ranking completo guardado en: {args.output}")


if __name__ == "__main__":
    main()

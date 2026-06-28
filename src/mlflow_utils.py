"""
mlflow_utils.py — Helpers de tracking y Model Registry para los experimentos de
detección de riesgo OTDR. Tracking local con backend de archivos en ./mlruns
(sin servidor remoto), tal como se acordó con el usuario.

Nota sobre Model Registry: se usan *aliases* (`set_registered_model_alias`) en
vez de *stages* (`transition_model_version_stage`), porque los stages están
deprecados desde mlflow 2.9. El URI de carga resultante es
`models:/<name>@<alias>` (no `models:/<name>/<stage>`).
"""

from pathlib import Path
from typing import Optional

import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient

_DB_PATH = Path(__file__).resolve().parent.parent / "mlflow.db"


def set_tracking_uri() -> None:
    """Apunta mlflow al backend SQLite local mlflow.db. Debe llamarse antes
    de cualquier operación de tracking o de carga de modelos del Registry."""
    mlflow.set_tracking_uri(f"sqlite:///{_DB_PATH}")


def get_or_create_experiment(name: str) -> str:
    set_tracking_uri()
    exp = mlflow.set_experiment(name)
    return exp.experiment_id


def log_dataset_snapshot(df: pd.DataFrame, name: str) -> None:
    """Loguea metadata del dataset usado en el run activo (debe llamarse dentro
    de un mlflow.start_run() activo), para poder rastrear qué snapshot de
    data/ generó cada run."""
    params = {
        f"{name}_n_rows": len(df),
    }
    if "route_id" in df.columns:
        params[f"{name}_n_routes"] = df["route_id"].nunique()
    if "TestTime" in df.columns and len(df):
        times = pd.to_datetime(df["TestTime"], errors="coerce")
        params[f"{name}_date_min"] = str(times.min())
        params[f"{name}_date_max"] = str(times.max())
    mlflow.log_params(params)


def log_model_run(
    run_name: str,
    params: dict,
    metrics: dict,
    model=None,
    model_flavor: str = "sklearn",
    artifacts: Optional[dict] = None,
) -> str:
    """Abre un run de MLflow, loguea params/metrics/modelo/artifacts y lo cierra.
    Devuelve el run_id."""
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        if model is not None:
            log_fn = getattr(mlflow, model_flavor).log_model
            log_fn(model, artifact_path="model")
        for art_name, art_path in (artifacts or {}).items():
            mlflow.log_artifact(str(art_path), artifact_path=art_name)
        return run.info.run_id


def register_best_model(run_id: str, model_name: str = "otdr_risk_classifier") -> str:
    """Registra el modelo logueado en run_id (bajo artifact_path='model') en el
    Model Registry. Devuelve la versión asignada."""
    result = mlflow.register_model(f"runs:/{run_id}/model", model_name)
    return result.version


def promote_model(model_name: str, version: str, alias: str = "production") -> None:
    """Asigna un alias (ej. 'production') a una versión registrada del modelo."""
    client = MlflowClient()
    client.set_registered_model_alias(model_name, alias, version)

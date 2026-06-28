"""
fms_extract.py — Extracción masiva de topología y mediciones del EXFO FMS, con
caché en disco (parquet) y soporte de reanudación para corridas largas sobre
todas las rutas ópticas disponibles.
"""

import json
import logging
import math
import time
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from tqdm import tqdm

from fms_client import FmsClient

log = logging.getLogger(__name__)


def _to_float(val) -> float:
    """brief.LinkResults.Results[].Loss/Wavelength vienen como STRING en la API
    real (confirmado contra el servidor, ej. Loss="18.910"), no como número."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


MAX_RELIABLE_DISTANCE_KM = 110.0
"""Distancia máxima confiable de la traza OTDR para rutas cuya distancia
óptica real excede el alcance del instrumento.

Hallazgo del usuario (no derivable de la API): en rutas como la 60
(ICA-P2-Poroma), el baseline midió ~135km, pero esa distancia está fuera del
alcance confiable del OTDR — pequeñas perturbaciones hacen que la distancia
detectada varíe entre mediciones, y todo lo que aparece después de ~110km es
ruido, no señal real (revisión posterior de los equipos actualmente instalados
ajustó este límite de 120km a 110km). Por eso `brief.Measurement.Elements[]`
se filtra por `Position <= MAX_RELIABLE_DISTANCE_KM * 1000` antes de agregar
pérdida/eventos.

NOTA DE UNIDADES: la API devuelve `Position` en **metros** (confirmado contra
el servidor real — el elemento más lejano de la ruta 41 aparece a 62,249.5,
que corresponde a ~62.25 km). La comparación convierte km→m multiplicando por
1000."""


def _extract_element_features(brief: dict) -> dict:
    """Extrae conteo de elementos y agregados de pérdida/desviación desde
    brief.Measurement.Elements[], limitando a elementos dentro de
    MAX_RELIABLE_DISTANCE_KM (ver nota arriba). Devuelve también
    elements_loss_sum_db: la suma de pérdidas de los elementos dentro del
    rango confiable, usada en features.py como respaldo de total_loss_db
    cuando el Loss end-to-end del FMS viene NaN (rutas que exceden el alcance
    del OTDR).

    Pérdida: suma la pérdida puntual de cada elemento (conector/splice/splitter)
    MÁS la pérdida de su PreviousFiberSection (la sección de fibra previa), tomada
    del campo Results[].Loss ya calculado por el FMS. Esto hace que
    elements_loss_sum_db se aproxime a la pérdida total del enlace (loss_<wl>).

    Desviación: agrega tanto la desviación del elemento (Deviation.Loss) como la
    de su sección de fibra previa (Deviation.PreviousFiberSectionLoss), y toma el
    máximo en valor absoluto — las desviaciones pueden ser positivas o negativas
    según el sentido del cambio respecto al baseline, y max() sobre valores con
    signo pierde las degradaciones severas que producen desviaciones negativas."""
    elements = brief.get("Measurement", {}).get("Elements") or []

    # Position viene en metros desde la API; MAX_RELIABLE_DISTANCE_KM está en km.
    elements_in_range = [el for el in elements if _to_float(el.get("Position")) <= MAX_RELIABLE_DISTANCE_KM * 1000]

    loss_in_range, devs_in_range = [], []
    for el in elements_in_range:
        # Pérdida y desviación de la sección de fibra previa al elemento
        pfs = el.get("PreviousFiberSection") or {}
        for pfs_res in pfs.get("Results") or []:
            pfs_loss = _to_float(pfs_res.get("Loss"))
            if not math.isnan(pfs_loss):
                loss_in_range.append(pfs_loss)

        # Pérdida y desviación del elemento puntual (conector/splice/splitter)
        for res in el.get("Results", []) or []:
            loss_val = _to_float(res.get("Loss"))
            if not math.isnan(loss_val):
                loss_in_range.append(loss_val)
            dev = res.get("Deviation") or {}
            el_dev = _to_float(dev.get("Loss"))
            if not math.isnan(el_dev):
                devs_in_range.append(el_dev)
            pfs_dev = _to_float(dev.get("PreviousFiberSectionLoss"))
            if not math.isnan(pfs_dev):
                devs_in_range.append(pfs_dev)

    return {
        "element_count":            len(elements_in_range),
        "element_count_raw":        len(elements),
        "elements_loss_sum_db":     sum(loss_in_range) if loss_in_range else float("nan"),
        "max_element_deviation_db": max(abs(d) for d in devs_in_range) if devs_in_range else float("nan"),
    }


def flatten_result(raw_result: dict) -> dict:
    """Aplana un resultado individual de /v1/results a un dict plano.

    Esquema confirmado contra el servidor real (route_id=41, FMS 8.5):
      - metadata.BaselineId, HasFault, PortId existen junto a los campos ya
        usados por main.py (TestTime, TestType, TestCategory, FaultStatus).
      - brief.LinkResults.Results[] trae, por wavelength, Loss y además
        Deviation.Loss — la desviación vs. baseline que el propio FMS ya
        calcula (se captura como fms_deviation_db_<wavelength>, útil como
        cross-check del delta calculado en features.py vía join por
        BaselineId).
      - brief.Measurement.Elements[] (solo en mediciones iOLM) son los
        elementos de enlace (conectores, splices, splitters) — el "evento"
        documentado en el README. Se filtran por MAX_RELIABLE_DISTANCE_KM
        (ver _extract_element_features) antes de agregar conteo/pérdida.
    """
    resultid = raw_result.get("resultid", "")
    m = raw_result.get("metadata", {}) or {}
    brief = raw_result.get("brief", {}) or {}

    row = {
        "resultid":     resultid,
        "route_id":     m.get("AssetId", ""),
        "route_name":   m.get("AssetName", ""),
        "port_id":      m.get("PortId", ""),
        "TestTime":     m.get("TestTime", ""),
        "TestType":     m.get("TestType", ""),
        "TestCategory": m.get("TestCategory", ""),
        "FaultStatus":  m.get("FaultStatus", ""),
        "HasFault":     m.get("HasFault"),
        "BaselineId":   m.get("BaselineId", ""),
        "PromiseId":    m.get("PromiseId", ""),
    }

    link_results = (brief.get("LinkResults") or {}).get("Results", []) if isinstance(brief, dict) else []
    for lr in link_results:
        wl = lr.get("Wavelength")
        if wl is not None:
            row[f"loss_{wl}"] = _to_float(lr.get("Loss"))
            dev = lr.get("Deviation") or {}
            row[f"fms_deviation_db_{wl}"] = _to_float(dev.get("Loss"))
    row["n_wavelengths"] = len(link_results)

    row.update(_extract_element_features(brief))

    row["brief_raw"] = json.dumps(brief, ensure_ascii=False) if brief else ""

    return row


def backfill_element_features(df_results: pd.DataFrame) -> pd.DataFrame:
    """Recalcula element_count/element_count_raw/elements_loss_sum_db/
    max_element_deviation_db a partir de brief_raw ya cacheado en disco, sin
    volver a golpear la API. Útil tras ajustar MAX_RELIABLE_DISTANCE_KM o
    _extract_element_features sobre datos ya extraídos."""
    df = df_results.copy()
    parsed = df["brief_raw"].apply(
        lambda s: _extract_element_features(json.loads(s)) if s else _extract_element_features({})
    )
    new_cols = pd.DataFrame(parsed.tolist(), index=df.index)
    for col in new_cols.columns:
        df[col] = new_cols[col]
    return df


def extract_all_routes(
    client: FmsClient,
    out_path: Path = Path("data/raw/optical_routes.parquet"),
    force: bool = False,
) -> pd.DataFrame:
    """Pagina TODAS las rutas ópticas vía client.iter_all_optical_routes y
    cachea en parquet. Si out_path ya existe y force=False, lo carga desde
    disco sin volver a golpear la API."""
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return pd.read_parquet(out_path)

    rows = []
    for route in client.iter_all_optical_routes():
        rows.append({
            "id":          route.get("id", ""),
            "name":        route.get("name", ""),
            "type":        route.get("type", ""),
            "description": route.get("description", ""),
        })

    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return df


def _load_state(state_path: Path) -> set:
    if not state_path.exists():
        return set()
    try:
        return set(json.loads(state_path.read_text()).get("completed_route_ids", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_state(state_path: Path, completed: set) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"completed_route_ids": sorted(completed)}, indent=2))


def extract_results_for_routes(
    client: FmsClient,
    route_ids: Iterable[str],
    out_path: Path = Path("data/raw/results_all.parquet"),
    state_path: Path = Path("data/interim/extraction_state.json"),
    resume: bool = True,
    sleep_between_calls: float = 0.3,
    page_size: int = 500,
    max_results_per_route: Optional[int] = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Extrae el histórico de mediciones de cada ruta en route_ids, aplana cada
    resultado con flatten_result y cachea en out_path (parquet).

    Pagina con $skip vía client.iter_all_results_for_route — confirmado contra
    el servidor real que /v1/results limita cada respuesta a 1000 filas por
    defecto (algunas rutas tienen miles de mediciones, ej. la ruta 41 con 2658),
    así que una sola llamada sin paginar trunca el historial silenciosamente.

    Resumible: las rutas ya completadas se registran en state_path, así que
    interrumpir la extracción (Ctrl+C) y volver a correr esta función no
    re-pide rutas ya bajadas. Hace merge incremental por resultid contra
    out_path si ya existe, en vez de sobreescribir.

    sleep_between_calls evita saturar el servidor real con cientos de llamadas
    consecutivas. max_results_per_route acota el volumen histórico por ruta si
    se prefiere no traer el historial completo de rutas muy grandes.
    """
    out_path = Path(out_path)
    state_path = Path(state_path)

    done = _load_state(state_path) if resume else set()

    # Si el state dice que hay rutas completadas pero el parquet no existe, el
    # estado quedó corrupto (ej. una corrida anterior guardó estado pero devolvió
    # 0 resultados y nunca escribió el archivo). Resetear para forzar re-extracción.
    if done and not out_path.exists():
        log.warning(
            "State file indica %d rutas completadas pero %s no existe — "
            "reseteando estado para forzar re-extracción.",
            len(done), out_path,
        )
        done = set()
        _save_state(state_path, done)

    existing_df = pd.read_parquet(out_path) if (resume and out_path.exists()) else pd.DataFrame()

    route_ids = [str(r) for r in route_ids]
    pending = [rid for rid in route_ids if rid not in done]

    log.info(
        "Inicio extracción — out_path=%s | total_rutas=%d | ya_completadas=%d | pendientes=%d | existing_rows=%d",
        out_path, len(route_ids), len(done), len(pending), len(existing_df),
    )
    if done:
        log.debug("Rutas ya completadas: %s", sorted(done))
    if not pending:
        log.warning("No hay rutas pendientes. Si esperas nuevos datos, usa resume=False o borra %s", state_path)

    iterator = tqdm(pending, desc="Extrayendo mediciones por ruta") if show_progress else pending

    new_rows = []
    for route_id in iterator:
        try:
            n_for_route = 0
            log.debug("Ruta %s — iniciando paginación (page_size=%d)", route_id, page_size)
            for raw in client.iter_all_results_for_route(route_id, page_size=page_size):
                new_rows.append(flatten_result(raw))
                n_for_route += 1
                if max_results_per_route and n_for_route >= max_results_per_route:
                    log.debug("Ruta %s — límite max_results_per_route=%d alcanzado", route_id, max_results_per_route)
                    break
                if sleep_between_calls and n_for_route % page_size == 0:
                    time.sleep(sleep_between_calls)
        except SystemExit:
            # _data_check (vía main.py) hace sys.exit en error HTTP — no abortar
            # toda la extracción por una ruta puntual, se reintenta en la próxima corrida.
            log.warning("Ruta %s falló con SystemExit (error HTTP), se omite y reintentará en próxima corrida.", route_id)
            continue
        except Exception as e:
            log.warning("Ruta %s falló con excepción: %s", route_id, e, exc_info=True)
            continue

        if n_for_route == 0:
            log.warning(
                "Ruta %s — la API devolvió 0 resultados. No se marca como completada "
                "(se reintentará en la próxima corrida).",
                route_id,
            )
            continue

        log.info("Ruta %s — %d resultados obtenidos.", route_id, n_for_route)
        done.add(route_id)
        _save_state(state_path, done)
        if sleep_between_calls:
            time.sleep(sleep_between_calls)

    log.info("Paginación completa — new_rows acumulados: %d", len(new_rows))

    df_new = pd.DataFrame(new_rows)
    if not existing_df.empty and not df_new.empty:
        df_all = (
            pd.concat([existing_df, df_new], ignore_index=True)
            .drop_duplicates(subset="resultid", keep="last")
        )
    elif not df_new.empty:
        df_all = df_new
    else:
        df_all = existing_df

    if not df_all.empty:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df_all.to_parquet(out_path, index=False)
        log.info("Parquet guardado en %s (%d filas totales)", out_path, len(df_all))
    else:
        log.warning("df_all está vacío — no se escribió ningún archivo en %s", out_path)

    return df_all


def resolve_unmatched_baselines(client: FmsClient, df_results: pd.DataFrame) -> pd.DataFrame:
    """Para los BaselineId que no aparecen como resultid dentro de df_results
    (baseline fuera de la ventana extraída, o de una ruta incompleta), intenta
    resolverlos puntualmente vía client.find_result_by_id.

    Devuelve un DataFrame con las filas adicionales encontradas (en el mismo
    formato de flatten_result) para concatenar con df_results. Si un BaselineId
    no se puede resolver, se omite y se loguea — no se inventa el valor; queda
    como baseline no resuelto para quien construya features.build_delta_features.
    """
    if df_results.empty or "BaselineId" not in df_results.columns:
        return pd.DataFrame()

    known_ids = set(df_results["resultid"])
    needed = (
        df_results[["route_id", "BaselineId"]]
        .dropna(subset=["BaselineId"])
        .loc[lambda d: d["BaselineId"] != ""]
        .drop_duplicates()
    )
    missing = needed[~needed["BaselineId"].isin(known_ids)]

    extra_rows = []
    for _, row in missing.iterrows():
        found = client.find_result_by_id(row["route_id"], row["BaselineId"])
        if found:
            extra_rows.append(flatten_result(found))
        else:
            print(f"[WARN] No se pudo resolver BaselineId={row['BaselineId']} para ruta {row['route_id']}")

    return pd.DataFrame(extra_rows)

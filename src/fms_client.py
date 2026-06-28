"""
fms_client.py — Cliente de alto nivel para la API REST de EXFO FMS, pensado para
notebooks y scripts de analítica.

Envuelve las funciones ya probadas en main.py (auth, llamadas HTTP, manejo de
errores 401/HTTP) en una interfaz orientada a objetos. No duplica esa lógica ni
reescribe el CLI existente — lo importa.
"""

import logging
import sys
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).parent))

import main as _main  # noqa: E402

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Sin esto, los logs no aparecen en notebooks por default (no hay
    # handler configurado y el root logger no llega a stdout de Jupyter).
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


class FmsClient:
    """Envuelve main.py: auth + llamadas HTTP contra el topology API y el data API."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or _main.get_access_token()

    # ── Topología ──────────────────────────────────────────────────────────

    def list_optical_routes(self, page_size: Optional[int] = None,
                             page: Optional[int] = None,
                             name: Optional[str] = None) -> dict:
        """GET /api/topology/opticalroutes — una sola página."""
        params = {}
        if name:       params["name"] = name
        if page_size:  params["pageSize"] = page_size
        if page:       params["pageNumber"] = page
        return _main.api_get(self.token, f"{_main.API_TOPO}/opticalroutes", params)

    def iter_all_optical_routes(self, page_size: int = 100) -> Iterator[dict]:
        """Pagina hasta agotar el total reportado por la API (vía pageNumber).
        pageSize está limitado por el servidor a [1, 100] (confirmado: pedir
        200 devuelve HTTP 400 '11002_attribute_out_of_bounds')."""
        page = 1
        seen = 0
        while True:
            data = self.list_optical_routes(page_size=page_size, page=page)
            objects = _main._objects(data)
            if not objects:
                break
            for obj in objects:
                yield obj.get("opticalRoute", obj)
            seen += len(objects)

            total = _main._total(data)
            if total.isdigit() and seen >= int(total):
                break
            if len(objects) < page_size:
                break
            page += 1

    def get_optical_route(self, route_id: str) -> dict:
        """GET /api/topology/opticalroutes/{id}"""
        logger.info("get_optical_route(route_id=%s)", route_id)
        try:
            data = _main.api_get(self.token, f"{_main.API_TOPO}/opticalroutes/{route_id}")
        except Exception as e:
            # api_get sólo sys.exit(1) en errores HTTP/401; esto cubre lo que
            # se le escapa (timeouts, JSON inválido, etc.) y deja la causa
            # real en el log en vez de un traceback genérico.
            logger.exception(f"get_optical_route({route_id}) falló")
            logger.error(str(e))
            return {"exception": str(e), "route_id": route_id}
        logger.debug("get_optical_route(%s) -> %s", route_id, data)
        return data

    def list_remote_test_units(self) -> Iterator[dict]:
        """GET /api/topology/remotetestunits — cada RTU trae
        `monitoredAssets.OpticalRoute` con los IDs de ruta que monitorea
        (confirmado contra el servidor real: las 4 rutas de este FMS se
        reparten 2 y 2 entre las RTUs `EAOTHICA` y `EAOTHRBL`)."""
        data = _main.api_get(self.token, f"{_main.API_TOPO}/remotetestunits")
        for obj in _main._objects(data):
            yield obj.get("opticalDevice", obj)

    # ── Mediciones ─────────────────────────────────────────────────────────

    def get_results(self, route_id: str, test_type: Optional[str] = None,
                     test_category: Optional[str] = None,
                     fault_status: Optional[str] = None,
                     promise_id: Optional[str] = None,
                     top: Optional[int] = None,
                     skip: Optional[int] = None) -> dict:
        """GET /v1/results — misma construcción de filtro OData que
        main.py:cmd_results, devolviendo el dict de respuesta directamente."""
        filters = [f"metadata/AssetId eq {route_id}"]
        if test_type:      filters.append(f"metadata/TestType eq '{test_type}'")
        if test_category:  filters.append(f"metadata/TestCategory eq '{test_category}'")
        if promise_id:      filters.append(f"metadata/PromiseId eq '{promise_id}'")
        if fault_status:    filters.append(f"metadata/FaultStatus eq '{fault_status}'")

        params = {"$filter": " and ".join(filters), "$orderby": "metadata/TestTime desc"}
        if top:  params["$top"] = top
        if skip: params["$skip"] = skip

        r = _main._data_get(self.token, f"{_main.API_DATA}/v1/results", params)
        _main._data_check(r)
        return r.json()

    def iter_all_results_for_route(self, route_id: str, page_size: int = 500,
                                    **filters) -> Iterator[dict]:
        """Pagina resultados de una ruta vía $top/$skip si el volumen lo requiere.
        El soporte de $skip en /v1/results no está confirmado en el manual de la
        API — validar en notebook 01 antes de depender de esto para rutas con
        historiales muy grandes."""
        skip = 0
        while True:
            data = self.get_results(route_id, top=page_size, skip=skip, **filters)
            results = data.get("results", [])
            if not results:
                break
            yield from results
            if len(results) < page_size:
                break
            skip += page_size

    def get_related_results(self, result_id: str) -> dict:
        """GET /v1/results/{resultId}/relatedresults"""
        r = _main._data_get(self.token, f"{_main.API_DATA}/v1/results/{result_id}/relatedresults")
        _main._data_check(r)
        return r.json()

    def find_result_by_id(self, route_id: str, result_id: str,
                           search_top: int = 500) -> Optional[dict]:
        """Resuelve un resultid específico de una ruta conocida (ej. un BaselineId
        que no apareció en una extracción ya hecha). Primero intenta un filtro
        OData directo por resultid (no confirmado en el manual de la API); si no
        devuelve nada, recurre a traer el historial de la ruta y buscarlo en
        memoria. Devuelve None si no se encuentra por ninguna vía."""
        try:
            params = {
                "$filter": f"metadata/AssetId eq {route_id} and resultid eq '{result_id}'",
                "$top": 1,
            }
            r = _main._data_get(self.token, f"{_main.API_DATA}/v1/results", params)
            if r.ok:
                results = r.json().get("results", [])
                if results:
                    return results[0]
        except Exception:
            pass

        data = self.get_results(route_id, top=search_top)
        for res in data.get("results", []):
            if res.get("resultid") == result_id:
                return res
        return None

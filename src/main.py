#!/usr/bin/env python3
"""
main.py — CLI de consulta para la API REST de EXFO FMS 8.5

Uso:  python main.py <comando> [opciones]
      python main.py --help
      python main.py <comando> --help
"""

import argparse
import json
import os
import sys
import urllib3
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

FMS_BASE = os.environ["FMS_BASE_URL"]
API_TOPO = f"{FMS_BASE}/api/topology"
API_DATA = os.environ["FMS_DATA_URL"]
TOKEN_CACHE = Path(__file__).parent.parent / "fms_token.json"


# ── Token ─────────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    sys.path.insert(0, str(Path(__file__).parent))
    from fms_auth import get_token
    result = get_token()
    if not result:
        print("[FAIL] No se pudo obtener el token. Abortando.")
        sys.exit(1)
    return result["access_token"]


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _do_get(token: str, url: str, params: dict | None = None) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    try:
        return requests.get(url, headers=headers, params=params, verify=False, timeout=30)  # nosec B501  # nosemgrep: python.requests.security.disabled-cert-validation.disabled-cert-validation — cert self-signed del servidor FMS interno
    except requests.exceptions.ConnectionError as e:
        print(f"[FAIL] Sin conexión al FMS: {e}")
        sys.exit(1)


def api_get(token: str, url: str, params: dict | None = None) -> dict | list:
    r = _do_get(token, url, params)
    if r.status_code == 401:
        print("[FAIL] Token rechazado (401). Obtén un token nuevo y reintenta.")
        sys.exit(1)
    if not r.ok:
        print(f"[FAIL] HTTP {r.status_code} — {r.url}")
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text[:400])
        sys.exit(1)
    return r.json()


# ── Salida ────────────────────────────────────────────────────────────────────

def print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def print_table(rows: list[dict], fields: list[str]):
    if not rows:
        print("(sin resultados)")
        return
    widths = {f: len(f) for f in fields}
    for row in rows:
        for f in fields:
            widths[f] = max(widths[f], len(str(row.get(f, ""))))
    header = "  ".join(f.ljust(widths[f]) for f in fields)
    sep    = "  ".join("-" * widths[f] for f in fields)
    print(header)
    print(sep)
    for row in rows:
        print("  ".join(str(row.get(f, "")).ljust(widths[f]) for f in fields))


def save_output(data, path: str):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[+] Resultado guardado en: {path}")


def _objects(data) -> list:
    if isinstance(data, list):
        return data
    if "objects" in data:
        return data["objects"]
    return [data]


def _total(data) -> str:
    if isinstance(data, dict):
        n = data.get("totalObjectCount")
        if n is not None:
            return str(n)
        if "TotalObjectsCount" in data:
            return str(data["TotalObjectsCount"])
    return "?"


# ── Subcomandos ───────────────────────────────────────────────────────────────

def cmd_optical_routes(args, token):
    """GET /api/topology/opticalroutes"""
    params = {}
    if args.name:       params["name"]       = args.name
    if args.page_size:  params["pageSize"]   = args.page_size
    if args.page:       params["pageNumber"] = args.page

    data = api_get(token, f"{API_TOPO}/opticalroutes", params)

    if args.output == "table":
        total = _total(data)
        print(f"Total de rutas: {total}\n")
        rows = []
        for obj in _objects(data):
            or_ = obj.get("opticalRoute", obj)
            rows.append({
                "id":          or_.get("id", ""),
                "name":        or_.get("name", ""),
                "type":        or_.get("type", ""),
                "description": str(or_.get("description") or "")[:50],
            })
        print_table(rows, ["id", "name", "type", "description"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_optical_route(args, token):
    """GET /api/topology/opticalroutes/{id}"""
    data = api_get(token, f"{API_TOPO}/opticalroutes/{args.id}")
    print_json(data)
    if args.save:
        save_output(data, args.save)


def cmd_optical_devices(args, token):
    """GET /api/topology/opticaldevices"""
    params = {}
    if args.name:       params["name"]       = args.name
    if args.ids:        params["ids"]        = args.ids
    if args.page_size:  params["pageSize"]   = args.page_size
    if args.page:       params["pageNumber"] = args.page

    data = api_get(token, f"{API_TOPO}/opticaldevices", params)

    if args.output == "table":
        total   = _total(data)
        partial = data.get("PartialResult", False) if isinstance(data, dict) else False
        print(f"Total: {total}{'  (resultado parcial)' if partial else ''}\n")
        rows = []
        for obj in _objects(data):
            od = obj.get("opticalDevice", obj)
            print("od:", od)
            rows.append({
                "id":        od.get("id", ""),
                "name":      od.get("name", ""),
                "type":      od.get("type", ""),
                "portCount": od.get("portCount", ""),
            })
        print_table(rows, ["id", "name", "type", "portCount"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_device_ports(args, token):
    """GET /api/topology/opticaldevices/{id}/ports"""
    data = api_get(token, f"{API_TOPO}/opticaldevices/{args.id}/ports")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            p = obj.get("devicePort", obj)
            rows.append({
                "id":            p.get("id", ""),
                "number":        p.get("number", ""),
                "position":      p.get("position", ""),
                "connectorType": p.get("connectorType", ""),
                "flow":          p.get("flow", ""),
            })
        print_table(rows, ["id", "number", "position", "connectorType", "flow"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_rtus(args, token):
    """GET /api/topology/remotetestunits"""
    data = api_get(token, f"{API_TOPO}/remotetestunits")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            d = obj.get("opticalDevice", obj.get("remoteTestUnit", obj))
            print("d:", d)
            rows.append({
                "id":   d.get("id", ""),
                "name": d.get("name", ""),
                "type": d.get("type", ""),
            })
        print_table(rows, ["id", "name", "type"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_rtu_modules(args, token):
    """GET /api/topology/remotetestunits/{id}/modules"""
    if not args.rtu_id:
        print("[FAIL] --rtu-id es obligatorio.")
        sys.exit(1)

    data = api_get(token, f"{API_TOPO}/remotetestunits/{args.rtu_id}/modules")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            m = obj.get("module", obj)
            print("m:", m)
            rows.append({
                "id":           m.get("id", ""),
                "type":         m.get("type", ""),
                "serialNumber": m.get("serialNumber", ""),
                "rtuId":        m.get("rtuId", ""),
            })
        print_table(rows, ["id", "type", "serialNumber", "rtuId"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_rtu_ports(args, token):
    """GET /api/topology/remotetestunits/{id}/ports"""
    if not args.rtu_id:
        print("[FAIL] --rtu-id es obligatorio.")
        sys.exit(1)

    data = api_get(token, f"{API_TOPO}/remotetestunits/{args.rtu_id}/ports")
    print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_route_notes(args, token):
    """GET /api/topology/opticalroutes/{id}/notes"""
    params = {}
    if args.last:
        params["last"] = args.last

    data = api_get(token, f"{API_TOPO}/opticalroutes/{args.id}/notes", params)
    print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_route_testsetups(args, token):
    """GET /api/topology/opticalroutes/{id}/testsetups"""
    data = api_get(token, f"{API_TOPO}/opticalroutes/{args.id}/testsetups")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            ts = obj.get("testSetup", obj)
            rows.append({
                "id":                  ts.get("id", ""),
                "name":                ts.get("name", ""),
                "supportedTestType":   ts.get("supportedTestType", ""),
            })
        print_table(rows, ["id", "name", "supportedTestType"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_diagrams(args, token):
    """GET /api/topology/diagrams"""
    data = api_get(token, f"{API_TOPO}/diagrams")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            d = obj.get("diagram", obj)
            rows.append({
                "id":          d.get("id", ""),
                "name":        d.get("name", ""),
                "description": str(d.get("description") or "")[:50],
            })
        print_table(rows, ["id", "name", "description"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_sites(args, token):
    """GET /api/topology/sites"""
    data = api_get(token, f"{API_TOPO}/sites")

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            s = obj.get("site", obj)
            rows.append({
                "id":   s.get("id", ""),
                "name": s.get("name", ""),
                "type": s.get("type", ""),
            })
        print_table(rows, ["id", "name", "type"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def _data_get(token: str, url: str, params: dict | None = None) -> requests.Response:
    """GET autenticado contra el servicio de mediciones (data.fms.local)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }
    try:
        return requests.get(url, headers=headers, params=params, verify=False, timeout=30)  # nosec B501  # nosemgrep: python.requests.security.disabled-cert-validation.disabled-cert-validation — cert self-signed del servidor FMS interno
    except requests.exceptions.ConnectionError as e:
        print(f"[FAIL] Sin conexión al servicio de mediciones: {e}")
        sys.exit(1)


def _data_check(r: requests.Response):
    if r.status_code == 401:
        print("[FAIL] Token rechazado (401) en el servicio de mediciones.")
        sys.exit(1)
    if not r.ok:
        print(f"[FAIL] HTTP {r.status_code} — {r.url}")
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text[:400])
        sys.exit(1)


def cmd_results(args, token):
    """GET /v1/results (data.fms.local) — mediciones con filtros OData.
    $filter es obligatorio: al menos --route-id debe estar presente."""
    if not args.route_id and not args.promise_id:
        print("[FAIL] Debes indicar al menos --route-id <ID> para filtrar las mediciones.")
        sys.exit(1)

    filters = []
    if args.route_id:
        filters.append(f"metadata/AssetId eq {args.route_id}")
    if args.test_type:
        filters.append(f"metadata/TestType eq '{args.test_type}'")
    if args.test_category:
        filters.append(f"metadata/TestCategory eq '{args.test_category}'")
    if args.promise_id:
        filters.append(f"metadata/PromiseId eq '{args.promise_id}'")
    if args.fault_status:
        filters.append(f"metadata/FaultStatus eq '{args.fault_status}'")

    params = {"$filter": " and ".join(filters), "$orderby": "metadata/TestTime desc"}
    if args.top:
        params["$top"] = args.top

    r = _data_get(token, f"{API_DATA}/v1/results", params)
    _data_check(r)
    data = r.json()

    if args.output == "table":
        total = data.get("count", "?")
        print(f"Total de mediciones: {total}\n")
        rows = []
        for res in data.get("results", []):
            m = res.get("metadata", {})
            brief = res.get("brief", {})
            lr_results = brief.get("LinkResults", {}).get("Results", [{}])
            first_wl = lr_results[0] if lr_results else {}
            rows.append({
                "resultId":     res.get("resultid", ""),
                "TestTime":     m.get("TestTime", "")[:19].replace("T", " "),
                "TestType":     m.get("TestType", ""),
                "TestCategory": m.get("TestCategory", ""),
                "FaultStatus":  m.get("FaultStatus", ""),
                "Loss(dB)":     first_wl.get("Loss", ""),
                "λ(nm)":        first_wl.get("Wavelength", ""),
            })
        print_table(rows, ["resultId", "TestTime", "TestType", "TestCategory", "FaultStatus", "Loss(dB)", "λ(nm)"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


def cmd_result_related(args, token):
    """GET /v1/results/{resultId}/relatedresults"""
    r = _data_get(token, f"{API_DATA}/v1/results/{args.result_id}/relatedresults")
    _data_check(r)
    data = r.json()
    print_json(data)
    if args.save:
        save_output(data, args.save)


def _meta_from(route_meta: dict, trace_meta: dict) -> dict:
    category = trace_meta.get("TestCategory", route_meta.get("TestCategory", ""))
    return {
        "OpticalRouteName": route_meta.get("AssetName", ""),
        "OpticalRouteId":   route_meta.get("AssetId", ""),
        "TestTime":         trace_meta.get("TestTime", ""),
        "TestCategory":     category,
        "IsBaseline":       category in ("Baseline", "VeryFirstReference"),
    }


def _fetch_result_metadata(token: str, route_id: str, target_result_id: str) -> dict | None:
    """Busca, entre las mediciones de la ruta, la que corresponde a target_result_id
    (directamente o como RelatedResults de una medición iOLM) y devuelve su metadata:
    nombre de la ruta, fecha de la traza y si es baseline."""
    params = {
        "$filter": f"metadata/AssetId eq {route_id}",
        "$orderby": "metadata/TestTime desc",
        "$top": 200,
    }
    r = _data_get(token, f"{API_DATA}/v1/results", params)
    _data_check(r)
    data = r.json()

    for res in data.get("results", []):
        m = res.get("metadata", {})
        if res.get("resultid") == target_result_id:
            return _meta_from(m, m)
        for rel in m.get("RelatedResults", []):
            if rel.get("ResultId") == target_result_id:
                return _meta_from(m, rel)
    return None


def cmd_result_sor(args, token):
    """GET /v1/results/otdr/sorfile — traza OTDR en Base64 o binario"""
    params = {"OtdrResultId": args.otdr_result_id}
    if args.iolm_result_id:
        params["IolmResultId"] = args.iolm_result_id
    params["formatOption"] = args.format

    r = _data_get(token, f"{API_DATA}/v1/results/otdr/sorfile", params)
    _data_check(r)

    meta = None
    if args.route_id:
        meta = _fetch_result_metadata(token, args.route_id, args.otdr_result_id)
        if meta is None:
            print("[WARN] No se encontró metadata para este resultId en la ruta indicada.")

    if args.save:
        if args.format == "Base64":
            Path(args.save).write_text(r.text)
        else:
            Path(args.save).write_bytes(r.content)
        print(f"[+] Traza SOR guardada en: {args.save}  ({len(r.content)} bytes)")
        if meta:
            meta_path = f"{args.save}.meta.json"
            Path(meta_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            print(f"[+] Metadata asociada guardada en: {meta_path}")
    else:
        if args.format == "Base64":
            preview = r.text[:800]
            print(preview + ("\n[...truncado, usa --save para el archivo completo]" if len(r.text) > 800 else ""))
        else:
            print(f"[INFO] Archivo binario de {len(r.content)} bytes. Usa --save FILE para guardarlo.")


def cmd_result_pdf(args, token):
    """GET /v1/results/pdf — reporte PDF de una medición OTDR"""
    r = _data_get(token, f"{API_DATA}/v1/results/pdf", {"resultId": args.result_id})
    _data_check(r)

    out = args.save or f"otdr_report_{args.result_id[:8]}.pdf"
    Path(out).write_bytes(r.content)
    print(f"[+] PDF guardado en: {out}  ({len(r.content)} bytes)")


def cmd_testconfigs(args, token):
    """GET /api/topology/testconfigurations"""
    params = {}
    if args.monitoring_type: params["monitoringType"] = args.monitoring_type
    if args.test_category:   params["testCategory"]   = args.test_category

    data = api_get(token, f"{API_TOPO}/testconfigurations", params)

    if args.output == "table":
        rows = []
        for obj in _objects(data):
            tc = obj.get("testConfiguration", obj) if isinstance(obj, dict) else obj
            if isinstance(tc, dict):
                rows.append({
                    "id":             tc.get("id", ""),
                    "name":           tc.get("name", ""),
                    "monitoringType": tc.get("monitoringType", ""),
                    "isDefault":      tc.get("isDefault", ""),
                })
            else:
                rows.append({"id": "", "name": str(tc)[:60], "monitoringType": "", "isDefault": ""})
        print_table(rows, ["id", "name", "monitoringType", "isDefault"])
    else:
        print_json(data)

    if args.save:
        save_output(data, args.save)


# ── Parser ────────────────────────────────────────────────────────────────────

EPILOG = """
Ejemplos:
  python main.py optical-routes --output table
  python main.py optical-routes --name "ICA" --page-size 20 --page 1
  python main.py optical-route --id 41
  python main.py optical-devices --output table
  python main.py optical-devices --name "EA" --output table --save devices.json
  python main.py device-ports --id 13 --output table
  python main.py rtus --output table
  python main.py rtu-modules --rtu-id 13 --output table
  python main.py rtu-ports --rtu-id 13
  python main.py route-notes --id 41
  python main.py route-testsetups --id 41 --output table
  python main.py diagrams --output table
  python main.py sites --output table
  python main.py testconfigs --output table
  python main.py testconfigs --monitoring-type pon

  # Mediciones / trazas OTDR
  python main.py results --route-id 41 --output table
  python main.py results --route-id 41 --top 1
  python main.py results --route-id 41 --test-type OTDR --top 10 --output table
  python main.py results --route-id 41 --test-category Baseline --top 5 --output table
  python main.py results --route-id 41 --has-fault true --output table
  python main.py result-related --result-id <UUID>
  python main.py result-sor --otdr-result-id <UUID> --format Base64
  python main.py result-sor --otdr-result-id <UUID> --format Binary --save traza.sor
  python main.py result-pdf --result-id <UUID> --save reporte.pdf
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="CLI de consulta (solo lectura) para la API REST de EXFO FMS 8.5\n"
                    f"API host: {FMS_BASE}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--output", choices=["json", "table"], default="json",
        help="Formato de salida: json (default) o table",
    )
    common.add_argument(
        "--save", metavar="FILE",
        help="Guarda la respuesta JSON en FILE",
    )

    sub = parser.add_subparsers(dest="cmd", metavar="COMANDO")
    sub.required = True

    # ── optical-routes ────────────────────────────────────────────────────────
    p = sub.add_parser(
        "optical-routes", parents=[common],
        help="Listar rutas ópticas (con filtro opcional por nombre)",
        description="GET /api/topology/opticalroutes\n"
                    "Soporta filtro por nombre parcial (contains), paginación.",
    )
    p.add_argument("--name",      help="Nombre parcial o completo de la ruta óptica")
    p.add_argument("--page-size", type=int, metavar="N", help="Resultados por página")
    p.add_argument("--page",      type=int, metavar="N", help="Número de página")

    # ── optical-route ─────────────────────────────────────────────────────────
    p = sub.add_parser(
        "optical-route", parents=[common],
        help="Ver detalle de una ruta óptica por ID",
        description="GET /api/topology/opticalroutes/{id}",
    )
    p.add_argument("--id", required=True, metavar="ID", help="ID de la ruta óptica")

    # ── optical-devices ───────────────────────────────────────────────────────
    p = sub.add_parser(
        "optical-devices", parents=[common],
        help="Listar dispositivos ópticos (RTUs, patch panels, splitters, etc.)",
        description="GET /api/topology/opticaldevices\n"
                    "Filtro por nombre usa modo 'begins-with'. Soporta paginación.",
    )
    p.add_argument("--name",      help="Prefijo del nombre del dispositivo")
    p.add_argument("--ids",       help="IDs separados por coma")
    p.add_argument("--page-size", type=int, metavar="N", help="Resultados por página")
    p.add_argument("--page",      type=int, metavar="N", help="Número de página (empieza en 1)")

    # ── device-ports ──────────────────────────────────────────────────────────
    p = sub.add_parser(
        "device-ports", parents=[common],
        help="Listar puertos de un dispositivo óptico",
        description="GET /api/topology/opticaldevices/{id}/ports",
    )
    p.add_argument("--id", required=True, metavar="ID", help="ID del dispositivo óptico")

    # ── rtus ──────────────────────────────────────────────────────────────────
    sub.add_parser(
        "rtus", parents=[common],
        help="Listar Remote Test Units (RTUs) registradas en FMS",
        description="GET /api/topology/remotetestunits",
    )

    # ── rtu-modules ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "rtu-modules", parents=[common],
        help="Listar módulos instalados en un RTU",
        description="GET /api/topology/remotetestunits/{id}/modules\n"
                    "Requiere el ID del RTU (obtenible con el comando rtus).",
    )
    p.add_argument("--rtu-id", required=True, metavar="ID", help="ID del RTU")

    # ── rtu-ports ─────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "rtu-ports", parents=[common],
        help="Listar puertos de un RTU",
        description="GET /api/topology/remotetestunits/{id}/ports",
    )
    p.add_argument("--rtu-id", required=True, metavar="ID", help="ID del RTU")

    # ── route-notes ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "route-notes", parents=[common],
        help="Ver notas de una ruta óptica",
        description="GET /api/topology/opticalroutes/{id}/notes",
    )
    p.add_argument("--id",   required=True, metavar="ROUTE_ID", help="ID de la ruta óptica")
    p.add_argument("--last", type=int, metavar="N", help="Retornar solo las últimas N notas")

    # ── route-testsetups ──────────────────────────────────────────────────────
    p = sub.add_parser(
        "route-testsetups", parents=[common],
        help="Ver test setups configurados en una ruta óptica",
        description="GET /api/topology/opticalroutes/{id}/testsetups",
    )
    p.add_argument("--id", required=True, metavar="ROUTE_ID", help="ID de la ruta óptica")

    # ── diagrams ──────────────────────────────────────────────────────────────
    sub.add_parser(
        "diagrams", parents=[common],
        help="Listar diagramas (agrupadores de RTUs y rutas)",
        description="GET /api/topology/diagrams",
    )

    # ── sites ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "sites", parents=[common],
        help="Listar sitios físicos registrados en FMS",
        description="GET /api/topology/sites",
    )

    # ── testconfigs ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "testconfigs", parents=[common],
        help="Listar configuraciones de test (plantillas OTDR/iOLM)",
        description="GET /api/topology/testconfigurations\n"
                    "Filtra plantillas de test por tipo de monitoreo y categoría.",
    )
    p.add_argument("--monitoring-type", choices=["p2p", "pon"],
                   help="Tipo de monitoreo: p2p o pon")
    p.add_argument("--test-category",  choices=["monitoring", "adhoc"],
                   help="Categoría del test")

    # ── results ───────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "results", parents=[common],
        help="Listar mediciones OTDR/iOLM con filtros OData",
        description="GET /api/topology/Results/get_results\n"
                    "Soporta filtros OData: por ruta, tipo de test, categoría, fallo y más.",
    )
    p.add_argument("--route-id",       metavar="ID",  help="ID de la ruta óptica (AssetId)")
    p.add_argument("--test-type",      choices=["OTDR", "iOLM"], help="Tipo de test")
    p.add_argument("--test-category",  metavar="CAT",
                   help="Categoría: Baseline, Monitoring, VeryFirstReference, Adhoc, etc.")
    p.add_argument("--promise-id",     metavar="UUID", help="PromiseId de una medición concreta")
    p.add_argument("--fault-status",   choices=["Detected", "Cleared"],
                   help="Filtrar por estado de fallo: Detected o Cleared")
    p.add_argument("--top",            type=int, metavar="N", help="Limitar a N resultados")
    p.add_argument("--select",         metavar="CAMPOS",
                   help="Proyección OData, ej: resultid,metadata,status")

    # ── result-related ────────────────────────────────────────────────────────
    p = sub.add_parser(
        "result-related", parents=[common],
        help="Resultados relacionados a una medición (baseline, VeryFirstReference…)",
        description="GET /api/topology/Results/get_results/{resultId}/relatedresults\n"
                    "Para OTDR/iOLM devuelve Baseline y VeryFirstReference más recientes.\n"
                    "Para iOLM PON devuelve también Baseline, RLNulling y medidas relacionadas.",
    )
    p.add_argument("--result-id", required=True, metavar="UUID", help="ID (UUID) de la medición")

    # ── result-sor ────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "result-sor", parents=[common],
        help="Descargar traza OTDR (.sor) en Base64 o binario",
        description="GET /v1/results/otdr/sorfile\n"
                    "Descarga el archivo .sor con la traza OTDR completa (puntos de reflexión,\n"
                    "atenuación por sección, eventos). Para resultados iOLM se extrae la traza\n"
                    "OTDR embebida indicando --iolm-result-id y --otdr-result-id.",
    )
    p.add_argument("--otdr-result-id", required=True, metavar="UUID",
                   help="ID (UUID) del resultado OTDR")
    p.add_argument("--iolm-result-id", metavar="UUID",
                   help="ID (UUID) del resultado iOLM (solo si la traza viene de un test iOLM)")
    p.add_argument("--route-id", metavar="ID",
                   help="ID de la ruta óptica (AssetId). Si se indica junto con --save, "
                        "se busca y guarda la metadata asociada (nombre de ruta, fecha de la "
                        "traza, baseline) en <archivo>.meta.json")
    p.add_argument("--format",         choices=["Base64", "Binary"], default="Base64",
                   help="Formato de salida: Base64 (default) o Binary")

    # ── result-pdf ────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "result-pdf", parents=[common],
        help="Descargar reporte PDF de una medición OTDR",
        description="GET /v1/results/pdf\n"
                    "Genera y descarga el informe PDF del resultado OTDR indicado.",
    )
    p.add_argument("--result-id", required=True, metavar="UUID", help="ID (UUID) de la medición OTDR")

    return parser


# ── Dispatch ──────────────────────────────────────────────────────────────────

_DISPATCH = {
    "optical-routes":    cmd_optical_routes,
    "optical-route":     cmd_optical_route,
    "optical-devices":   cmd_optical_devices,
    "device-ports":      cmd_device_ports,
    "rtus":              cmd_rtus,
    "rtu-modules":       cmd_rtu_modules,
    "rtu-ports":         cmd_rtu_ports,
    "route-notes":       cmd_route_notes,
    "route-testsetups":  cmd_route_testsetups,
    "diagrams":          cmd_diagrams,
    "sites":             cmd_sites,
    "testconfigs":       cmd_testconfigs,
    "results":           cmd_results,
    "result-related":    cmd_result_related,
    "result-sor":        cmd_result_sor,
    "result-pdf":        cmd_result_pdf,
}


def main():
    parser = build_parser()
    args   = parser.parse_args()
    token  = get_access_token()
    _DISPATCH[args.cmd](args, token)


if __name__ == "__main__":
    main()

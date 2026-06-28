#!/usr/bin/env python3
"""
sor_viewer.py — Visualizador de trazas OTDR (.sor / iOLM)

Soporta dos formatos EXFO:
  - SOR estándar Bellcore SR-4731 (pyotdr)
  - iOLM OLE2 (.sor con contenedor EXFO propietario)

Uso:
  python src/sor_viewer.py <archivo.sor>
  python src/sor_viewer.py <archivo.sor> --save grafica.png
"""

import argparse
import gzip
import json
import math
import struct
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ── Detección de formato ───────────────────────────────────────────────────────

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _is_ole2(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(8) == _OLE2_MAGIC


def _load_sidecar_meta(path: Path) -> dict | None:
    """Carga la metadata generada por `main.py result-sor --route-id ...`
    (nombre de ruta óptica, fecha de la traza, baseline) desde <path>.meta.json,
    si existe junto al archivo .sor."""
    meta_path = Path(f"{path}.meta.json")
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ── Monkey-patch pyotdr: usa latin-1 en lugar de utf-8 ────────────────────────

def _patch_pyotdr():
    import pyotdr.parts as _parts
    def _get_string_latin1(fh):
        mystr = b""
        byte = fh.read(1)
        while byte != "":
            tt = struct.unpack("c", byte)[0]
            if tt == b"\x00":
                break
            mystr += tt
            byte = fh.read(1)
        return mystr.decode("latin-1")
    _parts.get_string = _get_string_latin1


# ── Extracción de arrays del formato .NET BinaryFormatter ─────────────────────

def _find_single_arrays(data: bytes, min_count: int = 200) -> list[tuple]:
    """Busca ArraySinglePrimitive de tipo Single (float32) en datos .NET BF."""
    results = []
    i = 0
    while i < len(data) - 10:
        if data[i] == 0x0F:
            try:
                obj_id = struct.unpack("<I", data[i+1:i+5])[0]
                count  = struct.unpack("<I", data[i+5:i+9])[0]
                ptype  = data[i+9]
            except Exception:
                i += 1
                continue
            if ptype == 11 and min_count <= count <= 200000 and 0 < obj_id < 200000:
                ds = i + 10
                de = ds + count * 4
                if de <= len(data):
                    vals = struct.unpack(f"<{count}f", data[ds:de])
                    results.append((obj_id, count, vals))
        i += 1
    return results


def _best_trace_array(arrays: list[tuple]) -> tuple | None:
    """Elige el array más probable de ser la traza OTDR principal:
    mayor cantidad de muestras con valores positivos y rango decreciente."""
    best = None
    best_score = -1
    for obj_id, count, vals in arrays:
        pos = sum(1 for v in vals if v > 0)
        if pos < count * 0.7:
            continue
        vmax = max(vals)
        vmin = min(v for v in vals if v > 0)
        if vmax <= 0:
            continue
        dyn_range_db = 10 * math.log10(vmax / vmin) if vmin > 0 else 0
        score = count * (dyn_range_db / 40)
        if score > best_score:
            best_score = score
            best = (obj_id, count, vals)
    return best


def _to_db(vals: tuple) -> list[float]:
    """Convierte muestras lineales a nivel relativo en dB.
    Referencia = pico máximo → 0 dB arriba, valores negativos debajo (display OTDR estándar)."""
    vref = max((v for v in vals if v > 0), default=None)
    if vref is None:
        return [float("nan")] * len(vals)
    out = []
    for v in vals:
        if v > 0:
            out.append(10 * math.log10(v / vref))
        else:
            out.append(float("nan"))
    return out


def _find_fiber_length_km(data: bytes) -> float | None:
    """Busca un double plausible como longitud total de fibra en OlmData."""
    # Buscar doubles entre 0.5 y 500 km, alineados a 8 bytes, después de offset 0x5000
    for i in range(0x5000, len(data) - 8, 8):
        v = struct.unpack("<d", data[i:i+8])[0]
        if 0.5 <= v <= 500:
            return v
    return None


# ── Ruta OLE2 / EXFO iOLM ─────────────────────────────────────────────────────

def _plot_olm(path: Path, save_path: str | None, meta: dict | None):
    import olefile

    if not olefile.isOleFile(str(path)):
        print("[FAIL] El archivo no es OLE2 válido.")
        sys.exit(1)

    ole = olefile.OleFileIO(str(path))

    # Longitud de fibra desde OlmData
    fiber_km: float | None = None
    if ole.exists("OlmData"):
        try:
            raw_olm = ole.openstream("OlmData").read()
            olm_data = gzip.decompress(raw_olm)
            fiber_km = _find_fiber_length_km(olm_data)
        except Exception:
            pass

    # Extraer todas las trazas
    traces: dict[str, tuple] = {}
    for i in range(20):
        name = f"OlmSPTraces/{i}"
        if not ole.exists(name):
            break
        try:
            raw  = ole.openstream(name).read()
            data = gzip.decompress(raw)
            arrays = _find_single_arrays(data)
            best = _best_trace_array(arrays)
            if best:
                traces[name] = best
        except Exception:
            continue

    ole.close()

    if not traces:
        print("[FAIL] No se encontraron trazas en el archivo iOLM.")
        sys.exit(1)

    # Elegir la traza principal (mayor número de muestras con buen rango)
    main_name = max(traces, key=lambda k: traces[k][1])
    main_oid, main_count, main_vals = traces[main_name]

    # Eje x: distancia en km
    if fiber_km and fiber_km > 0:
        dx_km = fiber_km / main_count
    else:
        # Fallback: IOR=1.4682, T_s estimado para obtener rango coherente
        dx_km = 22.0 / main_count  # estimado conservador

    xs = [i * dx_km for i in range(main_count)]
    ys = _to_db(main_vals)

    _render_plot(
        path        = path,
        xs          = xs,
        ys          = ys,
        traces      = traces,
        fiber_km    = fiber_km,
        main_name   = main_name,
        fmt_label   = "EXFO iOLM (OLE2)",
        save_path   = save_path,
        meta        = meta,
    )


# ── Ruta SOR estándar (pyotdr) ────────────────────────────────────────────────

def _parse_trace_pts(tracedata: list) -> tuple[list, list]:
    xs, ys = [], []
    for line in tracedata:
        parts = line.strip().split("\t")
        if len(parts) == 2:
            try:
                xs.append(float(parts[0]))
                ys.append(float(parts[1]))
            except ValueError:
                pass
    return xs, ys


def _parse_events(results: dict) -> list[dict]:
    ke = results.get("KeyEvents", {})
    n  = ke.get("num events", 0)
    events = []
    for i in range(1, n + 1):
        ev = ke.get(f"event {i}", {})
        if not ev:
            continue
        try:
            events.append({
                "index":  i,
                "dist":   float(ev.get("distance", 0)),
                "splice": float(ev.get("splice loss", 0)),
                "refl":   float(ev.get("refl loss", 0)),
                "slope":  float(ev.get("slope", 0)),
                "type":   ev.get("type", ""),
            })
        except (ValueError, TypeError):
            continue
    return events


def _plot_sor(path: Path, save_path: str | None, meta: dict | None):
    _patch_pyotdr()
    from pyotdr import sorparse

    print(f"[*] Leyendo SOR estándar: {path.name} ...")
    status, results, tracedata = sorparse(str(path))

    if status != "ok" or results is None:
        print(f"[FAIL] No se pudo parsear el archivo SOR: {status}")
        sys.exit(1)

    xs, ys = _parse_trace_pts(tracedata)
    if not xs:
        print("[FAIL] No se encontraron puntos de traza.")
        sys.exit(1)

    events = _parse_events(results)
    fp   = results.get("FxdParams", {})
    ke   = results.get("KeyEvents", {})
    sm   = ke.get("Summary", {})

    _render_plot(
        path      = path,
        xs        = xs,
        ys        = ys,
        traces    = {},
        fiber_km  = None,
        main_name = None,
        fmt_label = "SOR estándar (Bellcore SR-4731)",
        save_path = save_path,
        events    = events,
        wl        = fp.get("wavelength", "—"),
        rng       = fp.get("range",      "—"),
        total_loss= sm.get("total loss", "—"),
        orl       = sm.get("ORL",        "—"),
        meta      = meta,
    )


# ── Render común ──────────────────────────────────────────────────────────────

def _event_color(etype: str) -> str:
    t = etype.lower()
    if "reflection" in t: return "#e74c3c"
    if "loss" in t or "drop" in t: return "#e67e22"
    if "end" in t or "eot" in t or "9999" in t: return "#8e44ad"
    return "#2980b9"


def _render_plot(
    path, xs, ys, traces, fiber_km, main_name, fmt_label, save_path,
    events=None, wl="—", rng="—", total_loss="—", orl="—", meta=None,
):
    events = events or []

    fig = plt.figure(figsize=(14, 9))
    fig.patch.set_facecolor("#1a1a2e")

    has_events = bool(events)
    ax     = fig.add_axes([0.07, 0.32 if has_events else 0.10, 0.88, 0.58 if has_events else 0.80])
    ax.set_facecolor("#16213e")

    # ── Traza principal ────────────────────────────────────────────────────────
    ys_clean = [v if not math.isnan(v) else float("nan") for v in ys]
    ax.plot(xs, ys_clean, color="#00d4ff", linewidth=0.8, zorder=2)
    _ys_valid = [v for v in ys_clean if not math.isnan(v)]
    _y_floor  = min(_ys_valid) - 2 if _ys_valid else -50
    ax.fill_between(xs, ys_clean, _y_floor, color="#00d4ff", alpha=0.05)

    # Overlay de otras trazas OLM (en gris tenue)
    if traces and main_name:
        others = [(k, v) for k, v in traces.items() if k != main_name]
        for i, (name, (oid, cnt, vals)) in enumerate(others[:6]):
            dxo = (fiber_km or 22) / cnt
            xo  = [j * dxo for j in range(cnt)]
            yo  = _to_db(vals)
            ax.plot(xo, yo, color="#444466", linewidth=0.4, alpha=0.5, zorder=1)

    # ── Formato de ejes ────────────────────────────────────────────────────────
    ax.set_xlabel("Distancia (km)", color="#cccccc", fontsize=10)
    ax.set_ylabel("Pérd. relativa (dB)", color="#cccccc", fontsize=10)
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    ax.xaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(True, which="major", color="#2d3561", linewidth=0.5)
    ax.grid(True, which="minor", color="#1e2545", linewidth=0.3)
    for spine in ax.spines.values():
        spine.set_edgecolor("#2d3561")

    # ── Marcadores de eventos (solo SOR estándar) ─────────────────────────────
    for ev in events:
        dist  = ev["dist"]
        color = _event_color(ev["type"])
        ax.axvline(x=dist, color=color, linewidth=0.7, alpha=0.7, linestyle="--", zorder=3)
        import bisect
        idx = min(bisect.bisect_left(xs, dist), len(ys_clean) - 1)
        y_pt = ys_clean[idx]
        if not math.isnan(y_pt):
            ax.plot(dist, y_pt, "o", color=color, markersize=5, zorder=4)
            ax.annotate(
                f"E{ev['index']}", xy=(dist, y_pt),
                xytext=(dist + 0.02 * (xs[-1] - xs[0]) / 10, y_pt - 0.5),
                fontsize=7, color=color, ha="left",
            )

    # ── Info en esquina ────────────────────────────────────────────────────────
    if fiber_km:
        info = f"Longitud fibra: {fiber_km:.3f} km\nMuestras: {len(xs)}"
    else:
        info = f"λ {wl}   Rango {rng}\nPérd. {total_loss} dB   ORL {orl} dB"

    ax.text(
        0.99, 0.03, info, transform=ax.transAxes,
        fontsize=8, color="#aaddff", va="bottom", ha="right",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#0f3460", alpha=0.8),
    )

    # Nota de formato
    ax.text(
        0.01, 0.03, f"Formato: {fmt_label}", transform=ax.transAxes,
        fontsize=7, color="#666688", va="bottom", ha="left",
    )

    ax.set_title(f"Traza OTDR — {path.name}", color="#ffffff", fontsize=12, pad=8)

    # ── Metadata de la medición (ruta óptica, fecha, baseline) ───────────────
    if meta:
        route_name = meta.get("OpticalRouteName") or "—"
        test_time  = (meta.get("TestTime") or "—")[:19].replace("T", " ")
        category   = meta.get("TestCategory") or "—"
        is_baseline = bool(meta.get("IsBaseline"))

        ax.text(
            0.5, 1.085, f"{route_name}   ·   {test_time}",
            transform=ax.transAxes, fontsize=9.5, color="#aaddff",
            ha="center", va="bottom",
        )

        badge_text  = "BASELINE" if is_baseline else category
        badge_color = "#f1c40f" if is_baseline else "#2980b9"
        ax.text(
            0.99, 1.085, badge_text,
            transform=ax.transAxes, fontsize=8, color="#1a1a2e" if is_baseline else "#ffffff",
            fontweight="bold", ha="right", va="bottom",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=badge_color, edgecolor="none"),
        )

    # ── Tabla de eventos (SOR estándar) ───────────────────────────────────────
    if has_events:
        ax_tbl = fig.add_axes([0.07, 0.04, 0.88, 0.24])
        ax_tbl.axis("off")
        col_labels = ["#", "Dist (km)", "Tipo", "Pérd. empalme (dB)", "Refl (dB)", "Pendiente (dB/km)"]
        table_data = [
            [str(ev["index"]), f"{ev['dist']:.3f}",
             ev["type"].split("\x00")[0][:30],
             f"{ev['splice']:.3f}", f"{ev['refl']:.3f}", f"{ev['slope']:.3f}"]
            for ev in events
        ]
        tbl = ax_tbl.table(cellText=table_data, colLabels=col_labels,
                           loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.3)
        for (row, col), cell in tbl.get_celld().items():
            cell.set_edgecolor("#2d3561")
            if row == 0:
                cell.set_facecolor("#0f3460")
                cell.set_text_props(color="#00d4ff", fontweight="bold")
            else:
                cell.set_facecolor("#16213e" if row % 2 == 0 else "#1a1a2e")
                cell.set_text_props(color="#dddddd")
                if col == 0:
                    color = _event_color(events[row-1]["type"])
                    cell.set_text_props(color=color, fontweight="bold")
        ax_tbl.set_title("Eventos clave", color="#aaaaaa", fontsize=9, pad=4)

    # ── Leyenda de streams OLM ────────────────────────────────────────────────
    if traces:
        n_streams = len(traces)
        ax.text(
            0.01, 0.97,
            f"{n_streams} trazas extraídas — mostrando: {main_name}",
            transform=ax.transAxes, fontsize=8,
            color="#88aacc", va="top", ha="left",
        )

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        print(f"[+] Gráfica guardada en: {save_path}")
    else:
        plt.show()


# ── Punto de entrada ──────────────────────────────────────────────────────────

def plot_sor(sor_file: str, save_path: str | None = None):
    path = Path(sor_file)
    if not path.exists():
        print(f"[FAIL] Archivo no encontrado: {sor_file}")
        sys.exit(1)

    meta = _load_sidecar_meta(path)
    if meta:
        print(f"[*] Metadata asociada encontrada: {meta.get('OpticalRouteName', '—')} "
              f"({meta.get('TestCategory', '—')})")

    print(f"[*] Detectando formato de {path.name} ...")
    if _is_ole2(path):
        print("[*] Formato OLE2/EXFO iOLM detectado")
        _plot_olm(path, save_path, meta)
    else:
        print("[*] Formato SOR estándar (Bellcore SR-4731) detectado")
        _plot_sor(path, save_path, meta)


def main():
    parser = argparse.ArgumentParser(
        description="Visualizador de trazas OTDR (.sor / iOLM) — EXFO FMS",
    )
    parser.add_argument("sor_file", metavar="ARCHIVO.sor",
                        help="Ruta al archivo .sor")
    parser.add_argument("--save", metavar="IMAGEN",
                        help="Guardar la gráfica en archivo (PNG, PDF, SVG…)")
    args = parser.parse_args()
    plot_sor(args.sor_file, save_path=args.save)


if __name__ == "__main__":
    main()

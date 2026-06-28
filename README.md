# EXFO FMS — CLI de consulta REST

Herramienta de línea de comandos de **solo lectura** para consultar la API REST del servidor EXFO FMS 8.5.

- **API host:** `https://api.fms.local`
- **Autenticación:** Keycloak (realm `Fiber`) con Direct Access Grants / TOTP

---

## Estructura del proyecto

```
analitica/
├── src/
│   ├── fms_auth.py    # Módulo de autenticación (token cache + refresh + MFA)
│   ├── main.py        # CLI principal
│   └── sor_viewer.py  # Visualizador de trazas OTDR/.sor → PNG
├── fms_token.json    # Caché del token (generado en tiempo de ejecución)
├── requirements.txt   # Dependencias del proyecto
├── .env               # Credenciales (no incluido en el repo)
├── .env.example       # Plantilla de variables de entorno
├── FMS-API_Guide_8.5.pdf
└── README.md
```

---

## Requisitos

- Python 3.10+
- Entorno virtual con las dependencias listadas en `requirements.txt` (`requests` y `python-dotenv` para el CLI; `matplotlib`, `pyotdr` y `olefile` para `sor_viewer.py`)

### Verificar la versión de Python

Antes de crear el entorno virtual, confirma que tienes Python 3.10 o superior:

```bash
python3 --version
```

Si la versión reportada es menor a `3.10`, actualízala según tu distribución antes de continuar:

**Debian / Ubuntu (apt):**

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa   # solo Ubuntu; en Debian usa backports si no aplica
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

**RHEL / CentOS / Fedora (dnf/yum):**

```bash
sudo dnf install -y epel-release   # solo si python3.12 no está en los repos base (RHEL/CentOS)
sudo dnf install -y python3.12
```

Tras instalar la versión nueva, usa explícitamente ese binario al crear el entorno virtual (sustituye `python3.12` si instalaste otra versión):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Si `python3 --version` ya reportaba 3.10+, puedes omitir el paso de actualización y crear el entorno con:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Configuración

Copia `.env.example` a `.env` y completa tus credenciales:

```bash
cp .env.example .env
```

```ini
FMS_BASE_URL=https://api.fms.local
FMS_DATA_URL=https://data.fms.local
FMS_AUTH_URL=https://auth.fms.local/auth/realms/Fiber/protocol/openid-connect/token
FMS_CLIENT_ID=fg-topologyui
FMS_USERNAME=tu_usuario
FMS_PASSWORD=tu_contraseña
```

---

## Autenticación

El módulo `src/fms_auth.py` gestiona el token automáticamente:

1. Si el token cacheado en `fms_token.json` sigue vigente, lo reutiliza.
2. Si venció pero el `refresh_token` es válido, lo renueva.
3. Si ambos expiraron, hace login completo (usuario + contraseña + TOTP si aplica).

---

## Uso general

```bash
python src/main.py <comando> [opciones]
python src/main.py --help
python src/main.py <comando> --help
```

> También puedes ejecutarlo directamente desde el directorio `src/`:
> ```bash
> cd src && python main.py <comando> [opciones]
> ```

**Opciones comunes a todos los comandos:**

| Opción | Descripción |
|---|---|
| `--output json` | Respuesta completa en JSON (por defecto) |
| `--output table` | Vista tabular resumida |
| `--save FILE` | Guarda la respuesta JSON en un archivo |

---

## Endpoints

Los comandos siguen el flujo natural de la topología: **sitios → RTUs → módulos → puertos → rutas ópticas**.

---

### 1. Sites — Sitios físicos

Lista todos los sitios físicos registrados en FMS.

```
GET /api/topology/sites
```

```bash
python src/main.py sites --output table
```

Campos devueltos: `id`, `name`, `type`

---

### 2. RTUs — Remote Test Units

Lista todas las RTUs registradas en FMS.

```
GET /api/topology/remotetestunits
```

```bash
python src/main.py rtus --output table
```

Campos devueltos: `id`, `name`, `type`

---

### 3. Módulos de una RTU

Lista los módulos instalados en una RTU específica.

```
GET /api/topology/remotetestunits/{id}/modules
```

```bash
python src/main.py rtu-modules --rtu-id <ID> --output table
```

| Argumento | Descripción |
|---|---|
| `--rtu-id ID` | ID de la RTU (obligatorio, obtenible con `rtus`) |

Campos devueltos: `id`, `type`, `serialNumber`, `rtuId`

---

### 4. Puertos

#### 4a. Puertos de una RTU

```
GET /api/topology/remotetestunits/{id}/ports
```

```bash
python src/main.py rtu-ports --rtu-id <ID>
```

#### 4b. Puertos de un dispositivo óptico

```
GET /api/topology/opticaldevices/{id}/ports
```

```bash
python src/main.py device-ports --id <ID> --output table
```

Campos devueltos: `id`, `number`, `position`, `connectorType`, `flow`

---

### 5. Rutas ópticas

#### 5a. Listar rutas ópticas

```
GET /api/topology/opticalroutes
```

```bash
# Todas las rutas
python src/main.py optical-routes --output table

# Filtrar por nombre (búsqueda parcial) con paginación
python src/main.py optical-routes --name "ICA" --page-size 20 --page 1
```

| Argumento | Descripción |
|---|---|
| `--name TEXTO` | Filtro por nombre (contiene) |
| `--page-size N` | Resultados por página |
| `--page N` | Número de página |

Campos devueltos: `id`, `name`, `type`, `description`

#### 5b. Detalle de una ruta óptica

```
GET /api/topology/opticalroutes/{id}
```

```bash
python src/main.py optical-route --id <ID>
```

#### 5c. Notas de una ruta óptica

```
GET /api/topology/opticalroutes/{id}/notes
```

```bash
python src/main.py route-notes --id <ID>
python src/main.py route-notes --id <ID> --last 5
```

#### 5d. Test setups de una ruta óptica

```
GET /api/topology/opticalroutes/{id}/testsetups
```

```bash
python src/main.py route-testsetups --id <ID> --output table
```

Campos devueltos: `id`, `name`, `supportedTestType`

---

## Mediciones y trazas OTDR

Estos comandos acceden a los resultados de los tests ejecutados por las RTUs: mediciones OTDR/iOLM con sus valores de atenuación, eventos de reflexión y archivos de traza completos.

> **Host del servicio:** `https://data.fms.local` (servicio separado del topology API)
> **Roles requeridos:** `fg-results-read` (lectura) o `fg-results-master` (acceso completo a T&M Services).

---

### 6. Listar mediciones

Consulta el histórico de mediciones de una ruta óptica. Usa filtros OData para acotar resultados.

```
GET https://data.fms.local/v1/results
```

> **`--route-id` es obligatorio** — el servicio requiere un `$filter` de assetId para responder.

```bash
# Últimas 10 mediciones de la ruta 41 (orden cronológico inverso)
python src/main.py results --route-id 41 --top 10 --output table

# Mediciones con fallo detectado
python src/main.py results --route-id 41 --fault-status Detected --output table

# Solo mediciones iOLM de Baseline
python src/main.py results --route-id 41 --test-type iOLM --test-category Baseline --top 5

# Medición por PromiseId (tras lanzar un test ad-hoc)
python src/main.py results --route-id 41 --promise-id <UUID>

# Guardar resultado completo (incluye brief con eventos y atenuaciones por sección)
python src/main.py results --route-id 41 --top 5 --save mediciones.json
```

| Argumento | Descripción |
|---|---|
| `--route-id ID` | ID de la ruta óptica — **obligatorio** |
| `--test-type` | `OTDR` o `iOLM` |
| `--test-category CAT` | `Baseline`, `Monitoring`, `VeryFirstReference`, `Adhoc` |
| `--fault-status` | `Detected` o `Cleared` |
| `--promise-id UUID` | ID de una medición concreta lanzada por API |
| `--top N` | Limitar a N resultados |

Campos en la salida tabla: `resultId`, `TestTime`, `TestType`, `TestCategory`, `FaultStatus`, `Loss(dB)`, `λ(nm)`

---

### 7. Resultados relacionados de una medición

Para una medición dada, obtiene las mediciones relacionadas: Baseline y VeryFirstReference más recientes (para OTDR/iOLM) o también RLNulling y anclas (para PON).

```
GET https://data.fms.local/v1/results/{resultId}/relatedresults
```

```bash
python src/main.py result-related --result-id <UUID>
```

Devuelve un array de `resultId`. El primero es la propia medición.

---

### 8. Traza OTDR completa (.sor)

Descarga el archivo de traza OTDR en formato propietario EXFO (`.sor`) que contiene la forma de onda, posición y atenuación de cada evento de reflexión/pérdida a lo largo de la fibra.

```
GET https://data.fms.local/v1/results/otdr/sorfile
```

```bash
# Ver traza en Base64 (para inspección rápida)
python src/main.py result-sor --otdr-result-id <UUID> --format Base64

# Guardar archivo .sor binario (para análisis con software EXFO)
python src/main.py result-sor --otdr-result-id <UUID> --format Binary --save traza.sor

# Para resultado iOLM (la traza OTDR está embebida)
python src/main.py result-sor --iolm-result-id <UUID-iOLM> --otdr-result-id <UUID-OTDR> --save traza.sor
```

| Argumento | Descripción |
|---|---|
| `--otdr-result-id UUID` | ID del resultado OTDR (obligatorio) |
| `--iolm-result-id UUID` | ID del resultado iOLM padre (solo para trazas extraídas de iOLM) |
| `--route-id ID` | ID de la ruta óptica (AssetId). Junto con `--save`, busca y guarda la metadata asociada |
| `--format` | `Base64` (default, texto) o `Binary` (archivo .sor nativo) |
| `--save FILE` | Guardar en archivo; obligatorio para formato Binary |

> Para obtener los UUIDs de resultados OTDR asociados a un iOLM, primero usa `result-related` y luego selecciona los de `TestType=OTDR`.

**Metadata asociada (`--route-id`)**

Si se indica `--route-id` junto con `--save`, se consulta `/v1/results` filtrado por esa ruta
para localizar el nombre del optical route, la fecha de la traza y la categoría del test
(`TestCategory`), y se guarda como JSON junto al `.sor` en `<archivo>.meta.json`:

```bash
python src/main.py result-sor --otdr-result-id <UUID> --route-id 41 \
    --format Binary --save traza.sor
# → traza.sor
# → traza.sor.meta.json   { OpticalRouteName, OpticalRouteId, TestTime, TestCategory, IsBaseline }
```

`IsBaseline` es `true` cuando `TestCategory` es `Baseline` o `VeryFirstReference`. Si no se
encuentra una coincidencia para el `resultId` dentro de la ruta indicada, se omite el sidecar
y se muestra una advertencia (la descarga del `.sor` no se ve afectada).

`src/sor_viewer.py` detecta automáticamente ese archivo `<archivo>.meta.json` y, si existe,
superpone el nombre de la ruta, la fecha de la traza y una etiqueta de categoría (dorada y
con el texto "BASELINE" cuando corresponde) en la gráfica generada.

#### 8b. Visualizar la traza como imagen (PNG)

`src/sor_viewer.py` lee el `.sor` descargado (formato Bellcore SR-4731 estándar o el
contenedor OLE2 propietario de EXFO iOLM) y genera una gráfica de la traza con sus eventos.

```bash
# Mostrar la gráfica en una ventana
python src/sor_viewer.py traza.sor

# Guardarla como imagen (PNG, PDF, SVG… según la extensión)
python src/sor_viewer.py traza.sor --save grafica.png
```

| Argumento | Descripción |
|---|---|
| `archivo.sor` | Ruta al archivo `.sor` a visualizar (obligatorio) |
| `--save IMAGEN` | Guarda la gráfica en archivo en vez de mostrarla en pantalla |

---

### 9. Reporte PDF de medición OTDR

Genera y descarga el informe PDF oficial de un resultado OTDR.

```
GET https://data.fms.local/v1/results/pdf
```

```bash
python src/main.py result-pdf --result-id <UUID>
python src/main.py result-pdf --result-id <UUID> --save informe_ruta41.pdf
```

Si no se especifica `--save`, el archivo se guarda como `otdr_report_<primeros8chars>.pdf`.

---

## Flujo completo: ver trazas de una ruta

```bash
# 1. Identificar la ruta óptica
python src/main.py optical-routes --name "MI-RUTA" --output table

# 2. Ver las últimas mediciones de esa ruta (ej: routeId=41)
python src/main.py results --route-id 41 --top 10 --output table

# 3. Ver detalle completo de la última medición con fallo
python src/main.py results --route-id 41 --fault-status Detected --top 1 --save fallo.json

# 4. Ver mediciones relacionadas (baseline de referencia)
python src/main.py result-related --result-id <UUID-del-fallo>

# 5. Descargar la traza OTDR del fallo (con metadata de ruta/fecha/baseline)
python src/main.py result-sor --otdr-result-id <UUID-del-fallo> --route-id 41 \
    --format Binary --save fallo.sor

# 6. Generar el informe PDF
python src/main.py result-pdf --result-id <UUID-del-fallo> --save informe_fallo.pdf

# 7. Generar una imagen de la traza para inspección visual
python src/sor_viewer.py fallo.sor --save fallo.png
```

---

## Comandos adicionales

### Dispositivos ópticos

Lista RTUs, patch panels, splitters y otros dispositivos ópticos.

```
GET /api/topology/opticaldevices
```

```bash
python src/main.py optical-devices --output table
python src/main.py optical-devices --name "EA" --output table --save devices.json
python src/main.py optical-devices --ids "1,2,3"
```

### Diagramas

Agrupadores de RTUs y rutas.

```
GET /api/topology/diagrams
```

```bash
python src/main.py diagrams --output table
```

### Configuraciones de test

Plantillas OTDR/iOLM.

```
GET /api/topology/testconfigurations
```

```bash
python src/main.py testconfigs --output table
python src/main.py testconfigs --monitoring-type pon
python src/main.py testconfigs --monitoring-type p2p --test-category monitoring
```

| Argumento | Valores |
|---|---|
| `--monitoring-type` | `p2p`, `pon` |
| `--test-category` | `monitoring`, `adhoc` |

---

## Ejemplo de flujo completo

```bash
# 1. Ver sitios disponibles
python src/main.py sites --output table

# 2. Ver todas las RTUs
python src/main.py rtus --output table

# 3. Ver módulos de la RTU con ID 13
python src/main.py rtu-modules --rtu-id 13 --output table

# 4. Ver puertos de la RTU con ID 13
python src/main.py rtu-ports --rtu-id 13

# 5. Ver rutas ópticas y guardar resultado
python src/main.py optical-routes --output table --save rutas.json

# 6. Ver detalle de la ruta óptica con ID 41
python src/main.py optical-route --id 41

# 7. Ver test setups de esa ruta
python src/main.py route-testsetups --id 41 --output table
```

---

## Nota sobre SSL

El servidor usa certificado **self-signed**. El CLI suprime las advertencias SSL automáticamente (`verify=False`). No usar en producción sin reemplazar el certificado por uno válido.

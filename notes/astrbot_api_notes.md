# AstrBot 4.26.6 — Verified API Cheatsheet (plugin / Pages / Web API)

**Source of truth:** locally installed package
`/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages/astrbot/`
(`astrbot/__init__.py:3` → `__version__ = "4.26.6"`).

Every fact below was read from that source (or, where marked, executed in-process against it).
Anything not provable from this install is explicitly labelled **UNVERIFIED**.

Path prefix used below: `PKG = /Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages/astrbot`.

---

## 1. `Star` base class and `@register(...)`

### Import
`PKG/api/star/__init__.py:1-7`
```python
from astrbot.core.star import Context, Star, StarTools
from astrbot.core.star.config import *
from astrbot.core.star.register import register_star as register
__all__ = ["Context", "Star", "StarTools", "register"]
```
→ `from astrbot.api.star import Star, Context, StarTools, register`

### `Star` definition — `PKG/core/star/base.py`
- `base.py:18` `class Star(CommandParserMixin, PluginKVStoreMixin)`
- `base.py:21-23` declared class attributes: `author: str`, `name: str`, `context: Context`
- `base.py:25-26`
  ```python
  def __init__(self, context: Context, config: dict | None = None) -> None:
      self.context = context
  ```
  Note: `config` is accepted and **ignored** by the base `__init__` (the plugin loader passes it; store it yourself, e.g. `def __init__(self, context, config): super().__init__(context); self.config = config`).
- `base.py:38-49` `__init_subclass__` — any subclass of `Star` is auto-registered into `star_map`/`star_registry`. No decorator needed. (Verified at runtime: `inspect.signature(Star.__init__)` → `(self, context: 'Context', config: 'dict | None' = None) -> 'None'`.)

### `self.name`
- `PKG/core/star/star_manager.py:1164-1171` and `:1190-1193`:
  ```python
  plugin_id = metadata.plugin_id
  p_author, p_name = plugin_id.split("/")
  setattr(metadata.star_cls_type, "name", p_name)     # also on the instance
  setattr(metadata.star_cls_type, "author", p_author)
  setattr(metadata.star_cls_type, "plugin_id", plugin_id)
  ```
- `PKG/core/star/star.py:78-82`:
  ```python
  @property
  def plugin_id(self) -> str:
      p_name = (self.name or "unknown").lower().replace("/", "_")
      p_author = (self.author or "unknown").lower().replace("/", "_")
      return f"{p_author}/{p_name}"
  ```
  ⇒ **`self.name` is the *lower-cased* `name:` value from `metadata.yaml`** (verified at runtime: `StarMetadata(name="Neko_Han", author="Stalyx").plugin_id` == `"stalyx/neko_han"`).
  ⇒ **`self.plugin_id` is `"<author.lower()>/<name.lower()>"`**, also injected as an attribute.
  ⇒ Both are class attributes **and** instance attributes; safe to read in `__init__`.
- `self.context` is the `Context` singleton (`base.py:26`).

### Methods available on `Star` (runtime `dir(Star)`, non-dunder)
`_get_context_config`, `delete_kv_data`, `get_kv_data`, `html_render`, `initialize`, `parse_commands`, `put_kv_data`, `regex_match`, `terminate`, `text_to_image`

| Method | Location | Signature / behaviour |
|---|---|---|
| `text_to_image` | `base.py:51-64` | `async def text_to_image(self, text: str, return_url=True) -> str` — T2I via `html_renderer.render_t2i`, template from config key `t2i_active_template` |
| `html_render` | `base.py:66-79` | `async def html_render(self, tmpl: str, data: dict, return_url=True, options: dict \| None = None) -> str` |
| `initialize` | `base.py:81-82` | `async def initialize(self) -> None` — called after instantiation (`star_manager.py:1346`) |
| `terminate` | `base.py:84-85` | `async def terminate(self) -> None` — disable/reload hook |
| `parse_commands` | `core/utils/command_parser.py:16-20` | `def parse_commands(self, message: str)` → `CommandTokens` |
| `regex_match` | `core/utils/command_parser.py:22-23` | `def regex_match(self, message: str, command: str) -> bool` |

### KV store (`PluginKVStoreMixin`, `PKG/core/utils/plugin_kv_store.py`)
```python
SUPPORTED_VALUE_TYPES = int | float | str | bytes | bool | dict | list | None   # line 5
class PluginKVStoreMixin:                                                        # line 9
    async def put_kv_data(self, key: str, value: SUPPORTED_VALUE_TYPES) -> None  # line 14
    async def get_kv_data(self, key: str, default: _VT) -> _VT | None            # line 22
    async def delete_kv_data(self, key: str) -> None                             # line 26
```
Backed by `astrbot.core.sp` (SharedPreferences/SQLite) under namespace `"plugin"` and key `self.plugin_id`. All three are **async** and `default` is **required** for `get_kv_data`.

### `StarTools` — `PKG/core/star/star_tools.py` (runtime `dir(StarTools)`)
`activate_llm_tool`, `create_event`, `create_message`, `deactivate_llm_tool`, `get_data_dir`, `initialize`, `register_llm_tool`, `send_message`, `unregister_llm_tool`

| Method | Line | Signature |
|---|---|---|
| `initialize` | 23-30 | `classmethod initialize(context: Context) -> None` (called by framework) |
| `send_message` | 32-52 | `async classmethod send_message(session: str \| MessageSesion, message_chain: MessageChain) -> bool` |
| `create_message` | 54-99 | `async classmethod create_message(type, self_id, session_id, sender, message, message_str, message_id="", raw_message=None, group_id="") -> AstrBotMessage` |
| `create_event` | 101-132 | `async classmethod create_event(abm: AstrBotMessage, platform: str = "aiocqhttp", is_wake: bool = True) -> None` (commits a synthetic event) |
| `activate_llm_tool` / `deactivate_llm_tool` | 134-166 | `classmethod (name: str) -> bool` |
| `register_llm_tool` | 168-189 | `classmethod (name: str, func_args: list, desc: str, func_obj: Callable[..., Awaitable[Any]]) -> None` |
| `unregister_llm_tool` | 191-203 | `classmethod (name: str) -> None` |
| `get_data_dir` | 205-260 | `classmethod get_data_dir(plugin_name: str \| None = None) -> Path` → `data/plugin_data/<plugin_name>`, creates it; if `plugin_name` omitted it resolves the caller via `inspect` + `star_map` |

### `@register(...)` — DEPRECATED
`PKG/core/star/register/star.py:8-14`
```python
def register_star(name: str, author: str, desc: str, version: str, repo: str | None = None):
```
- Exact positional order: **`name, author, desc, version, repo=None`** (runtime verified: `(name: str, author: str, desc: str, version: str, repo: str | None = None)`).
- Emits a `DeprecationWarning` once per process (`star.py:38-45`); docstring says it will be removed. In 4.26.6 it only back-fills `StarMetadata` fields (`star.py:47-64`).

---

## 2. `Context.register_web_api` — exact signature, route prefix, methods, path params

`PKG/core/star/context.py:568-590`
```python
def register_web_api(
    self,
    route: str,
    view_handler: WebApiHandler,   # Callable[..., Awaitable[Any]]   (context.py:51)
    methods: list[str],
    desc: str,
) -> None:
```
- Re-registering the *same route + identical methods list* replaces the entry (`:586-589`), otherwise it appends `(route, view_handler, methods, desc)` to the **class-level** list `Context.registered_web_apis` (`context.py:125`, type alias `RegisteredWebApi = tuple[str, WebApiHandler, list[str], str]` at `:52`).
- Runtime verified signature: `(self, route: 'str', view_handler: 'WebApiHandler', methods: 'list[str]', desc: 'str') -> 'None'`.

### Route prefix requirement — VERIFIED
The dashboard matches registered routes against the sub-path **after** the router prefix:

- Modern router: `API_V1_PREFIX = "/api/v1"` (`PKG/dashboard/api/router.py:32,36`) + `@router.get/post/put/patch/delete("/plugins/extensions/{plugin_path:path}")` (`PKG/dashboard/api/plugins.py:378-421`)
  ⇒ `GET|POST|PUT|PATCH|DELETE /api/v1/plugins/extensions/<plugin_path>`
- Legacy router (GET/POST only): `@legacy_router.api_route("/api/plug/{plugin_path:path}", methods=["GET", "POST"])` (`PKG/dashboard/api/plugins.py:1466`)
- Matching: `_match_registered_web_api()` (`plugins.py:165-178`) does
  `request_path = "/" + subpath.lstrip("/")` then `re.fullmatch(_plugin_api_route_pattern(route), request_path)`.
  `_plugin_api_route_pattern` (`plugins.py:152-162`) converts `<name>` → `(?P<name>[^/]+)` and `<path:name>` → `(?P<name>.*)`.
- The Page bridge always puts the plugin name first in that sub-path:
  `bridge endpoint "items/123"` ⇒ `/api/v1/plugins/extensions/<plugin_name>/items/123`.

Runtime verification (executed against `astrbot.dashboard.api.plugins._match_registered_web_api`):

| registered route | request subpath | result |
|---|---|---|
| `/neko_han/hello` | `neko_han/hello` | match, params `{}` |
| `/neko_han/item/<item_id>` | `neko_han/item/42` | match, params `{'item_id': '42'}` |
| `/neko_han/files/<path:rest>` | `neko_han/files/a/b/c.txt` | match, params `{'rest': 'a/b/c.txt'}` |
| `/neko_han/hello` | `hello` | **no match** |

⇒ **The route you register MUST start with `/<plugin_name>/`** (use `f"/{self.name}/..."`). The bridge endpoint on the JS side must **not** include the plugin name.
(Matches the official docs: <https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html>.)

### `methods`
Plain `list[str]` of HTTP verbs, upper-cased on match (`plugins.py:170`). What actually reaches the handler depends on the surface:

- `/api/v1/plugins/extensions/...`: `GET, POST, PUT, PATCH, DELETE` (`plugins.py:378,387,396,405,414`).
- `/api/plug/...` (legacy): `GET, POST` only (`plugins.py:1466`).
- The Page **bridge SDK only exposes `apiGet`/`apiPost`** (see §4) ⇒ from a Page you can effectively reach `GET`/`POST` handlers only, even though `PUT/PATCH/DELETE` routes can be registered and then called by a non-Page client.

### How path params reach the handler
`plugins.py:205-228` → `PluginRequest(..., path_params=path_values, ...)` and then either
`view_handler(**path_values)` (line 216, no app adapter) or `call_request_view(..., path_values, ...)` (line 221) → `PKG/dashboard/asgi_runtime.py:470-474`
```python
async def _call_view(view_func: Callable, path_params: dict[str, Any]):
    result = view_func(**path_params)
```
⇒ params are passed as **keyword arguments**, and they are also available as `request.path_params` (a `dict`). Handlers may be sync or async (`_call_view` awaits if awaitable).

Handler return coercion (`asgi_runtime.py:477-505`): a `Response` is used as-is; `dict`/`list` is JSON-encoded; a tuple `(body, status_code[, headers])` is respected; anything else returned raw.

---

## 3. `astrbot.api.web` — request proxy and response helpers

File: `PKG/api/web.py` (453 lines). `__all__` at `:442-453`:
`PluginMultiDict, PluginRequest, PluginRequestProxy, PluginUploadFile, bind_request_context, error_response, file_response, json_response, request, stream_response`.

### `request` — module-level contextvar proxy
`web.py:322` `request: PluginRequestProxy = PluginRequestProxy()`
Accessing it outside a plugin Web API handler raises `RuntimeError("astrbot.api.web.request is only available inside a plugin Web API handler.")` (`:257-264`).

`PluginRequestProxy` properties/methods (`web.py:254-319`), all delegating to the bound `PluginRequest`:

| Member | Kind | Underlying value (`PluginRequest.__init__`, `web.py:168-190`) |
|---|---|---|
| `request.method` | `str` property | `request_.method` |
| `request.path` | `str` property | `request_.url.path` |
| `request.headers` | `Headers` | starlette headers |
| `request.cookies` | `dict[str, str]` | |
| `request.content_type` | `str \| None` | `headers.get("content-type")` |
| `request.client_host` | `str \| None` | `request_.client.host` |
| `request.path_params` | `dict[str, Any]` | the matched route params |
| `request.plugin_name` | `str \| None` | first path segment of the extension path (`plugins.py:206`) |
| `request.username` | `str \| None` | dashboard user, may be `None` |
| `request.query` | `PluginMultiDict[str]` | `query_params.multi_items()` |
| `await request.body()` | `async -> bytes` | `:192-198` |
| `await request.json(default=None)` | `async -> Any \| default` | returns `default` on any parse failure (`:200-212`) |
| `await request.form()` | `async -> PluginMultiDict[str]` | multipart/urlencoded **without** files (`:228-236`) |
| `await request.files()` | `async -> PluginMultiDict[PluginUploadFile]` | (`:238-246`) |
| `request.headers`, `request.cookies`, `request.client_host`, … | | |
| unknown attributes | `__getattr__` fallthrough to the raw `PluginRequest` (`:318-319`) |

### `PluginMultiDict` (`web.py:17-88`) — duplicate-preserving multidict
```python
get(key, default=None, type=None)   # last value wins; `type(value)` conversion, falls back to default
                                    # on TypeError/ValueError  (:27-63)
getlist(key) -> list[ValueT]        # all values in request order (:64-72)
keys(), values(), items(), __contains__, __getitem__, __bool__
```
(`values()`/`items()` are plain lists, not views.) `PluginUploadFile` (`:90-162`) exposes `filename`, `content_type`, `headers`, `content_length`, and `async save(destination)`, `read(size=-1)`, `write`, `seek`, `close`.

### Response helpers (runtime-verified output shown)

| Helper | Lines | Signature |
|---|---|---|
| `json_response` | 342-362 | `json_response(data: Any = None, *, status_code: int = 200, headers: dict[str,str] \| None = None) -> JSONResponse` |
| `error_response` | 365-387 | `error_response(message: str, *, status_code: int = 400, data: Any = None, headers=None) -> JSONResponse` |
| `file_response` | 390-413 | `file_response(path: str \| Path, *, filename: str \| None = None, content_type: str \| None = None, headers=None) -> FileResponse` |
| `stream_response` | 416-439 | `stream_response(content: Any, *, content_type: str = "text/event-stream", status_code: int = 200, headers=None) -> StreamingResponse` |

Behaviour notes:
- `json_response` does **not** wrap in an envelope; it JSON-encodes `{}` when `data is None`. Runtime: `json_response({"a":1})` → `200 {"a":1}`.
- `error_response` builds `{"status": "error", "message": message, "data": data}`. Runtime: `error_response("boom", status_code=403, data={"x":1})` → `403 {"status":"error","message":"boom","data":{"x":1}}`.
- The bridge turns a `status == "error"` body into a rejected JS Promise (see §4), so `error_response(...)` is the correct way to make `apiGet/apiPost` reject.

---

## 4. Plugin Page bridge SDK (`window.AstrBotPluginPage`)

### Files
- SDK source: **`PKG/dashboard/plugin_page_bridge.js`** (288 lines) — served at **`/api/plugin/page/bridge-sdk.js`**.
- Parent side (WebUI, minified/build): `PKG/dashboard/dist/assets/PluginPagePage-CDSV0VGC.js`; SPA route `/plugin-page/:pluginName/:pageName` (`dist/assets/index-Drktcvxl.js`).
- Server: `PKG/dashboard/services/plugin_page_service.py`; routes in `PKG/dashboard/api/plugins.py:1417-1460`; path constants `PKG/dashboard/plugin_page_auth.py:3-4`.

### Global object and exact methods
`plugin_page_bridge.js:207-285` (runtime-verified by reading the file; grep confirms exactly these actions):

```js
window.AstrBotPluginPage = {
  ready(),                       // :208-210  → Promise<context>  (resolved by the initial context message)
  getContext(),                  // :211-213  → context | null
  getLocale(),                   // :214-216  → context?.locale || "zh-CN"
  getI18n(),                     // :217-219  → context?.i18n || {}
  t(key, fallback),              // :220-222  → nested dot-path lookup into context.i18n[locale]
  onContext(handler),            // :223-234  → unsubscribe function; called immediately if context exists
  __setInitialContext(nextContext), // :235-237 (internal; used by the injected snippet)
  apiGet(endpoint, params),      // :238-240  → Promise<data>
  apiPost(endpoint, body),       // :241-243  → Promise<data>
  async upload(endpoint, file),  // :244-261  → Promise<data>; file must have arrayBuffer()
  download(endpoint, params, filename), // :262-264 → Promise<{filename}>; triggers a browser download
  async subscribeSSE(endpoint, handlers, params), // :265-280 → Promise<subscriptionId>
  async unsubscribeSSE(subscriptionId),           // :281-284 → Promise
};
```

**There is NO `apiPut` and NO `apiDelete`** — verified by grepping the JS source and the built parent bundle: only the action strings `api:get`, `api:post`, `files:upload`, `files:download`, `sse:subscribe`, `sse:unsubscribe` exist. The parent rejects anything else with `Unsupported plugin bridge action: <action>` (built bundle `PluginPagePage-CDSV0VGC.js`). Implement mutations with `apiPost`.

Argument/return shapes:
- `apiGet(endpoint, params?)` → parent does `axios.get(url, { params: params || {} })`.
- `apiPost(endpoint, body?)` → `axios.post(url, body || {})` (JSON body; use `upload()` for multipart).
- `upload(endpoint, file)` → POST `multipart/form-data` with field name **`file`**, plus `fileName`/`fileType`/`fileLastModified`; 60 s timeout (`PluginPagePage-*.js`: `r.append("file", i, u)`).
- `download(endpoint, params, filename)` → GET with `responseType:"blob"`, then an `<a download>` click; `filename` falls back to the `content-disposition` header, then `"download.bin"`.
- `subscribeSSE(endpoint, handlers, params)` → raw `fetch(url, {headers:{Accept:"text/event-stream"}, signal})`; `handlers = {onMessage, onOpen, onError}`; `onMessage({raw, parsed, eventType, lastEventId})`.
- Resolution rule (parent bundle): if the response body has `status === "error"`, the promise **rejects** with `Error(body.message)`; otherwise it resolves with `body.data ?? body`.
- `ready()` resolves once the parent sends the initial `context` message (`:12-14`, `:139-142`, `:162-165`). `getContext()` may be `null` before that.

### Exact URL paths
Two distinct things:

1. **Where the SDK/page assets are fetched (classic HTTP, must carry `asset_token`):**
   - bridge SDK: `/api/plugin/page/bridge-sdk.js` — `plugin_page_service.py:669-673`, route `plugins.py:1417-1422`
   - page HTML/asset: `/api/plugin/page/content/{plugin_name}/{page_name}/[asset_path]` — `plugin_page_service.py:641-666`, routes `plugins.py:1429-1460`
   - Both are protected by `require_dashboard_user` + an `asset_token` JWT (`plugin_page_auth.py`, TTL 60 s — `plugin_page_service.py:24`), and are served with `Cache-Control: no-store`, a CSP, and `X-Frame-Options: SAMEORIGIN` unless `ASTRBOT_LAUNCHER=1` (`plugin_page_service.py:429-444`).
2. **Where the SDK's API calls go (via `postMessage` → parent `axios`):**
   - `endpoint` is validated (`plugin_page_bridge.js:72-96` / built bundle `te()`): must be a non-empty string with no `\`, `://`, `?`, `#`, and no empty/`.`/`..` segments.
   - Built URL: **`/api/v1/plugins/extensions/${encodeURIComponent(pluginName)}/${normalizedEndpoint}`** (built bundle `B()` in `PluginPagePage-CDSV0VGC.js`, next to the endpoint validator).
   - The iframe is created with `sandbox="allow-scripts allow-forms allow-downloads"` and `referrerpolicy="no-referrer"` (`PluginPagePage-*.js`) — **no `allow-same-origin`**, so the page cannot read dashboard cookies; the parent performs the authenticated request.

### Page discovery — no manifest required
`plugin_page_service.py`:
- `PLUGIN_PAGE_ROOT_DIR_NAME = "pages"` (`:25`), `PLUGIN_PAGE_ENTRY_FILE_NAME = "index.html"` (`:26`)
- `get_plugin_root_dir()` (`:485-496`) = `<plugin_store_path | reserved_plugin_path>/<metadata.root_dir_name>`
- `resolve_plugin_pages_root()` (`:498-506`) = `<plugin_root>/pages` (must exist)
- `discover_plugin_pages()` (`:508-538`): every **direct child directory** of `pages/` that contains `index.html` becomes a `PluginPage(name=dir_name, title=dir_name, entry_file="index.html")`. Page names are validated by `normalize_plugin_page_name()` (`:469-483`): non-empty, no `/` or `\`, not `.`/`..`, must not start with `.`.
- **No manifest/metadata file is required** for a Page. `metadata.yaml`'s `pages:` key is parsed into `StarMetadata.pages` (`star_manager.py:532-534`) but is **not consumed anywhere in this install** (grep: only assignment at `star_manager.py:1143`), so directory discovery is authoritative. `plugin_id`/`page_name` in URLs use **`StarMetadata.name`** (i.e. lower-cased `name:` from `metadata.yaml`) — `get_plugin_metadata_by_name()` (`:97-101`) compares `plugin.name == plugin_name`.
- Bridge injection: if the returned HTML has no reference to `/api/plugin/page/bridge-sdk.js`, it is appended before `</body>` (`rewrite_plugin_page_html`, `:738-748`). Relative `src`/`href`/CSS `url()`/JS imports are rewritten to the content path and get the `asset_token`+`theme` query params (`:680-838`).
- SPA route that hosts it: `/plugin-page/:pluginName/:pageName`; the iframe `src` is the page `content_path` returned by `GET /api/plugin/page/entry?name=<plugin>&page=<page>` plus `?theme=dark|light`.

### i18n convention for Pages (verified in `dist/assets/pluginI18n-B1Qcz_1s.js`)
`serialize_plugin_page()` returns `{"name", "title", "i18n_key": f"pages.{page.name}"}` (`plugin_page_service.py:871-875`). Plugin i18n files live at `<plugin>/.astrbot-plugin/i18n/<locale>.json` (`star_manager.py:540-576`) and the resolver looks up `i18n["<locale>"]["pages"]["<page_name>"]["title"]` (and `metadata.display_name` / `metadata.desc` / `metadata.short_desc` for plugin metadata).

---

## 5. `_conf_schema.json` — supported types, `template_list`, `list` of `object`

### Where it is loaded / persisted
`PKG/core/star/star_manager.py:1106-1120`
```python
plugin_schema_path = os.path.join(plugin_dir_path, self.conf_schema_fname)  # "_conf_schema.json", :200
if os.path.exists(plugin_schema_path):
    with open(plugin_schema_path, encoding="utf-8") as f:
        plugin_config = AstrBotConfig(
            config_path=os.path.join(self.plugin_config_path, f"{root_dir_name}_config.json"),
            schema=json.loads(f.read()),
        )
```
- `self.plugin_config_path = get_astrbot_config_path()` (`:194`) → `<root>/data/config`
- ⇒ persisted to **`data/config/<plugin_dir_name>_config.json`**
- The resulting object is passed to `MyPlugin(context=..., config=plugin_config)` (`:1174-1183`), falling back to `MyPlugin(context=...)` on `TypeError` (`:1176-1181`).

### Supported types — runtime verified
`_config_schema_to_default_config()` (`PKG/core/config/astrbot_config.py:139-164`) validates against `DEFAULT_VALUE_MAP` (`PKG/core/config/default.py:4396-4406`):
```python
{"int": 0, "float": 0.0, "bool": False, "string": "", "text": "",
 "list": [], "file": [], "object": {}, "template_list": []}
```
Executed against this install: `type: "dict"` → `TypeError: 不受支持的配置类型 dict。支持的类型有：dict_keys(['int','float','bool','string','text','list','file','object','template_list'])`; `type: "nope"` → same `TypeError`.

⚠️ **Gotcha (verified):** the WebUI renderer and the official docs also handle `"type": "dict"` (with `template_schema`), and AstrBot core uses it in `core/config/default.py:2044,2055`; but the **plugin** schema path goes through `_config_schema_to_default_config`, which raises `TypeError` for `"dict"`. That exception is caught by the outer per-plugin `except BaseException` (`star_manager.py:1369-1371`, logs `----- 插件 <dir> 载入失败 -----`), so a plugin using `"type": "dict"` **fails to load in 4.26.6**. Use `"object"` (+ `items`) instead. (The core config does not hit this path because `AstrBotConfig()` is constructed with `DEFAULT_CONFIG`, not a schema.)

### `template_list` — supported
- Accepted by the map above and special-cased at `astrbot_config.py:157-158` (`conf[k] = default`, i.e. the `default` value or `[]`).
- Dashboard validation: `PKG/dashboard/services/config_service.py:74-101` (`_validate_template_list`) and `:218-220`.
- WebUI editor: `TemplateListEditor` in `PKG/dashboard/dist/assets/ProviderChatCompletionPanel-CYyc6Ga0.js`; it uses `itemMeta.templates`, `__template_key`, and per-template `items` schemas.

Expected JSON shape (source-derived; corroborated by <https://docs.astrbot.app/en/dev/star/guides/plugin-config.html>):
```json
"field_id": {
  "type": "template_list",
  "description": "Template List Field",
  "default": [],
  "templates": {
    "template_1": {
      "name": "Template One",
      "hint": "hint",
      "display_item": "attr_name",
      "hide_hint_in_list": true,
      "items": {
        "attr_name": {"description": "Attribute Name", "type": "string", "default": ""},
        "attr_a":     {"description": "Attribute A",    "type": "int",    "default": 10},
        "attr_b":     {"description": "Attribute B",    "type": "bool",   "default": true}
      }
    }
  }
}
```
Persisted value (one entry per added row, `__template_key` selects the template):
```json
"field_id": [
  {"__template_key": "template_1", "attr_name": "", "attr_a": 10, "attr_b": true}
]
```
Validation details (`config_service.py:74-101`): value must be a `list`; each item must be a `dict`; the template key is read from `item["__template_key"]` **or** `item["template"]` (legacy) — missing ⇒ error `缺少模板选择`; unknown key ⇒ `未知模板`; then the item's fields are validated against `templates[<key>]["items"]` with path prefix `<key>.templates.<template_key>.`.
Nested key addressing for `template_list` templates (`config_service.py:151-182`, `:167-179`) is `<field>.templates.<template>.items...`.
Optional template display keys (`display_item`, `hide_hint_in_list`) are UI-only (docs; also referenced in the built editor).

### `list` of `object`
- Default generation (`astrbot_config.py:159-160`): `"list"` simply takes `v["default"]` verbatim (or `[]`). **`items` is ignored** — you must provide concrete default entries yourself.
- Dashboard typed validation (`config_service.py:248-262`) does honour `items`:
  ```python
  if meta["type"] == "list" and not isinstance(value, list): error
  elif meta["type"] == "list" and value and "items" in meta and isinstance(value[0], dict):
      for item in value: validate(item, meta["items"], path=f"{path}{key}.")
  ```
  ⇒ only checked when the list is non-empty and the first element is a dict.
- Pattern used by AstrBot's own core config for a list-of-object: `{"type": "list", "items": {...sub-schema...}, "default": [...]}` (e.g. `platform` arrays in `core/config/default.py`).
- Runtime-verified example (`list` with `items` + `default=[{"k":1}]`) round-tripped to JSON untouched:
  `{"my_list":[{"k":2}], "items":[], "tpl":[], "plain":""}` after `save_config()`.

Other schema keys used by the UI (from `core/config/i18n_utils.py:80-87` and the built renderer): `description`, `hint`, `obvious_hint`, `default`, `items`, `invisible`, `secret`, `options`, `labels`, `render_type`, `slider{min,max,step}`, `editor_mode`, `editor_language`, `editor_theme`, `_special`, `condition`, `collapsed`, `readonly`, `file_types`, `template_schema`. Only `type` is required by the Python converter; a missing `description`/`hint` is fine.

---

## 6. `AstrBotConfig`

- Import paths: `from astrbot.api import AstrBotConfig` (`PKG/api/__init__.py:5`, `__all__`), `from astrbot.api.all import AstrBotConfig`, or `from astrbot.core.config.astrbot_config import AstrBotConfig` (`PKG/core/config/astrbot_config.py:28`).
- `class AstrBotConfig(dict)` — `astrbot_config.py:28`.
- Constructor `astrbot_config.py:40-45`:
  `AstrBotConfig(config_path: str = ASTRBOT_CONFIG_PATH, default_config: dict = DEFAULT_CONFIG, schema: dict | None = None)`
  - `ASTRBOT_CONFIG_PATH = <data>/cmd_config.json` (`:17`)
  - If `schema` is given, `default_config` is **replaced** by `_config_schema_to_default_config(schema)` (`:53-54`).
  - If the file does not exist it is created from defaults (`:56-60`, `first_deploy` flag).
  - `check_config_integrity()` (`:166-223`) recursively adds missing keys, drops unknown ones, fixes ordering, and calls `save_config()` when anything changed.
- `save_config(replace_config: dict | None = None, *, indent: int = 2) -> None` (`:225-251`): optional replace-then-update; atomic write via `tempfile.mkstemp` + `os.replace`, JSON indent 2 (4 on first deploy, `:59`), `ensure_ascii=False`, `utf-8-sig` (BOM) encoding.
- Dict access semantics:
  - `__getattr__` (`:253-257`) → `self[item]`, returns `None` on `KeyError` (never raises). Runtime-verified: `c.plain == ''`, missing key `c.zzz is None`.
  - `__setattr__` (`:266-267`) → `self[key] = value` (**does not auto-save**).
  - `__delattr__` (`:259-264`) → deletes the key **and saves**; raises `AttributeError` if absent.
  - Standard `dict` methods (`[]`, `.get`, `.update`, …) work.
- Real config file for a plugin: `data/config/<plugin_dir_name>_config.json` (§5).

---

## 7. Session waiter utilities

### Import paths
- `from astrbot.api.util import SessionController, SessionWaiter, session_waiter` — `PKG/api/util/__init__.py:1-7`
  ⚠️ **`SessionFilter` is NOT re-exported there.**
- Full set: `from astrbot.core.utils.session_waiter import session_waiter, SessionController, SessionWaiter, SessionFilter, DefaultSessionFilter` — `PKG/core/utils/session_waiter.py`
- Built-in usage example: `PKG/builtin_stars/astrbot/main.py:12-17,112-131`.

### `session_waiter` — `session_waiter.py:174-204`
```python
def session_waiter(timeout: int = 30, record_history_chains: bool = False):
    def decorator(func: Callable[[SessionController, AstrMessageEvent], Awaitable[Any]]):
        async def wrapper(event, session_filter: SessionFilter | None = None, *args, **kwargs):
            if not session_filter: session_filter = DefaultSessionFilter()
            if not isinstance(session_filter, SessionFilter): raise ValueError(...)
            session_id = session_filter.filter(event)
            FILTERS.append(session_filter)
            waiter = SessionWaiter(session_filter, session_id, record_history_chains)
            return await waiter.register_wait(func, timeout)
        return wrapper
    return decorator
```
- The decorated function is awaited **inside the handler**: `await my_waiter(event)` — it blocks until `controller.stop()`, a new matching message, or `timeout`; on timeout it raises `TimeoutError("等待超时")` (`:78-80`). `record_history_chains=True` deep-copies each incoming event's message chain into `controller.history_chains` (`:162-165`).
- Routing of later messages into the waiter happens in `SessionWaiter.trigger(cls, session_id, event)` (`:153-171`) — called by the framework's event pipeline; the decorated function's first arg must be `(controller, event)`.

### `SessionController` — `:18-87`
```python
class SessionController:
    def __init__(self) -> None:
        self.future = asyncio.Future()
        self.current_event: asyncio.Event | None = None
        self.ts: float | None = None
        self.timeout: float | int | None = None
        self.history_chains: list[list[Comp.BaseMessageComponent]] = []   # :30

    def stop(self, error: Exception | None = None) -> None:              # :32-38
    def keep(self, timeout: float = 0, reset_timeout=False) -> None:      # :40-72
    async def _holding(self, event: asyncio.Event, timeout: float) -> None:  # :74-83 (internal)
    def get_history_chains(self) -> list[list[Comp.BaseMessageComponent]]:   # :85-87
```
- `stop(error=None)`: resolves the future (or sets the exception if `error` given); no-op if already done.
- `keep(timeout=0, reset_timeout=False)` (not async, returns `None`):
  - `reset_timeout=True`: restart the timer with exactly `timeout`; if `timeout <= 0` → immediate `stop()`.
  - `reset_timeout=False`: extend the **remaining** timeout by `timeout` (`left + timeout`); if `<= 0` → immediate `stop()`.
  - Both assertions (`self.timeout`/`self.ts` not None) mean the first `keep` of a session must use `reset_timeout=True` (which `register_wait` does).
  - Implementation cancels the previous hold event and spawns `asyncio.create_task(self._holding(...))`.
- `get_history_chains()` returns the raw list (deep copies captured only when `record_history_chains=True`).
- Signature note: `get_history_chains(self)` takes **no** arguments.

### `SessionFilter` / `DefaultSessionFilter`
```python
class SessionFilter:                       # :90-95
    @abc.abstractmethod
    def filter(self, event: AstrMessageEvent) -> str: ...
class DefaultSessionFilter(SessionFilter): # :98-101
    def filter(self, event): return event.unified_msg_origin
```
Pass a custom filter as the keyword argument: `await my_waiter(event, session_filter=MyFilter())`.

---

## 8. Proactive messaging outside an event handler

### Send API
`PKG/core/star/context.py:506-540`
```python
async def send_message(self, session: str | MessageSesion, message_chain: MessageChain) -> bool:
```
- `session` = `event.unified_msg_origin` (a string) or an `event.session` (`MessageSesion`). Strings go through `MessageSesion.from_str()`; invalid → `ValueError("不合法的 session 字符串: ...")` (`:527-531`).
- Looks up `platform.meta().id == session.platform_name` among `platform_manager.platform_insts`, calls `platform.send_by_session(...)`, returns `True`; returns `False` + a warning if no platform matches (`:533-540`). `qq_official` does not support this method (docstring `:525`).
- Convenience wrapper: `await StarTools.send_message(session, message_chain)` (`star_tools.py:32-52`, raises `ValueError("StarTools not initialized")` if the framework hasn't called `StarTools.initialize`).

### `MessageChain` — `PKG/core/message/message_event_result.py:18-34` (dataclass)
Fields: `chain: list[BaseMessageComponent] = []`, `use_t2i_: bool | None = None`, `use_markdown_: bool | None = None`, `type: str | None = None`.
Constructor: `MessageChain()` or `MessageChain(chain=[...])`.

| Helper | Line | Signature / return |
|---|---|---|
| `message` | 49-58 | `message(self, message: str)` → `self` (appends `Plain`) |
| `at` | 60-69 | `at(self, name: str, qq: str \| int)` → `self` (appends `At`) |
| `at_all` | 71-80 | `at_all(self)` → `self` (appends `AtAll`) |
| `url_image` | 93-104 | `url_image(self, url: str)` → `self` (`Image.fromURL`) |
| `file_image` | 106-116 | `file_image(self, path: str)` → `self` (`Image.fromFileSystem`) |
| `base64_image` | 118-125 | `base64_image(self, base64_str: str)` → `self` |
| `use_t2i` | 127-135 | `use_t2i(self, use_t2i: bool)` → `self` |
| `use_markdown` | 137-147 | `use_markdown(self, use: bool \| None = True)` → `self` |
| `derive` | 36-47 | `derive(self, chain=None) -> MessageChain` (copies metadata) |
| `get_plain_text` | 149-172 | `get_plain_text(self, with_other_comps_mark: bool = False) -> str` |
| `squash_plain` | 174-196 | `squash_plain(self)` (merges all `Plain` into the first) |
| `error` | 82-91 | **deprecated** — same as `message` |

All chain helpers are chainable (return `self`). Runtime-verified:
`MessageChain().message('hi').at('张三','123').file_image('/tmp/x.png')` →
`Plain{text:'hi'}`, `At{qq:'123', name:'张三'}`, `Image{file:'file:///private/tmp/x.png', path:'/private/tmp/x.png'}`.

### `Comp` component constructors — `PKG/core/message/components.py`
Import as `import astrbot.api.message_components as Comp` or `from astrbot.api.message_components import *` (`PKG/api/message_components.py:1` re-exports `core/message/components.py`); also `from astrbot.api.platform import *` re-exports them.

| Component | Line | Constructor |
|---|---|---|
| `Plain` | 111-123 | `Plain(text: str, convert: bool = True, **_)`; `toDict()` → `{"type":"text","data":{"text":...}}` |
| `At` | 408-420 | pydantic model `At(qq: int\|str, name: str\|None = "")`; `AtAll()` sets `qq="all"` (423-427) |
| `Image` | 499-531 | `Image(file: str \| None, **_)`; statics `fromURL(url)` (http/https only), `fromFileSystem(path)` (→ `file://` URI + `path`), `fromBase64(b64)` (→ `base64://`), `fromBytes(bytes)`, `fromIO(IO)` |
| `Node` | 653-703 | `Node(content: list[BaseMessageComponent], **_)` plus fields `name` (nickname), `uin` (QQ, default `"0"`), `id`, `seq`, `time`; `await node.to_dict()` |
| `Nodes` | 707-731 | `Nodes(nodes: list[Node], **_)` |
| `File` | 761-... | `File(name: str, file: str = "", url: str = "")` |
| others | 125-812 | `Face`, `Record`, `Video`, `RPS`, `Dice`, `Shake`, `Share`, `Contact`, `Location`, `Music`, `Reply`, `Poke`, `Forward`, `Json`, `Unknown` |

Runtime-verified: `Comp.Node(content=[Comp.Plain('p'), Comp.At(qq='1', name='n')], name='Nick', uin='10001').model_dump()` →
`{'type': Node, 'id': 0, 'name': 'Nick', 'uin': '10001', 'content': [Plain, At], 'seq': '', 'time': 0}`; `Comp.AtAll().qq == 'all'`.

### Typical proactive send
```python
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, At, Image, Node
from astrbot.api import logger

await self.context.send_message(umo, MessageChain().message("hi").file_image(path))
```

---

## 9. `metadata.yaml` keys recognized by the loader

Files searched: `metadata.yaml`, then `metadata.yml` — `PKG/core/star/updator.py:13` `PLUGIN_METADATA_FILENAMES = ("metadata.yaml", "metadata.yml")`.

Required fields — `updator.py:14` `PLUGIN_METADATA_REQUIRED_FIELDS = ("name", "desc", "version", "author")`; each must be a **non-empty string** (`updator.py:131-150`). `description` is accepted as an alias for `desc` (`updator.py:128-129`, `star_manager.py:499-500`).

Keys actually read into `StarMetadata` — `PKG/core/star/star_manager.py:506-536`:

| key | required | notes |
|---|---|---|
| `name` | ✅ | must be a valid Python identifier (`_validate_importable_name`, `star_manager.py:582-591`); also used as the plugin **directory name** (`_get_plugin_dir_name_from_metadata`, `:593-624`) |
| `author` | ✅ | |
| `desc` (`description` alias) | ✅ | |
| `short_desc` | – | only if it is a `str`, else `None` (`:510-514`) |
| `version` | ✅ | free-form string (`v0.1`, `1.0.0`, …) |
| `repo` | – | optional; without it the plugin cannot be updated |
| `display_name` | – | WebUI display name (≥ v4.5.0) |
| `support_platforms` | – | list of adapter-id strings, filtered to `str` entries; ids are keys of `ADAPTER_NAME_2_TYPE` (`star.py:66-67`) |
| `astrbot_version` | – | PEP 440 specifier, e.g. `>=4.13.0,<4.17.0`; validated with `SpecifierSet` against `VERSION` (`star_manager.py:626-655`) |
| `pages` | – | list; **parsed and stored but not used by the page system in 4.26.6** (see §4) |

Not a YAML key: `i18n`. Translations are loaded from **`<plugin>/.astrbot-plugin/i18n/<locale>.json`** (`star_manager.py:540-576`; ≤1 MB, must be a JSON object). Plugin logo: a file named **`logo.png`** in the plugin root (`self.logo_fname = "logo.png"`, `star_manager.py:201`; applied at `:1301-1302`).
`StarMetadata` dataclass fields: `PKG/core/star/star.py:17-76` (`name, author, desc, short_desc, version, repo, star_cls_type, module_path, star_cls, module, root_dir_name, reserved, activated, config, star_handler_full_names, display_name, logo_path, support_platforms, astrbot_version, i18n, pages`).

Local example (`~/.astrbot/data/plugins/neko_han/metadata.yaml`) uses `name, display_name, desc, version, author, repo` — all valid.

---

## 10. Plugin data directory helpers (`PKG/core/utils/astrbot_path.py`)

| Function | Line | Returns |
|---|---|---|
| `get_astrbot_path()` | 22-26 | the AstrBot source tree |
| `get_astrbot_root()` | 29-35 | `$ASTRBOT_ROOT` → realpath; else packaged-desktop ⇒ `~/.astrbot`; else `os.getcwd()` |
| `get_astrbot_data_path()` | 38-40 | `<root>/data` |
| `get_astrbot_config_path()` | 43-45 | `<root>/data/config` |
| `get_astrbot_plugin_path()` | 48-50 | `<root>/data/plugins` |
| **`get_astrbot_plugin_data_path()`** | **53-55** | **`<root>/data/plugin_data`** |
| `get_astrbot_t2i_templates_path()` | 58-60 | `<root>/data/t2i_templates` |
| `get_astrbot_webchat_path()` | 63-65 | `<root>/data/webchat` |
| `get_astrbot_temp_path()` | 68-70 | `<root>/data/temp` |
| `get_astrbot_skills_path()` | 73-75 | `<root>/data/skills` |
| `get_astrbot_workspaces_path()` | 78-80 | `<root>/data/workspaces` |
| `get_astrbot_system_tmp_path()` | 83-85 | `<tempdir>/.astrbot` |
| `get_astrbot_site_packages_path()` | 88-90 | `<root>/data/site-packages` |
| `get_astrbot_knowledge_base_path()` | 93-95 | `<root>/data/knowledge_base` |
| `get_astrbot_backups_path()` | 98-100 | `<root>/data/backups` |

`get_astrbot_plugin_data_path()` takes **no arguments** (runtime verified: `inspect.signature(...)` → `() -> str`; returned `<root>/data/plugin_data`).
Per-plugin sub-directory: either `Path(get_astrbot_plugin_data_path()) / plugin_name` (docs pattern) or `StarTools.get_data_dir()` → `data/plugin_data/<metadata.name>` (created, `.resolve()`d; `star_tools.py:205-260`).

---

## 11. `astrbot.api.logger`

- `PKG/astrbot/__init__.py:1-4`: `import logging` / `__version__ = "4.26.6"` / `logger = logging.getLogger("astrbot")`.
- `PKG/api/__init__.py:1` `from astrbot import logger`; listed in `__all__` (`:6-16`). Also re-exported by `astrbot.api.all` (`from astrbot import logger`).
- Runtime verified: `from astrbot.api import logger` → `isinstance(logger, logging.Logger)`, `logger.name == "astrbot"`, identical object to `astrbot.logger`.
- It is a **stdlib `logging.Logger`** (not loguru), configured by `astrbot.core` via `LogManager.GetLogger("astrbot")` (`PKG/core/log.py:256-263`) with loguru bridging. Usage: `logger.info/warning/error/exception/debug(...)`, `%s`-style args supported.
- Also available: `astrbot.core.logger` (same object; `PKG/core/__init__.py` sets `logger = LogManager.GetLogger(log_name="astrbot")`) — used by dashboard code, e.g. `dashboard/api/plugins.py:13`.

---

## 12. Bundled example plugins using `pages/` + `register_web_api`

**None exist in this installation.** Verified:
- `grep -rn "register_web_api" PKG --include=*.py` → only `PKG/core/star/context.py:568` (the definition itself).
- No `pages/` directory anywhere under `PKG` (`find PGK -type d -name pages` → empty, including `builtin_stars/astrbot`, `builtin_stars/builtin_commands`).
- `grep -rl "register_web_api" <site-packages>` → only `astrbot/core/star/context.py` (+ its `.pyc`).
- Bundled builtin plugins (`PKG/builtin_stars/astrbot`, `PKG/builtin_stars/builtin_commands`) ship only `metadata.yaml`, code and `.astrbot-plugin/i18n/{en-US,zh-CN}.json`.
- The local data dir has exactly one plugin: `~/.astrbot/data/plugins/neko_han` — `metadata.yaml`, `main.py`, `README.md`, `LICENSE`, `.gitignore`; **no `pages/`, no `_conf_schema.json`, no `register_web_api`** (it only has a `@filter.command("喵")` handler).

The closest verified reference implementations are:
1. the official docs "Minimal Complete Example" (backend + `pages/bridge-demo/index.html` + `app.js`) at <https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html>, and
2. the WebUI's own `bridge-sdk` source `PKG/dashboard/plugin_page_bridge.js`, which is the definitive method list.

### Minimal correct skeleton for this install
```
data/plugins/<plugin_dir>/            # dir name == metadata.yaml name (valid Python identifier)
├─ metadata.yaml                      # name, desc, version, author (required) + repo/display_name
├─ main.py
├─ _conf_schema.json                  # optional; avoid "type": "dict"
└─ pages/
   └─ settings/
      └─ index.html                   # required entry; bridge script auto-injected
```
```python
# main.py
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request


class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # route MUST start with the plugin name == self.name (lower-cased metadata name)
        context.register_web_api(f"/{self.name}/ping", self.page_ping, ["GET"], "Page ping")
        context.register_web_api(
            f"/{self.name}/items/<item_id>", self.get_item, ["GET"], "Get item"
        )

    async def page_ping(self):
        limit = request.query.get("limit", 20, type=int)     # query.get(key, default, type)
        return json_response({"message": "pong", "limit": limit, "username": request.username})

    async def get_item(self, item_id: str):                   # path params arrive as kwargs
        return json_response({"item_id": item_id})
```
```js
// pages/settings/index.html  (script or module)
const bridge = window.AstrBotPluginPage;      // SDK auto-injected before </body>
const ctx = await bridge.ready();
const data = await bridge.apiGet("ping", { limit: 20 });   // -> /api/v1/plugins/extensions/<pluginName>/ping
```

---

## UNVERIFIED / caveats

1. **No runtime end-to-end test of a live Page.** The dashboard server was not started (this session cannot bind the WebUI port / approval is disabled). All bridge and route behaviour above is from source plus isolated in-process calls of `_match_registered_web_api`, `AstrBotConfig`, `MessageChain`, `Comp`, and `json_response`/`error_response`. Not executed: an actual `postMessage` round-trip, the `asset_token` JWT flow, `sandbox` iframe behaviour, `upload`/`download`/SSE.
2. **`apiPut`/`apiDelete`**: their absence is verified in `plugin_page_bridge.js` and in the built parent bundle's action dispatcher (both greppable). I could not find any other SDK variant in this install; a differently-versioned WebUI dist is out of scope.
3. **`metadata.yaml` `pages:` key**: verified as parsed/stored and *not consumed* anywhere under `PKG` in 4.26.6 (grep). A consumer could exist outside this package (e.g. plugin-market tooling) — untested.
4. **`template_list` optional UI keys** (`display_item`, `hide_hint_in_list`) come from the official docs and the minified editor bundle; the Python validator (`config_service.py:74-101`) ignores all keys other than `__template_key`/`template` and `items`. The `templates[k]["default"]` per-template default is **not** applied by `_config_schema_to_default_config` (that path returns `default` or `[]` for the whole field).
5. **`"type": "dict"` failure** is proven for `AstrBotConfig(schema=...)`; I did not run a full `PluginManager.load_plugin()` cycle, but the call site (`star_manager.py:1113-1121`) is unguarded and the enclosing handler is `except BaseException` at `:1369`, which logs `插件 <dir> 载入失败`.
6. Official docs were consulted only to corroborate shape/conventions (cited inline); they describe a slightly different version (e.g. they list `dict` as supported and show `display_item`/`hide_hint_in_list`).

/* ==========================================================================
 * neko_han · AstrBot 插件管理面板（Page）
 * --------------------------------------------------------------------------
 * 纯原生 ES Module，零依赖、零构建，可直接在 AstrBot 的沙箱 iframe 中运行。
 *
 * 约束提醒（决定了本文件的写法）：
 *   - 沙箱为 sandbox="allow-scripts allow-forms allow-downloads"：
 *     没有 localStorage / cookie / 同源 / CDN，并且 window.confirm 之类的
 *     原生模态框会被浏览器拦截，所以确认弹窗必须自己用 DOM 实现。
 *   - 所有网络请求都要走 window.AstrBotPluginPage 桥接（apiGet / apiPost），
 *     不能直接 fetch AstrBot 的 API。
 *   - 桥接在失败时 reject 一个 Error（message 即后端返回的 message），
 *     因此每个调用都必须 try/catch，并把 err.message 展示给用户。
 * ========================================================================== */

/* ==========================================================================
 * 1. 桥接层与请求封装
 * ========================================================================== */

// 桥接对象由 AstrBot 注入。若缺失（例如在普通浏览器里直接打开调试），
// 则退回内置的离线演示数据，保证 UI 仍可预览，同时页面顶部会给出醒目提示。
const bridge = (typeof window !== 'undefined' && window.AstrBotPluginPage) || null;
const OFFLINE = !bridge;

/** 读取桥接返回的错误文案（桥接可能 reject Error，也可能 reject 字符串/对象）。 */
function message(err) {
  if (!err) return '未知错误';
  if (typeof err === 'string') return err;
  if (typeof err.message === 'string' && err.message) return err.message;
  try {
    return String(err);
  } catch (_) {
    return '未知错误';
  }
}

/**
 * 统一的 GET 请求。endpoint 是相对于插件 API 命名空间的路径，
 * 不带前导斜杠、不带插件名，例如 'items'。
 */
async function apiGet(endpoint, params) {
  const target = OFFLINE ? offlineBridge : bridge;
  if (!target || typeof target.apiGet !== 'function') {
    throw new Error('AstrBot 桥接未就绪，无法读取数据');
  }
  return target.apiGet(endpoint, params);
}

/** 统一的 POST 请求，body 会被序列化成 JSON。 */
async function apiPost(endpoint, body) {
  const target = OFFLINE ? offlineBridge : bridge;
  if (!target || typeof target.apiPost !== 'function') {
    throw new Error('AstrBot 桥接未就绪，无法提交数据');
  }
  return target.apiPost(endpoint, body);
}

/* ==========================================================================
 * 2. 常量
 * ========================================================================== */

/** 标签页名称，顺序与 HTML 中的导航按钮一致。 */
const TAB_NAMES = ['items', 'shop', 'overview'];

/** 分类兜底列表（接口会返回 categories，这里只用于接口异常时的降级显示）。 */
const DEFAULT_CATEGORIES = ['food', 'drink', 'medicine', 'special', 'toy'];

/** 分类中文名。 */
const CATEGORY_LABELS = {
  food: '食物',
  drink: '饮品',
  medicine: '药品',
  special: '特殊',
  toy: '玩具',
};

/** effect_keys 兜底值。 */
const DEFAULT_EFFECT_KEYS = ['satiety', 'hydration', 'energy', 'health', 'revive'];

/** 数值型效果字段（revive 是布尔值，单独处理）。 */
const NUMERIC_EFFECT_KEYS = ['satiety', 'hydration', 'energy', 'health'];

/** 效果字段中文名。 */
const EFFECT_LABELS = {
  satiety: '饱食',
  hydration: '水分',
  energy: '精力',
  health: '健康',
};

/** 编辑器里需要做行内校验的字段 → 输入框 id。 */
const FIELD_IDS = {
  id: 'f-id',
  name: 'f-name',
  basePrice: 'f-basePrice',
  priceFluctuation: 'f-priceFluctuation',
  stockMin: 'f-stockMin',
  stockMax: 'f-stockMax',
};

/* ==========================================================================
 * 3. 页面状态（唯一数据源）
 * ========================================================================== */

const state = {
  activeTab: 'items',
  items: [],
  categories: DEFAULT_CATEGORIES.slice(),
  categoryLabels: {}, // 接口可选返回的 category_labels（分类中文名，优先使用）
  effectKeys: DEFAULT_EFFECT_KEYS.slice(),
  effectFlags: null, // 接口可选返回的 effect_flags（如 ["revive"]），null 表示接口未提供
  shop: null,
  overview: null,
  editingId: null, // 正在编辑的道具 id；null 表示新增
  filters: { q: '', category: '', onlyDisabled: false },
};

/* ==========================================================================
 * 4. 通用工具函数
 * ========================================================================== */

const $ = (id) => document.getElementById(id);

/** HTML 文本转义，所有来自接口的字符串都要经过它再插入 innerHTML。 */
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 属性值转义（用于 select 的 value）。 */
function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

/** 安全取整：任何非法输入都回退到 fallback。 */
function toInt(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

/** 安全取浮点。 */
function toFloat(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** 金币等数字的展示格式化。 */
function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('zh-CN');
}

/** 0.2 → "20%"。 */
function formatPercent(ratio) {
  const n = Number(ratio);
  if (!Number.isFinite(n)) return '0%';
  const pct = n * 100;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
}

function categoryLabel(key) {
  if (!key) return '未分类';
  // 后端可能直接给出 category_labels，它是权威的中文名；缺失时用内置兜底表
  const fromApi = state.categoryLabels ? state.categoryLabels[key] : '';
  return fromApi || CATEGORY_LABELS[key] || String(key);
}

/** 把 effect 对象转成 "饱食+30 · 水分+35" / "复活" / "无效果"。 */
function effectSummary(effect) {
  const eff = effect && typeof effect === 'object' ? effect : {};
  const parts = [];
  for (const key of NUMERIC_EFFECT_KEYS) {
    const value = toInt(eff[key], 0);
    if (value !== 0) parts.push(`${EFFECT_LABELS[key]}${value > 0 ? '+' : ''}${value}`);
  }
  if (eff.revive === true) parts.push('复活');
  return parts.length ? parts.join(' · ') : '无效果';
}

/** 今日价格相对基础价的涨跌：红涨绿跌。 */
function priceDelta(price, basePrice) {
  const p = Number(price);
  const b = Number(basePrice);
  if (!Number.isFinite(p) || !Number.isFinite(b) || b <= 0) {
    return { text: '—', cls: 'delta-flat' };
  }
  const pct = Math.round(((p - b) / b) * 100);
  if (pct === 0) return { text: '持平', cls: 'delta-flat' };
  return { text: `${pct > 0 ? '+' : ''}${pct}%`, cls: pct > 0 ? 'delta-up' : 'delta-down' };
}

/** 徽章 HTML。 */
function tag(text, variant) {
  return `<span class="tag${variant ? ` tag-${variant}` : ''}">${escapeHtml(text)}</span>`;
}

/** 布尔徽章：开启为绿色，关闭为灰色。 */
function boolTag(label, on) {
  return tag(label, on ? 'on' : 'off');
}

/* ==========================================================================
 * 5. 全局 UI：加载指示器 / Toast / 确认对话框
 * ========================================================================== */

let loadingCount = 0;

/** 开始一次加载（支持并发计数，计数归零才隐藏进度条）。 */
function startLoading(text) {
  loadingCount += 1;
  const label = $('loadingText');
  if (label && text) label.textContent = text;
  const bar = $('loadingBar');
  if (bar) bar.hidden = false;
}

function stopLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) {
    const bar = $('loadingBar');
    if (bar) bar.hidden = true;
  }
}

/**
 * 包住一次异步操作：期间禁用按钮并显示进度条，结束后无论如何都恢复按钮状态。
 * 这样请求失败也不会把页面卡在「按钮永久禁用」的状态。
 */
async function runWithLoading(button, loadingText, task) {
  const wasDisabled = button ? !!button.disabled : false;
  if (button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  }
  startLoading(loadingText);
  try {
    return await task();
  } finally {
    stopLoading();
    if (button) {
      button.disabled = wasDisabled;
      button.removeAttribute('aria-busy');
    }
  }
}

/** 显示一条提示。type: 'success' | 'error' | 'info'。 */
function toast(type, text, timeout) {
  const area = $('toastArea');
  if (!area || typeof document.createElement !== 'function') return;

  const kind = type === 'error' ? 'error' : type === 'success' ? 'success' : 'info';
  const icon = kind === 'error' ? '❌' : kind === 'success' ? '✅' : 'ℹ️';
  const el = document.createElement('div');
  el.className = `toast toast-${kind}`;
  el.innerHTML =
    `<span class="toast-icon" aria-hidden="true">${icon}</span>` +
    '<span class="toast-text"></span>' +
    '<button type="button" class="toast-close" aria-label="关闭提示">✕</button>';

  // 用 textContent 写入文案，避免后端返回内容造成 HTML 注入
  const textEl = typeof el.querySelector === 'function' ? el.querySelector('.toast-text') : null;
  if (textEl) textEl.textContent = String(text);

  let removed = false;
  const remove = () => {
    if (removed) return;
    removed = true;
    el.classList.add('is-out');
    setTimeout(() => {
      if (typeof el.remove === 'function') el.remove();
      else if (el.parentNode && typeof el.parentNode.removeChild === 'function') el.parentNode.removeChild(el);
    }, 220);
  };

  const closeBtn = typeof el.querySelector === 'function' ? el.querySelector('.toast-close') : null;
  if (closeBtn) closeBtn.addEventListener('click', remove);

  if (typeof area.appendChild === 'function') area.appendChild(el);
  setTimeout(remove, typeof timeout === 'number' ? timeout : kind === 'error' ? 7000 : 4200);
}

/* --- 自建确认对话框（沙箱内 window.confirm 不可用） ---------------------- */

let confirmResolver = null;

/**
 * 返回 Promise<boolean> 的确认框。
 * @param {{title?:string, message?:string, confirmText?:string, cancelText?:string, icon?:string, danger?:boolean}} options
 */
function confirmDialog(options = {}) {
  // 若已有确认框打开，先把上一个按「取消」处理，避免 Promise 永久挂起
  if (confirmResolver) {
    const previous = confirmResolver;
    confirmResolver = null;
    previous(false);
  }

  return new Promise((resolve) => {
    confirmResolver = resolve;
    const icon = $('confirmIcon');
    const title = $('confirmTitle');
    const msg = $('confirmMessage');
    const ok = $('confirmOkBtn');
    const cancel = $('confirmCancelBtn');
    if (icon) icon.textContent = options.icon || '⚠️';
    if (title) title.textContent = options.title || '请确认';
    if (msg) msg.textContent = options.message || '';
    if (ok) {
      ok.textContent = options.confirmText || '确定';
      ok.classList.toggle('btn-danger', options.danger !== false);
      ok.classList.toggle('btn-primary', options.danger === false);
    }
    if (cancel) cancel.textContent = options.cancelText || '取消';
    const backdrop = $('confirmBackdrop');
    if (backdrop) backdrop.hidden = false;
    if (ok && typeof ok.focus === 'function') ok.focus();
  });
}

function resolveConfirm(result) {
  const backdrop = $('confirmBackdrop');
  if (backdrop) backdrop.hidden = true;
  const resolver = confirmResolver;
  confirmResolver = null;
  if (resolver) resolver(result);
}

/* ==========================================================================
 * 6. 标签页切换
 * ========================================================================== */

function switchTab(name) {
  state.activeTab = TAB_NAMES.includes(name) ? name : TAB_NAMES[0];
  for (const tab of TAB_NAMES) {
    const active = tab === state.activeTab;
    const btn = $(`tabBtn-${tab}`);
    const panel = $(`panel-${tab}`);
    if (btn) {
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
      btn.tabIndex = active ? 0 : -1;
    }
    if (panel) {
      panel.classList.toggle('is-active', active);
      panel.hidden = !active;
    }
  }
}

/* ==========================================================================
 * 7. 道具管理
 * ========================================================================== */

/** 当前生效的数值型效果字段（接口返回的 effect_keys 优先）。 */
function activeEffectKeys() {
  return Array.isArray(state.effectKeys) && state.effectKeys.length
    ? state.effectKeys
    : NUMERIC_EFFECT_KEYS.slice();
}

/**
 * 当前生效的布尔型效果字段。
 * 后端可能用 effect_flags 单独给出（如 ["revive"]），也可能把它并进 effect_keys，
 * 这里两种形态都兼容；接口什么都没给时按内置默认展示。
 */
function activeEffectFlags() {
  if (Array.isArray(state.effectFlags)) return state.effectFlags;
  if (Array.isArray(state.effectKeys) && state.effectKeys.length) return state.effectKeys;
  return ['revive'];
}

/** 用接口返回的分类填充两个下拉框，并尽量保留用户当前选择。 */
function fillCategoryOptions() {
  const cats = Array.isArray(state.categories) && state.categories.length
    ? state.categories
    : DEFAULT_CATEGORIES.slice();

  const filter = $('itemCategoryFilter');
  if (filter) {
    const current = filter.value || '';
    filter.innerHTML =
      '<option value="">全部分类</option>' +
      cats.map((c) => `<option value="${escapeAttr(c)}">${escapeHtml(categoryLabel(c))}</option>`).join('');
    filter.value = cats.includes(current) ? current : '';
  }

  const formSelect = $('f-category');
  if (formSelect) {
    const current = formSelect.value || '';
    formSelect.innerHTML = cats
      .map((c) => `<option value="${escapeAttr(c)}">${escapeHtml(categoryLabel(c))}</option>`)
      .join('');
    formSelect.value = cats.includes(current) ? current : cats[0];
  }
}

/** 按当前搜索/分类条件过滤道具。 */
function filteredItems() {
  const searchEl = $('itemSearch');
  const categoryEl = $('itemCategoryFilter');
  const disabledEl = $('itemOnlyDisabled');

  const keyword = String((searchEl && searchEl.value) || '').trim().toLowerCase();
  const category = String((categoryEl && categoryEl.value) || '');
  const onlyDisabled = !!(disabledEl && disabledEl.checked);

  return state.items.filter((item) => {
    if (!item || typeof item !== 'object') return false;
    if (category && item.category !== category) return false;
    if (onlyDisabled && item.enabled !== false) return false;
    if (!keyword) return true;
    const name = String(item.name || '').toLowerCase();
    const id = String(item.id || '').toLowerCase();
    return name.includes(keyword) || id.includes(keyword);
  });
}

/** 渲染道具表格。 */
function renderItems() {
  const tbody = $('itemsTbody');
  const emptyHint = $('itemsEmpty');
  const list = filteredItems();

  if (tbody) {
    tbody.innerHTML = list.map(itemRowHtml).join('');
  }
  if (emptyHint) {
    // 区分「一个道具都没有」和「筛选后为空」两种情况，提示更友好
    const hasAny = state.items.length > 0;
    emptyHint.hidden = list.length > 0;
    emptyHint.textContent = hasAny
      ? '没有符合条件的道具，试试调整搜索或分类筛选。'
      : '还没有任何道具，点击「新增道具」创建第一个，或「恢复默认道具」载入内置道具。';
  }
}

function itemRowHtml(item) {
  const id = String(item.id || '');
  const delta = item.enabled === false ? ' is-disabled' : '';
  return `
    <tr class="${delta.trim()}" data-id="${escapeAttr(id)}">
      <td>
        <span class="cell-item">
          <span class="item-emoji" aria-hidden="true">${escapeHtml(item.emoji || '📦')}</span>
          <span class="item-name">${escapeHtml(item.name || '(未命名)')}</span>
        </span>
      </td>
      <td class="col-optional"><code class="mono">${escapeHtml(id || '—')}</code></td>
      <td>${tag(categoryLabel(item.category))}</td>
      <td class="num">${escapeHtml(formatNumber(item.base_price))}</td>
      <td class="num col-optional">±${escapeHtml(formatPercent(item.price_fluctuation))}</td>
      <td class="num col-optional">${escapeHtml(formatNumber(item.stock_min))} – ${escapeHtml(formatNumber(item.stock_max))}</td>
      <td class="cell-effect">${escapeHtml(effectSummary(item.effect))}</td>
      <td class="cell-badges">
        ${boolTag('上架', item.shop_enabled !== false)}
        ${boolTag('可交易', item.tradable !== false)}
        ${boolTag('启用', item.enabled !== false)}
      </td>
      <td class="cell-actions">
        <button type="button" class="btn btn-small" data-action="edit" data-id="${escapeAttr(id)}">编辑</button>
        <button type="button" class="btn btn-small btn-danger-ghost" data-action="delete" data-id="${escapeAttr(id)}">删除</button>
      </td>
    </tr>`;
}

/* --- 数据加载 ------------------------------------------------------------ */

async function loadItems(options = {}) {
  startLoading('正在加载道具列表…');
  try {
    const data = await apiGet('items');
    state.items = data && Array.isArray(data.items) ? data.items : [];
    if (data && Array.isArray(data.categories) && data.categories.length) {
      state.categories = data.categories.slice();
    }
    // 可选的附加信息：分类中文名、布尔型效果字段（旧/新后端形态都能兼容）
    if (data && data.category_labels && typeof data.category_labels === 'object') {
      state.categoryLabels = data.category_labels;
    }
    if (data && Array.isArray(data.effect_flags)) {
      state.effectFlags = data.effect_flags.slice();
    }
    if (data && Array.isArray(data.effect_keys) && data.effect_keys.length) {
      state.effectKeys = data.effect_keys.slice();
    }
    fillCategoryOptions();
    renderItems();
    if (options.notify) toast('success', `道具列表已更新，共 ${state.items.length} 个道具`);
  } catch (err) {
    if (!options.silent) toast('error', `加载道具列表失败：${message(err)}`);
    renderItems();
  } finally {
    stopLoading();
  }
}

/* --- 新增 / 编辑 -------------------------------------------------------- */

function setInputValue(id, value) {
  const el = $(id);
  if (el) el.value = value;
}

function setInputChecked(id, checked) {
  const el = $(id);
  if (el) el.checked = !!checked;
}

/** 新增时的默认草稿值。 */
function defaultDraft() {
  const cats = Array.isArray(state.categories) && state.categories.length
    ? state.categories
    : DEFAULT_CATEGORIES.slice();
  return {
    id: '',
    name: '',
    emoji: '📦',
    category: cats[0] || 'food',
    description: '',
    base_price: 0,
    price_fluctuation: 0.1,
    shop_enabled: true,
    stock_min: 1,
    stock_max: 10,
    effect: { satiety: 0, hydration: 0, energy: 0, health: 0, revive: false },
    tradable: true,
    enabled: true,
  };
}

/** 打开编辑器；item 为 null 时表示新增。 */
function openEditor(item) {
  const draft = item && typeof item === 'object' ? item : defaultDraft();
  const effect = draft.effect && typeof draft.effect === 'object' ? draft.effect : {};
  state.editingId = item && item.id ? String(item.id) : null;

  clearFieldErrors();
  setEditorHint('');

  fillCategoryOptions();
  applyEffectKeyVisibility();

  setInputValue('f-originalId', state.editingId || '');
  setInputValue('f-id', draft.id || '');
  setInputValue('f-name', draft.name || '');
  setInputValue('f-emoji', draft.emoji || '📦');
  setInputValue('f-description', draft.description || '');
  setInputValue('f-basePrice', toInt(draft.base_price, 0));
  setInputValue('f-priceFluctuation', toFloat(draft.price_fluctuation, 0));
  setInputValue('f-stockMin', toInt(draft.stock_min, 0));
  setInputValue('f-stockMax', toInt(draft.stock_max, 0));
  setInputValue('f-eff-satiety', toInt(effect.satiety, 0));
  setInputValue('f-eff-hydration', toInt(effect.hydration, 0));
  setInputValue('f-eff-energy', toInt(effect.energy, 0));
  setInputValue('f-eff-health', toInt(effect.health, 0));
  setInputChecked('f-eff-revive', effect.revive === true);
  setInputChecked('f-shopEnabled', draft.shop_enabled !== false);
  setInputChecked('f-tradable', draft.tradable !== false);
  setInputChecked('f-enabled', draft.enabled !== false);

  const cats = Array.isArray(state.categories) && state.categories.length
    ? state.categories
    : DEFAULT_CATEGORIES.slice();
  const categorySelect = $('f-category');
  if (categorySelect) {
    categorySelect.value = cats.includes(draft.category) ? draft.category : cats[0];
  }

  const title = $('editorTitle');
  if (title) {
    title.textContent = state.editingId ? `编辑道具 · ${draft.name || draft.id}` : '新增道具';
  }

  updatePreview();

  const backdrop = $('editorBackdrop');
  if (backdrop) backdrop.hidden = false;
  const nameInput = $('f-name');
  if (nameInput && typeof nameInput.focus === 'function') nameInput.focus();
}

function closeEditor() {
  const backdrop = $('editorBackdrop');
  if (backdrop) backdrop.hidden = true;
  state.editingId = null;
}

/** 只显示接口声明的效果字段，避免展示后端并不支持的选项。 */
function applyEffectKeyVisibility() {
  const keys = activeEffectKeys();
  for (const key of NUMERIC_EFFECT_KEYS) {
    const field = $(`effField-${key}`);
    if (field) field.hidden = !keys.includes(key);
  }
  const flags = activeEffectFlags();
  const reviveRow = $('reviveRow');
  if (reviveRow) reviveRow.hidden = !flags.includes('revive');
}

/** 从表单读取一个数值输入框。 */
function readNumber(id, fallback = 0) {
  const el = $(id);
  if (!el) return fallback;
  const raw = String(el.value === null || el.value === undefined ? '' : el.value).trim();
  if (raw === '') return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : NaN;
}

/** 收集表单内容；返回 { payload, errors }。errors 中每项为 { field, text }。 */
function collectForm() {
  const errors = [];
  const idRaw = String(($('f-id') || {}).value || '').trim();
  const normalizedId = normalizeItemId(idRaw);
  const name = String(($('f-name') || {}).value || '').trim();
  const emoji = String(($('f-emoji') || {}).value || '').trim();
  const description = String(($('f-description') || {}).value || '').trim();
  const category = String(($('f-category') || {}).value || '') || 'food';

  // ID 规则与后端 normalize_item 保持一致：小写字母/数字/下划线，长度 2-32
  if (idRaw && !/^[a-z0-9_]{2,32}$/.test(normalizedId)) {
    errors.push({ field: 'id', text: 'ID 只能包含小写字母、数字和下划线，长度 2-32 位。' });
  }
  if (!name) {
    errors.push({ field: 'name', text: '请填写道具名称。' });
  } else if (name.length > 20) {
    errors.push({ field: 'name', text: `名称最多 20 个字符（当前 ${name.length} 个）。` });
  }

  const basePrice = readNumber('f-basePrice', 0);
  if (!Number.isFinite(basePrice) || basePrice < 0) {
    errors.push({ field: 'basePrice', text: '基础价格必须是不小于 0 的数字。' });
  }

  const fluctuation = readNumber('f-priceFluctuation', 0);
  if (!Number.isFinite(fluctuation) || fluctuation < 0 || fluctuation > 1) {
    errors.push({ field: 'priceFluctuation', text: '价格浮动需要是 0 到 1 之间的小数（例如 0.2 表示 ±20%）。' });
  }

  const stockMin = readNumber('f-stockMin', 0);
  if (!Number.isFinite(stockMin) || stockMin < 0) {
    errors.push({ field: 'stockMin', text: '库存下限必须是不小于 0 的整数。' });
  }

  const stockMax = readNumber('f-stockMax', 0);
  if (!Number.isFinite(stockMax) || stockMax < 0) {
    errors.push({ field: 'stockMax', text: '库存上限必须是不小于 0 的整数。' });
  }

  if (Number.isFinite(stockMin) && Number.isFinite(stockMax) && stockMin > stockMax) {
    errors.push({ field: 'stockMax', text: '库存上限不能小于库存下限。' });
  }

  const effect = {
    satiety: toInt(readNumber('f-eff-satiety', 0) || 0, 0),
    hydration: toInt(readNumber('f-eff-hydration', 0) || 0, 0),
    energy: toInt(readNumber('f-eff-energy', 0) || 0, 0),
    health: toInt(readNumber('f-eff-health', 0) || 0, 0),
    revive: !!($('f-eff-revive') || {}).checked,
  };

  const payload = {
    name,
    emoji: emoji || '📦',
    category,
    description,
    base_price: Number.isFinite(basePrice) ? Math.trunc(basePrice) : 0,
    price_fluctuation: Number.isFinite(fluctuation) ? fluctuation : 0,
    shop_enabled: !!($('f-shopEnabled') || {}).checked,
    stock_min: Number.isFinite(stockMin) ? Math.trunc(stockMin) : 0,
    stock_max: Number.isFinite(stockMax) ? Math.trunc(stockMax) : 0,
    effect,
    tradable: !!($('f-tradable') || {}).checked,
    enabled: !!($('f-enabled') || {}).checked,
  };

  // id 为空时省略该字段，交给后端自动生成；否则发送规范化后的 id
  if (idRaw) payload.id = normalizedId;

  return { payload, errors };
}

/**
 * 规范化道具 ID：后端 normalize_item 会把大写转小写、把短横线和空格转成下划线，
 * 前端提前做同样的处理，保证「所见即所存」。
 */
function normalizeItemId(raw) {
  return String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, '_');
}

function setEditorHint(text) {
  const hint = $('editorHint');
  if (hint) hint.textContent = text || '';
}

function clearFieldErrors() {
  for (const key of Object.keys(FIELD_IDS)) {
    const errEl = $(`e-${key}`);
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = '';
    }
    const input = $(FIELD_IDS[key]);
    if (input && typeof input.removeAttribute === 'function') input.removeAttribute('aria-invalid');
  }
}

/** 把校验错误写到对应输入框下方，并把焦点移到第一个出错的字段。 */
function showFieldErrors(errors) {
  clearFieldErrors();
  let firstInput = null;
  for (const err of errors) {
    const errEl = $(`e-${err.field}`);
    if (errEl) {
      errEl.textContent = err.text;
      errEl.hidden = false;
    }
    const input = $(FIELD_IDS[err.field]);
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      if (!firstInput) firstInput = input;
    }
  }
  if (firstInput && typeof firstInput.focus === 'function') firstInput.focus();
}

/** 预览卡片：随表单输入实时更新。 */
function updatePreview() {
  const name = String(($('f-name') || {}).value || '').trim() || '未命名道具';
  const emoji = String(($('f-emoji') || {}).value || '').trim() || '📦';
  const basePrice = readNumber('f-basePrice', 0);
  const fluctuation = readNumber('f-priceFluctuation', 0);
  const effect = {
    satiety: toInt(readNumber('f-eff-satiety', 0) || 0, 0),
    hydration: toInt(readNumber('f-eff-hydration', 0) || 0, 0),
    energy: toInt(readNumber('f-eff-energy', 0) || 0, 0),
    health: toInt(readNumber('f-eff-health', 0) || 0, 0),
    revive: !!($('f-eff-revive') || {}).checked,
  };

  const emojiEl = $('previewEmoji');
  const nameEl = $('previewName');
  const priceEl = $('previewPrice');
  const effectEl = $('previewEffect');
  const badgesEl = $('previewBadges');

  if (emojiEl) emojiEl.textContent = emoji;
  if (nameEl) nameEl.textContent = name;
  if (priceEl) {
    const price = Number.isFinite(basePrice) ? formatNumber(Math.trunc(basePrice)) : '0';
    const fluc = Number.isFinite(fluctuation) && fluctuation > 0 ? `（浮动 ±${formatPercent(fluctuation)}）` : '';
    priceEl.textContent = `${price} 金币${fluc}`;
  }
  if (effectEl) effectEl.textContent = effectSummary(effect);
  if (badgesEl) {
    badgesEl.innerHTML = [
      boolTag('官方上架', !!($('f-shopEnabled') || {}).checked),
      boolTag('可交易', !!($('f-tradable') || {}).checked),
      boolTag('启用', !!($('f-enabled') || {}).checked),
    ].join('');
  }
}

/** 保存（新增或更新）道具。 */
async function onSaveItem() {
  const { payload, errors } = collectForm();
  if (errors.length) {
    showFieldErrors(errors);
    setEditorHint('请先修正表单中标红的内容。');
    toast('error', `表单校验未通过（${errors.length} 项），请检查标红的字段。`);
    return;
  }
  setEditorHint('');
  const saveBtn = $('editorSaveBtn');

  await runWithLoading(saveBtn, '正在保存道具…', async () => {
    try {
      const result = await apiPost('items/save', payload);
      const saved = result && result.item ? result.item : payload;
      const created = !!(result && result.created);
      upsertItem(saved);
      renderItems();
      closeEditor();
      toast('success', created ? `已新增道具「${saved.name || saved.id}」` : `已保存道具「${saved.name || saved.id}」`);
    } catch (err) {
      const text = message(err);
      setEditorHint(`保存失败：${text}`);
      toast('error', `保存道具失败：${text}`);
    }
  });
}

/** 把后端返回的道具写回本地状态（id 可能被改写，所以要按编辑前 id 定位）。 */
function upsertItem(item) {
  if (!item || typeof item !== 'object') return;
  const targetId = item.id || state.editingId;
  const index = state.items.findIndex(
    (it) => it && (it.id === targetId || (state.editingId && it.id === state.editingId)),
  );
  if (index >= 0) state.items[index] = item;
  else state.items.push(item);
}

/** 表格里的「编辑 / 删除」按钮（事件委托）。 */
function onItemsTableClick(event) {
  const target = event && event.target;
  const button = target && typeof target.closest === 'function' ? target.closest('[data-action]') : null;
  if (!button) return;
  const action = button.getAttribute('data-action');
  const id = button.getAttribute('data-id');
  if (action === 'edit') {
    const item = state.items.find((it) => it && it.id === id);
    if (!item) {
      toast('error', '找不到该道具，可能已被删除，请刷新列表。');
      return;
    }
    openEditor(item);
  } else if (action === 'delete') {
    onDeleteItem(id);
  }
}

/** 删除道具（二次确认）。 */
async function onDeleteItem(id) {
  const item = state.items.find((it) => it && it.id === id);
  const name = (item && item.name) || id;
  const ok = await confirmDialog({
    icon: '🗑️',
    title: '删除道具',
    message: `确定要删除「${name}」（${id}）吗？此操作不可撤销。`,
    confirmText: '确认删除',
  });
  if (!ok) return;

  await runWithLoading(null, '正在删除道具…', async () => {
    try {
      await apiPost('items/delete', { id });
      state.items = state.items.filter((it) => !it || it.id !== id);
      renderItems();
      toast('success', `已删除道具「${name}」`);
    } catch (err) {
      toast('error', `删除失败：${message(err)}`);
    }
  });
}

/** 恢复默认道具（强确认）。 */
async function onResetItems() {
  const ok = await confirmDialog({
    icon: '♻️',
    title: '恢复默认道具',
    message: '这会用内置的默认道具覆盖现有道具数据，所有自定义道具与改动都会丢失。确定继续吗？',
    confirmText: '确认恢复',
  });
  if (!ok) return;

  const resetBtn = $('resetItemsBtn');
  await runWithLoading(resetBtn, '正在恢复默认道具…', async () => {
    try {
      const result = await apiPost('items/reset', {});
      if (result && Array.isArray(result.items)) state.items = result.items;
      renderItems();
      toast('success', `已恢复默认道具，共 ${state.items.length} 个`);
    } catch (err) {
      toast('error', `恢复默认道具失败：${message(err)}`);
    }
  });
}

/* ==========================================================================
 * 8. 官方商城
 * ========================================================================== */

async function loadShop(options = {}) {
  startLoading('正在加载官方商城…');
  try {
    const data = await apiGet('shop');
    state.shop = data && typeof data === 'object' ? data : null;
    renderShop();
    if (options.notify) toast('success', '官方商城已刷新');
  } catch (err) {
    if (!options.silent) toast('error', `加载官方商城失败：${message(err)}`);
  } finally {
    stopLoading();
  }
}

function renderShop() {
  const shop = state.shop || {};
  const entries = enrichShopEntries(shop.entries);

  const dateEl = $('shopDate');
  const hourEl = $('shopRefreshHour');
  const countEl = $('shopCount');
  if (dateEl) dateEl.textContent = shop.date || '—';
  if (hourEl) hourEl.textContent = Number.isFinite(Number(shop.refresh_hour)) ? `${toInt(shop.refresh_hour, 0)}:00` : '—';
  if (countEl) countEl.textContent = String(entries.length);

  const tbody = $('shopTbody');
  if (tbody) tbody.innerHTML = entries.map(shopRowHtml).join('');

  const emptyHint = $('shopEmpty');
  if (emptyHint) emptyHint.hidden = entries.length > 0;
}

/**
 * 补齐商城条目的展示信息。
 * GET shop 会带 name/emoji/category，但 shop/refresh 的返回只包含 id 与价格库存，
 * 这里用已经加载好的道具列表补上，避免刷新后表格退化成「id + 📦」。
 */
function enrichShopEntries(entries) {
  if (!Array.isArray(entries)) return [];
  return entries.map((entry) => {
    const e = entry && typeof entry === 'object' ? { ...entry } : {};
    if (!e.name || !e.emoji || !e.category) {
      const item = state.items.find((it) => it && it.id === e.item_id);
      if (item) {
        if (!e.name) e.name = item.name;
        if (!e.emoji) e.emoji = item.emoji;
        if (!e.category) e.category = item.category;
      }
    }
    return e;
  });
}

function shopRowHtml(entry) {
  const e = entry && typeof entry === 'object' ? entry : {};
  const delta = priceDelta(e.price, e.base_price);
  const stock = toInt(e.stock, 0);
  const stockInitial = toInt(e.stock_initial, 0);
  const ratio = stockInitial > 0 ? Math.max(0, Math.min(1, stock / stockInitial)) : 0;
  const lowCls = stockInitial > 0 && stock / stockInitial <= 0.3 ? ' is-low' : '';

  return `
    <tr data-id="${escapeAttr(e.item_id || '')}">
      <td>
        <span class="cell-item">
          <span class="item-emoji" aria-hidden="true">${escapeHtml(e.emoji || '📦')}</span>
          <span class="item-name">${escapeHtml(e.name || e.item_id || '(未知商品)')}</span>
        </span>
      </td>
      <td>${tag(categoryLabel(e.category))}</td>
      <td class="num">${escapeHtml(formatNumber(e.base_price))}</td>
      <td class="num"><b>${escapeHtml(formatNumber(e.price))}</b></td>
      <td class="num"><span class="delta ${delta.cls}">${escapeHtml(delta.text)}</span></td>
      <td class="num">
        <span class="stock-cell">
          <span>${escapeHtml(formatNumber(stock))} / ${escapeHtml(formatNumber(stockInitial))}</span>
          <span class="stock-bar${lowCls}" title="库存剩余 ${Math.round(ratio * 100)}%"><i style="width:${Math.round(ratio * 100)}%"></i></span>
        </span>
      </td>
    </tr>`;
}

async function onRefreshShop() {
  const refreshBtn = $('shopRefreshBtn');
  await runWithLoading(refreshBtn, '正在刷新官方商城…', async () => {
    try {
      const data = await apiPost('shop/refresh', {});
      state.shop = data && typeof data === 'object' ? data : state.shop;
      renderShop();
      toast('success', `官方商城已刷新，共 ${Array.isArray((state.shop || {}).entries) ? state.shop.entries.length : 0} 件商品`);
    } catch (err) {
      toast('error', `刷新商城失败：${message(err)}`);
    }
  });
}

/* ==========================================================================
 * 9. 概览与发放金币
 * ========================================================================== */

async function loadOverview(options = {}) {
  startLoading('正在加载数据概览…');
  try {
    const data = await apiGet('overview');
    state.overview = data && typeof data === 'object' ? data : null;
    renderOverview();
    if (options.notify) toast('success', '概览数据已更新');
  } catch (err) {
    if (!options.silent) toast('error', `加载概览失败：${message(err)}`);
  } finally {
    stopLoading();
  }
}

function renderOverview() {
  const data = state.overview || {};
  const stats = [
    { icon: '👤', label: '玩家总数', value: data.players, hint: '已注册的玩家' },
    {
      icon: '🐱',
      label: '猫娘总数',
      value: data.catgirls,
      hint: `存活 ${formatNumber(data.alive)} · 死亡 ${formatNumber(data.dead)}`,
    },
    { icon: '💍', label: '结婚对数', value: data.marriages, hint: '已结为伴侣的猫娘' },
    { icon: '🏷️', label: '市场挂单', value: data.listings, hint: '玩家交易行在售' },
    { icon: '🪙', label: '金币总量', value: data.total_coins, hint: '全体玩家持有金币' },
    { icon: '📅', label: '商城日期', value: data.shop_date, hint: '官方商城当前日期', raw: true },
  ];

  const grid = $('statGrid');
  if (grid) {
    grid.innerHTML = stats
      .map((s) => {
        const shown = s.raw
          ? s.value
            ? escapeHtml(s.value)
            : '—'
          : escapeHtml(formatNumber(s.value));
        return `
          <div class="stat-card">
            <span class="stat-label"><span aria-hidden="true">${s.icon}</span> ${escapeHtml(s.label)}</span>
            <span class="stat-value">${shown}</span>
            <span class="stat-hint">${escapeHtml(s.hint || '')}</span>
          </div>`;
      })
      .join('');
  }

  const top = Array.isArray(data.top_players) ? data.top_players : [];
  const list = $('topPlayers');
  if (list) {
    list.innerHTML = top
      .map((player, index) => {
        const p = player && typeof player === 'object' ? player : {};
        return `
          <li class="rank-item">
            <span class="rank-no">${index + 1}</span>
            <span class="rank-name">${escapeHtml(p.name || '匿名玩家')}</span>
            <span class="rank-meta">🐱 ${escapeHtml(formatNumber(p.catgirls))}</span>
            <span class="rank-coins">🪙 ${escapeHtml(formatNumber(p.coins))}</span>
          </li>`;
      })
      .join('');
  }
  const topEmpty = $('topPlayersEmpty');
  if (topEmpty) topEmpty.hidden = top.length > 0;
}

/** 发放金币表单提交。 */
async function onGrantSubmit(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();

  const errorEl = $('grantError');
  const showError = (text) => {
    if (errorEl) {
      errorEl.textContent = text;
      errorEl.hidden = false;
    }
  };
  if (errorEl) {
    errorEl.hidden = true;
    errorEl.textContent = '';
  }

  const playerId = String(($('grantPlayerId') || {}).value || '').trim();
  const amountRaw = String(($('grantAmount') || {}).value || '').trim();
  const amount = Number(amountRaw);

  if (!playerId) {
    showError('请填写玩家 ID，例如 aiocqhttp:12345。');
    const input = $('grantPlayerId');
    if (input && typeof input.focus === 'function') input.focus();
    return;
  }
  if (amountRaw === '' || !Number.isFinite(amount) || !Number.isInteger(amount)) {
    showError('请填写整数金额（可以为负数，表示扣除）。');
    const input = $('grantAmount');
    if (input && typeof input.focus === 'function') input.focus();
    return;
  }
  if (amount === 0) {
    showError('金额不能为 0，请输入正数发放或负数扣除。');
    return;
  }

  const submitBtn = $('grantSubmitBtn');
  await runWithLoading(submitBtn, '正在发放金币…', async () => {
    try {
      const result = await apiPost('players/grant', { player_id: playerId, amount });
      const balance = result && result.coins !== undefined ? result.coins : null;
      const action = amount > 0 ? '发放' : '扣除';
      toast(
        'success',
        `已向「${playerId}」${action} ${formatNumber(Math.abs(amount))} 金币` +
          (balance === null ? '' : `，当前余额 ${formatNumber(balance)}`),
      );
      await loadOverview();
    } catch (err) {
      const text = message(err);
      showError(`发放失败：${text}`);
      toast('error', `发放金币失败：${text}`);
    }
  });
}

/* ==========================================================================
 * 10. 主题与桥接上下文
 * ========================================================================== */

function syncThemeChip() {
  const root = document.documentElement;
  const chip = $('themeChip');
  if (!root || !chip) return;
  const theme = root.getAttribute('data-theme');
  chip.textContent = theme === 'dark' ? '🌙 主题：深色' : theme === 'light' ? '☀️ 主题：浅色' : '🎨 主题：跟随面板';
}

/** 页面被单独打开（没有 data-theme）时，用系统偏好补一个，保证配色正确。 */
function ensureThemeAttribute() {
  const root = document.documentElement;
  if (!root) return;
  if (!root.getAttribute('data-theme')) {
    const prefersDark =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  }
  syncThemeChip();
}

/** 根据桥接上下文设置标题、副标题与主题。 */
function applyContext(ctx) {
  if (!ctx || typeof ctx !== 'object') return;
  const titleEl = $('pageTitle');
  const subEl = $('pageSubtitle');

  if (ctx.pageTitle && titleEl) titleEl.textContent = ctx.pageTitle;
  if (subEl) {
    const plugin = ctx.displayName || ctx.pluginName || '';
    subEl.textContent = plugin ? `${plugin} · 道具管理 · 官方商城 · 数据概览` : '道具管理 · 官方商城 · 数据概览';
  }
  if (typeof document !== 'undefined' && ctx.pageTitle) {
    document.title = `${ctx.pageTitle} · 管理面板`;
  }

  // 桥接给出的 isDark 是权威值（服务端已写在 <html data-theme> 上，这里保持同步）
  if (typeof ctx.isDark === 'boolean') {
    const root = document.documentElement;
    if (root) root.setAttribute('data-theme', ctx.isDark ? 'dark' : 'light');
  }
  syncThemeChip();
}

/** 连接桥接上下文，并订阅主题/上下文变化。 */
async function connectContext() {
  try {
    if (bridge && typeof bridge.ready === 'function') {
      const ctx = await bridge.ready();
      applyContext(ctx);
    } else if (OFFLINE) {
      const ctx = await offlineBridge.ready();
      applyContext(ctx);
    }
  } catch (err) {
    toast('error', `与 AstrBot 建立连接失败：${message(err)}`);
  }

  // 订阅上下文变化（主题切换等），返回的取消订阅函数暂不需要用到
  try {
    if (bridge && typeof bridge.onContext === 'function') {
      bridge.onContext((ctx) => applyContext(ctx));
    }
  } catch (err) {
    toast('error', `订阅主题变化失败：${message(err)}`);
  }

  // 双保险：直接监听 <html data-theme> 的变化来更新主题指示
  try {
    if (typeof MutationObserver === 'function' && document.documentElement) {
      const observer = new MutationObserver(syncThemeChip);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }
  } catch (_) {
    /* 观察失败不影响主要功能，忽略 */
  }
}

/* ==========================================================================
 * 11. 事件绑定
 * ========================================================================== */

/** 绑定事件的小助手：元素不存在时静默跳过，避免任何单点报错拖垮整页。 */
function on(id, eventName, handler) {
  const el = $(id);
  if (el && typeof el.addEventListener === 'function') el.addEventListener(eventName, handler);
  return el;
}

function setupEvents() {
  // --- 标签导航 ---
  for (const name of TAB_NAMES) {
    on(`tabBtn-${name}`, 'click', () => switchTab(name));
    on(`tabBtn-${name}`, 'keydown', (event) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
      const index = TAB_NAMES.indexOf(name);
      const next = event.key === 'ArrowRight'
        ? (index + 1) % TAB_NAMES.length
        : (index - 1 + TAB_NAMES.length) % TAB_NAMES.length;
      switchTab(TAB_NAMES[next]);
      const nextBtn = $(`tabBtn-${TAB_NAMES[next]}`);
      if (nextBtn && typeof nextBtn.focus === 'function') nextBtn.focus();
    });
  }

  // --- 顶部操作 ---
  on('refreshAllBtn', 'click', onRefreshAll);

  // --- 道具筛选 ---
  on('itemSearch', 'input', renderItems);
  on('itemCategoryFilter', 'change', renderItems);
  on('itemOnlyDisabled', 'change', renderItems);

  // --- 道具操作 ---
  on('addItemBtn', 'click', () => openEditor(null));
  on('resetItemsBtn', 'click', onResetItems);
  on('itemsTbody', 'click', onItemsTableClick);

  // --- 编辑器 ---
  on('editorCloseBtn', 'click', closeEditor);
  on('editorCancelBtn', 'click', closeEditor);
  on('editorSaveBtn', 'click', onSaveItem);
  on('editorBackdrop', 'mousedown', (event) => {
    if (event && event.target === event.currentTarget) closeEditor();
  });
  const form = $('itemForm');
  if (form && typeof form.addEventListener === 'function') {
    form.addEventListener('input', updatePreview);
    form.addEventListener('change', updatePreview);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      onSaveItem();
    });
  }

  // --- 商城 ---
  on('shopRefreshBtn', 'click', onRefreshShop);

  // --- 概览 ---
  on('grantForm', 'submit', onGrantSubmit);

  // --- 确认对话框 ---
  on('confirmOkBtn', 'click', () => resolveConfirm(true));
  on('confirmCancelBtn', 'click', () => resolveConfirm(false));
  on('confirmBackdrop', 'mousedown', (event) => {
    if (event && event.target === event.currentTarget) resolveConfirm(false);
  });

  // --- 全局键盘 ---
  if (typeof document.addEventListener === 'function') {
    document.addEventListener('keydown', (event) => {
      if (!event || event.key !== 'Escape') return;
      const confirmBackdrop = $('confirmBackdrop');
      if (confirmBackdrop && confirmBackdrop.hidden === false) {
        resolveConfirm(false);
        return;
      }
      const editorBackdrop = $('editorBackdrop');
      if (editorBackdrop && editorBackdrop.hidden === false) closeEditor();
    });
  }
}

/** 「全部刷新」：并行刷新三个标签页的数据。 */
async function onRefreshAll() {
  const refreshBtn = $('refreshAllBtn');
  await runWithLoading(refreshBtn, '正在刷新全部数据…', async () => {
    const results = await Promise.allSettled([
      loadItems(),
      loadShop(),
      loadOverview(),
    ]);
    const failed = results.filter((r) => r.status === 'rejected').length;
    if (failed === 0) toast('success', '已刷新全部数据');
    else toast('error', `刷新完成，但有 ${failed} 项数据获取失败，请查看上方提示。`);
  });
}

/* ==========================================================================
 * 12. 离线演示桥接（仅在 window.AstrBotPluginPage 缺失时启用）
 * --------------------------------------------------------------------------
 * 目的：让 UI 在没有 AstrBot 的环境里也能预览/调试。数据全部存在内存中，
 * 刷新页面即重置；页面顶部的黄色提示条会明确告知当前处于演示模式。
 * ========================================================================== */

const OFFLINE_ITEMS = [
  {
    id: 'cat_food',
    name: '猫粮',
    emoji: '🍚',
    category: 'food',
    description: '普通的猫粮，能填饱肚子。',
    base_price: 20,
    price_fluctuation: 0.2,
    shop_enabled: true,
    stock_min: 5,
    stock_max: 20,
    effect: { satiety: 30, hydration: 0, energy: 0, health: 0, revive: false },
    tradable: true,
    enabled: true,
  },
  {
    id: 'fresh_water',
    name: '清水',
    emoji: '💧',
    category: 'drink',
    description: '干净的水，补充水分最有效。',
    base_price: 8,
    price_fluctuation: 0.1,
    shop_enabled: true,
    stock_min: 8,
    stock_max: 30,
    effect: { satiety: 0, hydration: 35, energy: 0, health: 0, revive: false },
    tradable: true,
    enabled: true,
  },
  {
    id: 'health_potion',
    name: '复原药剂',
    emoji: '🧪',
    category: 'medicine',
    description: '苦涩的药剂，但能快速恢复健康。',
    base_price: 120,
    price_fluctuation: 0.25,
    shop_enabled: true,
    stock_min: 2,
    stock_max: 6,
    effect: { satiety: 0, hydration: 0, energy: 10, health: 40, revive: false },
    tradable: true,
    enabled: true,
  },
  {
    id: 'phoenix_feather',
    name: '不死鸟之羽',
    emoji: '🪶',
    category: 'special',
    description: '传说中的羽毛，可以让死去的猫娘复活。',
    base_price: 800,
    price_fluctuation: 0.05,
    shop_enabled: false,
    stock_min: 1,
    stock_max: 2,
    effect: { satiety: 0, hydration: 0, energy: 0, health: 0, revive: true },
    tradable: true,
    enabled: true,
  },
  {
    id: 'yarn_ball',
    name: '毛线球',
    emoji: '🧶',
    category: 'toy',
    description: '玩一会儿心情会变好，但也挺费精力。',
    base_price: 35,
    price_fluctuation: 0.3,
    shop_enabled: true,
    stock_min: 3,
    stock_max: 12,
    effect: { satiety: 0, hydration: 0, energy: -5, health: 5, revive: false },
    tradable: true,
    enabled: true,
  },
  {
    id: 'old_ribbon',
    name: '旧丝带',
    emoji: '🎀',
    category: 'special',
    description: '已经下架的纪念品。',
    base_price: 50,
    price_fluctuation: 0.1,
    shop_enabled: false,
    stock_min: 0,
    stock_max: 0,
    effect: { satiety: 0, hydration: 0, energy: 0, health: 0, revive: false },
    tradable: false,
    enabled: false,
  },
];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

const offlineBridge = (() => {
  let items = clone(OFFLINE_ITEMS);
  let generated = 0;
  let shop = null;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function buildShop() {
    const today = new Date();
    const date = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    const entries = items
      .filter((it) => it.enabled !== false && it.shop_enabled !== false && toInt(it.stock_max, 0) > 0)
      .map((it) => {
        const fluctuation = toFloat(it.price_fluctuation, 0);
        const factor = 1 + (Math.random() * 2 - 1) * fluctuation;
        const stockInitial = Math.max(1, toInt(it.stock_max, 1));
        return {
          item_id: it.id,
          name: it.name,
          emoji: it.emoji,
          category: it.category,
          price: Math.max(1, Math.round(toInt(it.base_price, 1) * factor)),
          base_price: toInt(it.base_price, 0),
          stock: Math.max(1, Math.round(stockInitial * (0.4 + Math.random() * 0.6))),
          stock_initial: stockInitial,
        };
      });
    return { date, refresh_hour: 4, entries };
  }

  shop = buildShop();

  const overview = {
    players: 3,
    catgirls: 5,
    alive: 4,
    dead: 1,
    marriages: 1,
    listings: 2,
    total_coins: 1234,
    shop_date: shop.date,
    top_players: [
      { name: '某人', coins: 800, catgirls: 2 },
      { name: '示例玩家', coins: 300, catgirls: 1 },
      { name: '路人甲', coins: 134, catgirls: 0 },
    ],
  };

  return {
    async ready() {
      await sleep(80);
      return {
        pluginName: 'neko_han',
        displayName: '猫娘养成（离线预览）',
        pageName: 'panel',
        pageTitle: '道具与商城管理面板',
        locale: 'zh-CN',
        i18n: {},
        isDark: null, // 保持页面自身的 data-theme，不强行切换
      };
    },
    getContext() {
      return { pluginName: 'neko_han', pageName: 'panel' };
    },
    getLocale() {
      return 'zh-CN';
    },
    t(text) {
      return text;
    },
    onContext() {
      return () => {};
    },
    async apiGet(endpoint) {
      await sleep(140);
      if (endpoint === 'items') {
        return {
          items: clone(items),
          categories: DEFAULT_CATEGORIES.slice(),
          effect_keys: DEFAULT_EFFECT_KEYS.slice(),
        };
      }
      if (endpoint === 'shop') return clone(shop);
      if (endpoint === 'overview') return clone(overview);
      throw new Error(`离线预览模式不支持接口：GET ${endpoint}`);
    },
    async apiPost(endpoint, body) {
      await sleep(140);
      const payload = body && typeof body === 'object' ? body : {};

      if (endpoint === 'items/save') {
        const hasId = typeof payload.id === 'string' && payload.id.trim() !== '';
        const id = hasId ? payload.id.trim() : `custom_item_${++generated}`;
        const index = items.findIndex((it) => it.id === id);
        const merged = Object.assign(
          {
            description: '',
            base_price: 0,
            price_fluctuation: 0,
            shop_enabled: true,
            stock_min: 0,
            stock_max: 0,
            effect: { satiety: 0, hydration: 0, energy: 0, health: 0, revive: false },
            tradable: true,
            enabled: true,
            emoji: '📦',
          },
          index >= 0 ? items[index] : {},
          payload,
          { id },
        );
        merged.effect = Object.assign(
          { satiety: 0, hydration: 0, energy: 0, health: 0, revive: false },
          merged.effect || {},
        );
        if (index >= 0) items[index] = merged;
        else items.push(merged);
        return { saved: true, created: index < 0, item: clone(merged) };
      }

      if (endpoint === 'items/delete') {
        const id = payload.id;
        const index = items.findIndex((it) => it.id === id);
        if (index < 0) throw new Error(`道具不存在：${id}`);
        items.splice(index, 1);
        return { deleted: true };
      }

      if (endpoint === 'items/reset') {
        items = clone(OFFLINE_ITEMS);
        shop = buildShop();
        return { reset: true, items: clone(items) };
      }

      if (endpoint === 'shop/refresh') {
        shop = buildShop();
        overview.shop_date = shop.date;
        return clone(shop);
      }

      if (endpoint === 'players/grant') {
        const playerId = String(payload.player_id || '');
        const amount = toInt(payload.amount, 0);
        const player = overview.top_players.find((p) => p.name === playerId);
        if (!player) throw new Error(`找不到玩家：${playerId}（离线演示只认识 ${overview.top_players.map((p) => p.name).join('、')}）`);
        player.coins += amount;
        overview.total_coins += amount;
        return { player_id: playerId, coins: player.coins };
      }

      throw new Error(`离线预览模式不支持接口：POST ${endpoint}`);
    },
  };
})();

/* ==========================================================================
 * 13. 启动
 * ========================================================================== */

let booted = false;

async function init() {
  if (booted) return;
  booted = true;

  if (OFFLINE) {
    const banner = $('offlineBanner');
    if (banner) banner.hidden = false;
  }

  ensureThemeAttribute();
  setupEvents();
  switchTab(state.activeTab);
  fillCategoryOptions();
  applyEffectKeyVisibility();
  renderItems();
  renderShop();
  renderOverview();

  await connectContext();

  // 三个标签页数据并行加载；单个失败只影响对应区域，不会中断其它请求
  await Promise.allSettled([loadItems(), loadShop(), loadOverview()]);
}

// 模块顶层只做一件事：启动。任何异常都在内部消化，绝不让页面白屏。
init().catch((err) => {
  toast('error', `面板初始化失败：${message(err)}`);
});

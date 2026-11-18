/* 单页前端：规划 / 地图 / 预算 / 编辑 / 导出
 * 全在线架构：POST /api/trip/plan 由后端调用高德 Web 服务 + LLM 返回 TripPlan JSON。
 * 后端缺 Key 时接口返回 400，错误详情会直接显示在页面状态栏。
 * 地图：加载高德 JS API 真实渲染，未配置 Key 时提示配置而非降级为离线地图。
 */
const API_PORT = 8000;
/* 后端地址可配置：
   - window.API_BASE 是字符串时直接使用。空串 "" 表示"同源部署"——后端托管前端时
     API 就在同一域下，用相对路径 /api/... 即可。
   - 未定义时回退到 127.0.0.1:8000（前后端分开运行的场景）。
   必须用 typeof 判断：空字符串是合法的"同源"配置，写 || 会被误判成未配置。 */
const API_BASE =
  typeof window.API_BASE === "string" ? window.API_BASE : `http://127.0.0.1:${API_PORT}`;
const API = `${API_BASE}/api/trip/plan`;
const state = { plan: null };

const $ = (id) => document.getElementById(id);

/* 浏览器级会话标识：没有登录系统时用它隔离查询历史。
   不用 fingerprint / cookie，避免引入隐私争议；清 localStorage 即等于清空自己的历史关联。 */
function getSessionId() {
  try {
    let sid = localStorage.getItem("trip_session_id");
    if (!sid) {
      sid = "s_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("trip_session_id", sid);
    }
    return sid;
  } catch (e) {
    return "";  // 隐私模式下 localStorage 不可用，退化为不隔离
  }
}

/* HTML 转义：名称/优缺点等文本来自外部数据，插入 innerHTML 前统一转义 */
const esc = (s) =>
  String(s ?? "").replace(
    /[<>&]/g,
    (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c])
  );

/* 规划失败：在结果区显示明确错误卡片，替换掉"填写左表…"空状态引导。
   以前只在左侧 status 写一句提示，右侧 #empty 仍显示，两句叠在一起像没处理干净。
   成功后 renderAll() 会把结果区重新填回，无需手动恢复。 */
function showPlanError(msg) {
  const empty = $("empty");
  if (empty) {
    empty.hidden = false;
    empty.classList.add("plan-error");
    empty.textContent = "规划失败：" + msg;
  }
  const result = $("result");
  if (result) result.hidden = true;
}

/* 属性值转义：URL 之类要放进 src/href 属性，必须连引号一起转义 */
const escAttr = (s) =>
  String(s ?? "").replace(/[<>&"']/g, (c) => ({
    "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;",
  }[c]));

/* 触摸设备判定：手机上不劫持地图手势，保证页面能正常上下滚动 */
function isTouchDevice() {
  return window.matchMedia("(hover: none), (pointer: coarse)").matches;
}

/* 演示快照兜底：公网静态部署时后端不可达，用预生成的真实数据展示。
   命中规则：先精确匹配城市名，再退化为包含匹配。 */
function lookupDemo(dest) {
  const d = window.DEMO_DATA;
  if (!d || !dest) return null;
  if (d[dest]) return d[dest];
  const key = Object.keys(d).find((k) => dest.includes(k) || k.includes(dest));
  return key ? d[key] : null;
}

/* 未预置演示数据的城市：用北京快照做模板生成一份"占位行程"，
   保证静态部署下任意城市都能演示（内容非该城市真实数据，界面会明确标注）。 */
function makeGenericDemo(dest, startDate) {
  const tpl = (window.DEMO_DATA || {})["北京"];
  if (!tpl) return null;
  const plan = JSON.parse(JSON.stringify(tpl));
  plan.destination = dest;
  plan.overview =
    `为您规划了${dest}${plan.days}天行程（演示占位）。该城市暂无预置的真实快照，` +
    `以下景点/酒店为示例模板，接入后端后将展示 ${dest} 的真实规划结果。`;
  const base = startDate || (plan.days_plan && plan.days_plan[0] && plan.days_plan[0].date);
  return shiftDemoDates(plan, base);
}

function showDemoBadge(plan) {
  let b = $("demo-badge");
  if (!b) {
    b = document.createElement("div");
    b.id = "demo-badge";
    b.className = "demo-badge";
    $("result").prepend(b);
  }
  b.hidden = false;
  const city = plan.destination || "";
  const ds = (plan.days_plan || []).map((d) => d.date).filter(Boolean);
  const range = ds.length ? `（${ds[0]}${ds.length > 1 ? " ~ " + ds[ds.length - 1] : ""}）` : "";
  if (plan.__dateShifted) {
    b.textContent =
      `📌 演示快照 · ${city} ${range}：景点/酒店/路线为真实高德数据预生成，` +
      `日期已按你选的出发日对齐；天气为快照原日期${
        plan.__origFirstDate ? "（" + plan.__origFirstDate.slice(5) + " 起）" : ""
      }的真实预报，不是你选日期的天气（实时天气请用本地版）。`;
  } else {
    b.textContent =
      `📌 演示快照 · ${city} ${range}：真实高德数据预生成，非实时规划。` +
      (range ? "日期为快照固定值，不随你填写的出发日期变化。" : "");
  }
}

function hideDemoBadge() {
  const b = $("demo-badge");
  if (b) b.hidden = true;
}

function addDays(dateStr, n) {
  // 用本地时间取年月日，不能用 toISOString（会按 UTC 换算，东八区会整体少一天）
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/* 把演示快照的日期对齐到用户选择的出发日。

   行程内容（景点/酒店/路线/预算）与"具体哪一天"无关，平移日期是成立的；
   但天气绑定具体日期，平移后不再对应当天 —— 保留天气数据，由前端标注它属于快照原日期，
   既不让人误读，也不至于让天气功能凭空消失。
   必须深拷贝，否则会污染 window.DEMO_DATA，导致多次查询时日期被反复累加偏移。 */
function shiftDemoDates(plan, startDate) {
  const copy = JSON.parse(JSON.stringify(plan));
  const dates = (copy.days_plan || []).map((d) => d.date).filter(Boolean);
  if (!startDate || !dates.length) return copy;
  const first = dates[0];
  if (startDate === first) return copy;
  const offset = Math.round(
    (new Date(startDate + "T00:00:00") - new Date(first + "T00:00:00")) / 86400000
  );
  if (!offset) return copy;
  copy.days_plan.forEach((d) => {
    if (d.date) d.date = addDays(d.date, offset);
  });
  copy.__dateShifted = true;
  copy.__origFirstDate = first;
  return copy;
}

document.getElementById("plan-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("status");
  const btn = $("submit-btn");
  if (btn.disabled) return; // 防重复提交：避免连续点击刷爆高德配额与 LLM 额度
  btn.disabled = true;
  btn.textContent = "生成中…";
  status.textContent = "生成中…";
  // 清掉上一次规划失败留下的错误卡片，避免与本次结果/空状态叠加
  const prevErr = $("empty");
  if (prevErr) { prevErr.classList.remove("plan-error"); prevErr.textContent = "填写左侧表单并点击「生成行程」开始。"; }
  const payload = {
    destination: $("destination").value.trim(),
    days: parseInt($("days").value, 10) || 3,
    preferences: $("preferences").value.split(",").map((s) => s.trim()).filter(Boolean),
    budget_limit: $("budget_limit").value ? Number($("budget_limit").value) : null,
    start_date: $("start-date").value || null,
    // 交通与住宿偏好：后端据此选择路线规划模式与酒店候选池，不是纯展示字段
    transportation: $("transportation") ? $("transportation").value : "",
    accommodation: $("accommodation") ? $("accommodation").value : "",
    // 查询历史隔离：无登录系统时用浏览器级随机 ID，localStorage 持久化
    session_id: getSessionId(),
  };
  startProgress();
  try {
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      // 422 参数校验失败时 detail 是数组，直接拼接会显示 [object Object]
      const d = err.detail;
      const msg = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("；") : d;
      throw new Error(msg || "HTTP " + res.status);
    }
    state.plan = await res.json();
    state.demo = false;
    hideDemoBadge();
    renderAll();
    status.textContent = "完成 ✅";
    loadHistory();  // 后端已落库，刷新历史面板
  } catch (err) {
    // 后端不可达（公网静态部署场景）→ 回退到内置演示快照
    const demo = lookupDemo(payload.destination);
    if (demo) {
      // 演示快照：日期对齐到用户选的出发日（行程内容不变；天气保留并标注原日期）
      const plan = shiftDemoDates(demo, payload.start_date);
      state.plan = plan;
      state.demo = true;
      renderAll();
      showDemoBadge(plan);
      const dateNote = plan.__dateShifted
        ? ` 日期已对齐到 ${payload.start_date}；天气为快照原日期的真实预报。`
        : "";
      status.textContent = "完成（演示快照）✅" + dateNote;
    } else {
      // 未预置城市：用模板生成占位行程，保证任意城市都能演示
      const generic = makeGenericDemo(payload.destination, payload.start_date);
      if (generic) {
        state.plan = generic;
        state.demo = true;
        renderAll();
        showDemoBadge(generic);
        status.textContent = "完成（演示占位，非该城市真实数据）✅";
      } else {
        const names = Object.keys(window.DEMO_DATA || {}).join("、");
        const msg = names
          ? `${err.message}。演示版支持预置城市：${names}`
          : `${err.message}（确认后端已启动于 ${API_PORT} 端口）`;
        status.textContent = "失败 ❌";
        showPlanError(msg);
      }
    }
  } finally {
    stopProgress();
    btn.disabled = false;
    btn.textContent = "生成行程";
  }
});

/* ---------- 加载进度（分阶段提示） ----------
   规划一次要串行走「搜景点 → 查天气 → 选酒店 → 生成行程」，
   真实耗时可达十几秒。只显示一个静态「生成中…」会让人以为卡死，
   因此按阶段推进进度并说明当前在做什么。进度只是动画提示，
   真实完成以请求返回为准，绝不让进度条伪造完成状态。 */
const PROGRESS_STAGES = [
  [10, "🔍 正在搜索景点…"],
  [30, "🌤️ 正在查询天气…"],
  [50, "🏨 正在筛选酒店…"],
  [70, "📋 正在生成行程计划…"],
  [88, "🗺️ 正在规划路线…"],
];
let progressTimer = null;

function startProgress() {
  const wrap = $("progress-wrap");
  const fill = $("progress-fill");
  const text = $("progress-text");
  if (!wrap || !fill || !text) return;
  wrap.hidden = false;
  let pct = 0;
  let stage = 0;
  fill.style.width = "0%";
  text.textContent = PROGRESS_STAGES[0][1];
  progressTimer = setInterval(function () {
    pct += 2;
    // 上限 95%：剩下 5% 留给真实返回，避免进度条先到 100% 而结果还没回来
    if (pct > 95) pct = 95;
    fill.style.width = pct + "%";
    if (stage < PROGRESS_STAGES.length && pct >= PROGRESS_STAGES[stage][0]) {
      text.textContent = PROGRESS_STAGES[stage][1];
      stage += 1;
    }
  }, 350);
}

function stopProgress() {
  const wrap = $("progress-wrap");
  const fill = $("progress-fill");
  if (progressTimer) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
  if (fill) fill.style.width = "100%";
  if (wrap) {
    // 短暂展示 100% 后收起，给用户「确实完成了」的反馈
    setTimeout(function () {
      wrap.hidden = true;
    }, 400);
  }
}

/* ---------- 渲染 ---------- */
function renderAll() {
  const p = state.plan;
  window.__planReady = true;
  $("result").hidden = false;
  $("empty").hidden = true;
  $("empty").classList.remove("plan-error");
  $("overview").textContent = p.overview;
  renderBudget();
  renderHotels();
  renderWeather();
  renderDays();
  renderMap();
}

/* 天气独立区块：原来天气只在每日行程标题里夹带一行，
   单独成块后一眼能看到全程天气，超出预报范围的天数也明确说明原因。 */
function renderWeather() {
  const p = state.plan;
  const wrap = $("weather-container");
  if (!wrap) return;
  const days = p.days_plan || [];
  if (!days.length) {
    wrap.innerHTML = "<p class='hint'>暂无天气数据。</p>";
    return;
  }
  const shifted = !!p.__dateShifted;
  const cards = days
    .map((d) => {
      const w = d.weather;
      const label = d.date ? d.date.slice(5) : `第${d.day}天`;
      if (!w || !w.desc) {
        return `<div class="weather-card empty"><span class="wd">${label}</span>
          <span class="wt">暂无预报</span></div>`;
      }
      const orig = shifted && w.date ? `<small>（${w.date.slice(5)} 数据）</small>` : "";
      return `<div class="weather-card"><span class="wd">${label}</span>
        <span class="wt">${esc(w.desc)}</span>${orig}</div>`;
    })
    .join("");
  const note = days.some((d) => !d.weather || !d.weather.desc)
    ? `<div class="weather-note">⚠️ 高德免费预报仅覆盖约未来 4 天，超范围的日期留空，不做任何估算。</div>`
    : "";
  wrap.innerHTML = `<div class="weather-row">${cards}</div>${note}`;
}

/* 渲染多个酒店选项：星级 / 评分 / 房价 / 位置便利度 / 优缺点 */
function renderHotels() {
  const p = state.plan;
  const wrap = $("hotels-container");
  if (!wrap) return;
  const list = p.hotels && p.hotels.length ? p.hotels : p.hotel ? [p.hotel] : [];
  if (!list.length) {
    wrap.innerHTML = "<p class='hint'>本次未返回酒店数据。</p>";
    return;
  }
  wrap.innerHTML = list
    .map((h, i) => {
      const star = h.star == null ? null : Math.max(0, Math.min(5, h.star));
      const stars = star == null ? "星级待核实" : "★".repeat(star) + "☆".repeat(5 - star);
      // 高德免费版不返回房价/星级，此时标为「参考」，避免把模型估值当真实价格展示
      const priceEst = h.price_source === "llm_estimate";
      const starEst = h.star_source === "llm_estimate";
      const rating = h.rating ? `<span class="hotel-tag score">⭐ ${h.rating}</span>` : "";
      const dist =
        h.distance_to_sights_km != null
          ? `<span class="hotel-tag dist">📍 距景点群均 ${h.distance_to_sights_km} km</span>`
          : "";
      // 房价为 0/缺失时不能显示 "¥0/晚"（看起来像 bug），明确写"价格待询"
      const price = Number(h.price_per_night) || 0;
      const priceHtml =
        price > 0
          ? priceEst
            ? `<span class="hotel-price est" title="高德未返回实时房价">¥${price}<small>/晚 · 参考价</small></span>`
            : `<span class="hotel-price">¥${price}<small>/晚</small></span>`
          : `<span class="hotel-price unknown" title="高德免费接口不返回酒店房价">价格待询</span>`;
      const starHtml = `<span class="hotel-star" title="${star == null ? "高德未提供星级" : `${star} 星级`}">${stars}${
        starEst ? `<small class="est-tag">参考</small>` : ""
      }</span>`;
      const pros = (h.pros || []).length
        ? `<ul class="pc pros">${h.pros.map((x) => `<li>＋ ${esc(x)}</li>`).join("")}</ul>`
        : "";
      const cons = (h.cons || []).length
        ? `<ul class="pc cons">${h.cons.map((x) => `<li>－ ${esc(x)}</li>`).join("")}</ul>`
        : "";
      const addr = (h.location && h.location.address) || "";
      const estNote =
        priceEst || starEst || price <= 0 || star == null
          ? `<div class="hotel-note">⚠️ 高德未提供实时房价或星级；显示「待询/待核实」，请以预订平台实际房型价格为准</div>`
          : "";
      return `<div class="hotel-card${i === 0 ? " recommended" : ""}">
        <div class="hotel-head">
          <span class="hotel-name">${i === 0 ? "🏆 推荐 · " : ""}${esc(h.name)}</span>
          ${starHtml}
        </div>
        <div class="hotel-meta">
          ${priceHtml}${rating}${dist}
        </div>
        ${addr ? `<div class="hotel-addr">${esc(addr)}</div>` : ""}
        ${pros || cons ? `<div class="hotel-pc">${pros}${cons}</div>` : ""}
        ${estNote}
      </div>`;
    })
    .join("");
}

function renderBudget() {
  const b = state.plan.budget;
  // 高德不返回实时房价时住宿费为 0，直接显示「¥0」会让人误以为住宿免费，
  // 必须显式说明未计入，否则合计会明显低于真实花费。
  const hotelUnknown = !Number(b.hotel);
  const hotelTxt = hotelUnknown ? "未计入 · 价格待询" : `¥${b.hotel}`;
  const note = hotelUnknown
    ? `<div class="budget-note">⚠️ 住宿费未计入：数据源未提供实时房价，合计低于真实花费</div>`
    : "";
  $("budget-container").innerHTML = `
    <div class="row"><span>门票</span><span>¥${b.attractions}</span></div>
    <div class="row"><span>酒店(${state.plan.days - 1 || 1}晚)</span><span class="${
    hotelUnknown ? "budget-unknown" : ""
  }">${hotelTxt}</span></div>
    <div class="row"><span>餐饮</span><span>¥${b.meals}</span></div>
    <div class="row"><span>交通</span><span>¥${b.transport}</span></div>
    <div class="row total"><span>合计</span><span>¥${b.total}</span></div>${note}`;
}

function attractionById(id) {
  return state.plan.attractions.find((a) => a.id === id);
}
function mealById(id) {
  return state.plan.meals.find((m) => m.id === id);
}

function renderDays() {
  const p = state.plan;
  const wrap = $("days-container");
  wrap.innerHTML = "";
  p.days_plan.forEach((day) => {
    const card = document.createElement("div");
    card.className = "day-card";
    const attrItems = day.attraction_ids
      .map((id) => attractionById(id))
      .filter(Boolean)
      .map((a, i, arr) => itemRow(a, "attraction", day.day, i, arr.length))
      .join("");
    const mealItems = day.meal_ids
      .map((id) => mealById(id))
      .filter(Boolean)
      .map((m) => {
        const row = document.createElement("div");
        row.className = "item";
        row.innerHTML = `<span class="name">🍴 ${m.name}（${m.meal_type === "lunch" ? "午" : "晚"}餐 · ${m.cuisine}）</span>
          <span class="meta">⭐${m.rating != null ? m.rating : "—"} · ¥${m.price_per_person}/人</span>`;
        return row.outerHTML;
      })
      .join("");
    const w = day.weather;
    // 日期被平移过时，标注天气属于快照原日期，避免让人误以为是所选日期的天气
    const shifted = state.plan && state.plan.__dateShifted;
    const wText = w && w.desc
      ? `🌤️ ${w.desc}${shifted && w.date ? `（${w.date.slice(5)} 数据）` : ""}`
      : "";
    // 某天没有景点的空白日（如候选景点不足天数）补一行说明，避免出现空白卡片
    const freeNote = day.attraction_ids.length
      ? ""
      : `<div class="item free">${day.summary || "自由活动 / 市区休闲"}</div>`;
    const routeText = routeSummary(day.route);
    card.innerHTML = `<h4>第 ${day.day} 天${day.date ? " · " + day.date : ""}${wText ? " · " + wText : ""}</h4>${routeText}${attrItems}${mealItems}${freeNote}`;
    wrap.appendChild(card);
  });
}

/* 当日路线摘要：来自高德路径规划的真实步行距离与用时 */
function routeSummary(route) {
  if (!route || !route.legs || !route.legs.length) return "";
  const km = (route.total_distance_m / 1000).toFixed(1);
  const mins = Math.round(route.total_duration_s / 60);
  const mode =
    route.mode === "driving" ? "驾车" : route.mode === "walking" ? "步行" : "混合交通";
  const steps = route.legs
    .map(
      (l) =>
        `${l.mode === "driving" ? "🚗" : "🚶"} ${esc(l.from_name)}→${esc(l.to_name)} ${(
          l.distance_m / 1000
        ).toFixed(1)}km`
    )
    .join("；");
  return `<div class="route-line">🗺️ ${mode}全程约 ${km} km · ${mins} 分钟<span class="route-steps">${steps}</span></div>`;
}

function itemRow(a, type, day, i, len) {
  const row = document.createElement("div");
  row.className = "item";
  // 景点配图来自高德 POI 照片；加载失败（403/跨域）时直接隐藏，不留破图占位
  const img = a.image_url
    ? `<img class="item-img" src="${escAttr(a.image_url)}" alt="" loading="lazy"
         onerror="this.style.display='none'">`
    : "";
  // 调序/删除按钮只在编辑模式出现，浏览时避免误触
  const move = state.editMode
    ? `<div class="move">
        <button data-act="up" data-id="${a.id}" data-day="${day}" ${i === 0 ? "disabled" : ""}>▲</button>
        <button data-act="down" data-id="${a.id}" data-day="${day}" ${i === len - 1 ? "disabled" : ""}>▼</button>
      </div>`
    : "";
  const del = state.editMode
    ? `<button class="del" data-act="del" data-id="${a.id}">删除</button>`
    : "";
  row.innerHTML = `
    ${move}${img}
    <span class="name">📍 ${esc(a.name)}</span>
    <span class="meta">${esc(a.category)} · ⭐${a.rating != null ? a.rating : "—"} · ¥${a.ticket_price} · ${a.visit_duration_hours}h</span>
    ${del}`;
  return row.outerHTML;
}

/* ---------- 编辑（需求 4） ----------
   进入编辑模式后才出现调序/删除按钮，避免浏览时误触；
   进入时深拷贝一份原始计划，「取消」可完整回滚。
   删除只把景点从当天移除，保留在候选池里，因此可以随时加回来。 */
function setEditMode(on) {
  state.editMode = on;
  const edit = $("btn-edit");
  const add = $("btn-add");
  const save = $("btn-save");
  const cancel = $("btn-cancel");
  if (edit) edit.hidden = on;
  [add, save, cancel].forEach((b) => {
    if (b) b.hidden = !on;
  });
  renderDays();
}

function enterEdit() {
  // 深拷贝原始计划：取消时整体回滚，避免逐个字段还原漏掉
  state.originalPlan = JSON.parse(JSON.stringify(state.plan));
  setEditMode(true);
}

function saveEdit() {
  state.originalPlan = null;
  setEditMode(false);
  recomputeBudget();
  renderBudget();
  renderMap();
  const st = $("status");
  if (st) st.textContent = "已保存修改 ✅";
}

function cancelEdit() {
  if (state.originalPlan) {
    state.plan = state.originalPlan;
    state.originalPlan = null;
  }
  setEditMode(false);
  renderBudget();
  renderWeather();
  renderHotels();
  renderMap();
  const st = $("status");
  if (st) st.textContent = "已取消修改 ↩️";
}

/* 未被安排进任何一天的景点 —— 删除只是移出当天，它们仍在这里，可随时加回 */
function unassignedAttractions() {
  const p = state.plan;
  const used = new Set();
  (p.days_plan || []).forEach((d) => (d.attraction_ids || []).forEach((i) => used.add(i)));
  return (p.attractions || []).filter((a) => !used.has(a.id));
}

function renderAddPanel() {
  const pool = unassignedAttractions();
  const host = $("add-panel");
  if (!host) return;
  if (!pool.length) {
    host.innerHTML =
      "<div class='add-panel'>暂无可添加景点（所有景点都已安排在行程中）。</div>";
    host.hidden = false;
    return;
  }
  const opts = pool
    .map((a) => `<option value="${a.id}">${esc(a.name)}</option>`)
    .join("");
  const dayOpts = (state.plan.days_plan || [])
    .map(
      (d) =>
        `<option value="${d.day}">第 ${d.day} 天${d.date ? "（" + d.date.slice(5) + "）" : ""}</option>`
    )
    .join("");
  host.innerHTML = `<div class="add-panel">
      <label>景点
        <select id="add-attr">${opts}</select>
      </label>
      <label>加入到
        <select id="add-day">${dayOpts}</select>
      </label>
      <button type="button" id="add-confirm">确认添加</button>
      <button type="button" id="add-close">关闭</button>
    </div>`;
  host.hidden = false;
}

$("btn-edit") && $("btn-edit").addEventListener("click", enterEdit);
$("btn-save") && $("btn-save").addEventListener("click", saveEdit);
$("btn-cancel") && $("btn-cancel").addEventListener("click", cancelEdit);
$("btn-add") &&
  $("btn-add").addEventListener("click", () => {
    const host = $("add-panel");
    if (!host) return;
    // 再次点击收起，避免面板一直挂着
    if (!host.hidden) {
      host.hidden = true;
      return;
    }
    renderAddPanel();
  });

$("add-panel") &&
  $("add-panel").addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.id === "add-close") {
      $("add-panel").hidden = true;
      return;
    }
    if (!(t && t.id === "add-confirm")) return;
    const aid = $("add-attr") && $("add-attr").value;
    const day = parseInt($("add-day") && $("add-day").value, 10);
    const p = state.plan;
    const dayPlan = (p.days_plan || []).find((d) => d.day === day);
    if (!aid || !dayPlan) return;
    dayPlan.attraction_ids.push(aid);
    recomputeBudget();
    renderBudget();
    renderDays();
    renderMap();
    $("add-panel").hidden = true;
  });

$("days-container").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { act, id, day } = btn.dataset;
  const p = state.plan;
  const dayPlan = p.days_plan.find((d) => String(d.day) === day);
  if (act === "del") {
    // 仅移出当天：景点仍在候选池，可通过「添加景点」加回，避免误删无法恢复
    dayPlan.attraction_ids = dayPlan.attraction_ids.filter((x) => x !== id);
  } else if (act === "up" || act === "down") {
    const arr = dayPlan.attraction_ids;
    const idx = arr.indexOf(id);
    const swap = act === "up" ? idx - 1 : idx + 1;
    if (swap >= 0 && swap < arr.length) [arr[idx], arr[swap]] = [arr[swap], arr[idx]];
  }
  recomputeBudget();
  renderBudget();
  renderDays();
  renderMap();
});

function recomputeBudget() {
  const p = state.plan;
  const attr = p.attractions.reduce((s, a) => s + (a.ticket_price || 0), 0);
  const meals = p.meals.reduce((s, m) => s + (m.price_per_person || 0), 0);
  const hotel0 = p.hotels && p.hotels.length ? p.hotels[0] : p.hotel;
  const hotel = hotel0 ? hotel0.price_per_night * Math.max(p.days - 1, 1) : 0;
  const transport = 50 * p.days;
  p.budget = {
    attractions: +attr.toFixed(2),
    meals: +meals.toFixed(2),
    hotel: +hotel.toFixed(2),
    transport: +transport.toFixed(2),
    total: +(attr + meals + hotel + transport).toFixed(2),
    currency: "CNY",
  };
}

/* ---------- 地图可视化（需求 2） ---------- */
/* 渐进增强：先用真实经纬度画 SVG 示意图，保证地图区**永远有内容**；
   只有当高德脚本就绪且地图真正 complete 之后，才用真实底图替换它。
   这样无论 Key 配额/白名单/CDN 出什么问题，最坏结果也只是"显示示意图"，不会白板。 */
function renderMap() {
  const p = state.plan;
  if (!p || !p.attractions || !p.attractions.length) {
    $("map-container").innerHTML =
      "<div class='map-missing'>暂无可显示的景点<br><span>生成行程后这里会显示地图</span></div>";
    return;
  }

  // 1) 无条件先画 SVG —— 这是保底，不依赖任何外部服务
  try {
    renderSvgMap(p);
  } catch (e) {
    $("map-container").innerHTML =
      "<div class='map-missing'>示意图渲染失败<br><span>行程、预算、导出功能不受影响</span></div>";
  }

  // 2) 高德可用时才升级为真实底图；失败静默保留 SVG，并由 hint 说明原因
  if (window.__amapReady && window.AMap && !window.__amapBroken) {
    try {
      renderAmap(p);
    } catch (e) {
      window.__amapBroken = true;
      window.__amapLastError = e && e.message ? e.message : String(e);
      showMapHint("amap-exception");
    }
  }
}

/* 统一的地图状态提示：把"为什么没有真实底图"讲清楚，而不是静默白板。
   reason: no-key / load-failed / amap-exception / timeout / ok */
function showMapHint(reason) {
  var hint = document.getElementById("map-hint");
  if (!hint) return;
  var base = {
    "no-key": "未配置高德「Web 端(JS API)」Key，当前显示示意图（真实经纬度）。",
    "load-failed": "高德地图脚本加载失败（网络或 CDN），当前显示示意图。",
    "amap-exception": "高德地图初始化异常，多为 Key 配额用尽 / 域名不在白名单 / 安全密钥不匹配，当前显示示意图。",
    "timeout": "高德底图在限定时间内未就绪（配额或鉴权问题），当前显示示意图。",
    "ok": "已加载高德真实地图。",
  }[reason] || "当前显示示意图。";
  var detail = window.__amapLastError ? " 错误：" + String(window.__amapLastError).slice(0, 90) : "";
  hint.textContent = base + detail;
  hint.dataset.reason = reason;
  // textContent 赋值会清空子节点，所以这里 append 不会叠加；
  // 但防御性地判一次，避免将来改成 innerHTML 时出现多个重试按钮。
  if (reason !== "ok" && !hint.querySelector("button[data-map-retry]")) {
    hint.appendChild(makeRetryButton());
  }
}

/* 失败时给一个手动重试入口：__amapBroken 不该把用户永久锁死在示意图。 */
function makeRetryButton() {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.dataset.mapRetry = "1";
  btn.textContent = "重试真实地图";
  btn.style.cssText = "margin-left:10px;padding:2px 8px;font-size:12px;cursor:pointer";
  btn.onclick = function () {
    window.__amapBroken = false;
    window.__amapLastError = "";
    window.__amapReady = false;
    if (window.__map) { try { window.__map.destroy(); } catch (e) {} window.__map = null; }
    // 重新注入高德脚本，绕开浏览器对同一 src 的失败缓存
    var key = window.AMAP_WEB_KEY;
    if (!key || String(key).startsWith("your_")) { showMapHint("no-key"); return; }
    var s = document.createElement("script");
    s.src = "https://webapi.amap.com/maps?v=2.0&key=" + encodeURIComponent(key) + "&_t=" + Date.now();
    s.onload = function () {
      window.__amapReady = true;
      if (state.plan) renderMap();
    };
    s.onerror = function () { window.__amapLastError = "脚本重新加载失败"; showMapHint("load-failed"); };
    document.head.appendChild(s);
  };
  return btn;
}

/* SVG 示意地图：用行程里的真实经纬度归一化打点（无需任何 Key） */
function renderSvgMap(p) {
  const esc = (s) =>
    String(s).replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  const attrs = (p.attractions || []).filter((a) => a.location && a.location.lat && a.location.lng);
  const hotels = (p.hotels && p.hotels.length ? p.hotels : p.hotel ? [p.hotel] : []).filter(
    (h) => h.location && h.location.lat && h.location.lng
  );
  const pts = [...attrs, ...hotels, ...(p.meals || [])].filter(
    (x) => x && x.location && x.location.lat && x.location.lng
  );
  if (!pts.length) return;
  const lats = pts.map((l) => l.lat),
    lngs = pts.map((l) => l.lng);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const W = 640, H = 360, PAD = 46;
  const sx = (v) => PAD + ((v - minLng) / (maxLng - minLng || 1)) * (W - PAD * 2);
  const sy = (v) => H - PAD - ((v - minLat) / (maxLat - minLat || 1)) * (H - PAD * 2);

  // 优先画高德真实路线轨迹；取不到真实路线时才退回景点直线连接
  let realRoute = "";
  (p.days_plan || []).forEach((d) => {
    ((d.route && d.route.legs) || []).forEach((l) => {
      if (!l.polyline) return;
      const seq = l.polyline
        .split(";")
        .map((s) => s.split(",").map(Number))
        .filter((c) => c.length === 2 && !isNaN(c[0]) && !isNaN(c[1]));
      if (seq.length < 2) return;
      realRoute += `<polyline points="${seq
        .map(([lng, lat]) => `${sx(lng).toFixed(1)},${sy(lat).toFixed(1)}`)
        .join(" ")}" fill="none" stroke="#2b6cb0" stroke-width="2.5" opacity="0.8" stroke-linecap="round"/>`;
    });
  });
  const line =
    realRoute || attrs.length < 2
      ? ""
      : `<polyline points="${attrs
          .map((a) => `${sx(a.location.lng).toFixed(1)},${sy(a.location.lat).toFixed(1)}`)
          .join(" ")}" fill="none" stroke="#e53e3e" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.55"/>`;

  const spots = attrs
    .map((a, i) => {
      const x = sx(a.location.lng).toFixed(1), y = sy(a.location.lat).toFixed(1);
      return `<g><circle cx="${x}" cy="${y}" r="11" fill="#e53e3e"/>
        <text x="${x}" y="${(+y + 4).toFixed(1)}" font-size="11" fill="#fff" text-anchor="middle">${i + 1}</text>
        <text x="${x}" y="${(+y - 16).toFixed(1)}" font-size="11" fill="#2d3748" text-anchor="middle">${esc(a.name)}</text></g>`;
    })
    .join("");

  const meals = (p.meals || [])
    .filter((m) => m.location && m.location.lng)
    .map(
      (m) =>
        `<circle cx="${sx(m.location.lng).toFixed(1)}" cy="${sy(m.location.lat).toFixed(1)}" r="6" fill="#dd6b20"/>`
    )
    .join("");

  const hotel = hotels
    .map((h) => {
      const x = sx(h.location.lng).toFixed(1), y = sy(h.location.lat).toFixed(1);
      return `<g><circle cx="${x}" cy="${y}" r="10" fill="#3182ce" stroke="#fff" stroke-width="2"/>
        <text x="${x}" y="${(+y - 14).toFixed(1)}" font-size="11" fill="#2b6cb0" text-anchor="middle">🏨${esc(h.name)}</text></g>`;
    })
    .join("");

  $("map-container").innerHTML = `<div style="position:relative;width:100%;height:100%">
    <svg id="svg-map" viewBox="0 0 ${W} ${H}" style="width:100%;height:100%;background:#eef3f8;border-radius:8px;cursor:grab">
      ${realRoute}${line}${spots}${meals}${hotel}
    </svg>
    <div style="position:absolute;left:10px;bottom:10px;font-size:12px;color:#4a5568;background:rgba(255,255,255,.85);padding:4px 8px;border-radius:6px;line-height:1.5">
      🔴景点 🔵酒店 🟠餐厅${realRoute ? " · 蓝线=高德真实路线" : ""}${
        isTouchDevice() ? "" : "<br>可拖动平移 · 滚轮缩放"
      }
    </div>
    ${
      isTouchDevice()
        ? ""
        : `<button type="button" id="svg-reset" style="position:absolute;right:10px;top:10px;font-size:12px;padding:4px 8px;border:1px solid #cbd5e0;background:#fff;border-radius:6px;cursor:pointer">重置视图</button>`
    }
    </div>`;
  attachSvgMapControls();
}

/* 让 SVG 示意图支持拖动平移 + 滚轮缩放（公网无高德 Key 时的降级地图也需要能查看） */
function attachSvgMapControls() {
  // 触摸设备不绑定拖动：否则在地图区域上下滑动会被当成拖地图，页面就没法滚动了
  if (isTouchDevice()) return;
  const svg = document.getElementById("svg-map");
  if (!svg) return;
  const vb = svg.viewBox.baseVal;
  const orig = { x: vb.x, y: vb.y, w: vb.width, h: vb.height };
  // 记录每个文字的初始字号：缩放时按比例反向调整，避免放大后字跟着变大、标签互相重叠
  const texts = Array.from(svg.querySelectorAll("text"));
  texts.forEach((t) => {
    if (!t.dataset.base) t.dataset.base = t.getAttribute("font-size") || "11";
  });
  const apply = (x, y, w, h) => {
    svg.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
    const zoom = w / orig.w; // <1 表示视图被放大
    texts.forEach((t) => {
      t.setAttribute("font-size", (parseFloat(t.dataset.base) * zoom).toFixed(2));
    });
  };

  let dragging = false, startX = 0, startY = 0, startVb = null;
  svg.addEventListener("mousedown", (e) => {
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    startVb = { x: vb.x, y: vb.y };
    svg.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    svg.style.cursor = "grab";
  });
  svg.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    const dx = (e.clientX - startX) * (vb.width / rect.width);
    const dy = (e.clientY - startY) * (vb.height / rect.height);
    apply(startVb.x - dx, startVb.y - dy, vb.width, vb.height);
  });
  svg.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const mx = (e.clientX - rect.left) / rect.width;
      const my = (e.clientY - rect.top) / rect.height;
      const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
      const nw = Math.min(orig.w * 4, Math.max(orig.w * 0.15, vb.width * factor));
      const nh = nw * (orig.h / orig.w);
      apply(vb.x + (vb.width - nw) * mx, vb.y + (vb.height - nh) * my, nw, nh);
    },
    { passive: false }
  );
  const reset = document.getElementById("svg-reset");
  if (reset) reset.addEventListener("click", () => apply(orig.x, orig.y, orig.w, orig.h));
}



/* 高德 JS API 真实地图（有 key 时） */
function renderAmap(p) {
  const hotels = p.hotels && p.hotels.length ? p.hotels : p.hotel ? [p.hotel] : [];
  const pts = [...p.attractions, ...hotels, ...p.meals]
    .filter(Boolean)
    .map((x) => x.location);
  if (!pts.length) return;
  const lats = pts.map((l) => l.lat),
    lngs = pts.map((l) => l.lng);
  const center = [
    (Math.min(...lats) + Math.max(...lats)) / 2,
    (Math.min(...lngs) + Math.max(...lngs)) / 2,
  ];
  if (window.__map) {
    try { window.__map.destroy(); } catch (e) {}
    window.__map = null;
  }

  // 超时降级必须在 new AMap.Map 之前注册：构造本身可能抛异常（鉴权失败时），
  // 若把 setTimeout 放在后面，异常会让降级逻辑根本没机会注册 —— 这正是白板的成因。
  const timer = setTimeout(() => {
    if (window.__map && !window.__amapCompleted) {
      try { window.__map.destroy(); } catch (e) {}
      window.__map = null;
      window.__amapBroken = true;
      showMapHint("timeout");
      if (state.plan) renderSvgMap(state.plan);
    }
  }, 3500);
  window.__amapCompleted = false;

  // 渐进增强：只有 complete 事件确认底图真的渲染出来，才把容器交给高德。
  // 在此之前 SVG 一直留在页面上，用户看不到空白。
  const map = new AMap.Map("map-container", { zoom: 12, center });
  window.__map = map;
  try {
    map.on("complete", () => {
      window.__amapCompleted = true;
      clearTimeout(timer);
      showMapHint("ok");
    });
  } catch (e) {
    clearTimeout(timer);
    window.__amapBroken = true;
    window.__amapLastError = e && e.message ? e.message : String(e);
    showMapHint("amap-exception");
    if (state.plan) renderSvgMap(state.plan);
    return;
  }

  p.attractions.forEach((a, i) => {
    const mk = new AMap.Marker({
      position: [a.location.lng, a.location.lat],
      content: `<div style="background:#e53e3e;color:#fff;width:20px;height:20px;border-radius:50%;text-align:center;line-height:20px;font-size:12px;box-shadow:0 1px 3px rgba(0,0,0,.3)">${i + 1}</div>`,
      anchor: "center",
      title: a.name,
    });
    mk.on("click", () =>
      showInfo(map, [a.location.lng, a.location.lat], `<b>${i + 1}. ${a.name}</b><br>${a.description || ""}`)
    );
    map.add(mk);
  });

  hotels.forEach((h, idx) => {
    if (!h.location) return;
    const mk = new AMap.Marker({
      position: [h.location.lng, h.location.lat],
      content: `<div style="background:#2b6cb0;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.3)">🏨${idx + 1}. ${h.name}</div>`,
      anchor: "center",
      title: h.name,
    });
    const star = h.star == null ? "星级待核实" : "★".repeat(Math.max(0, Math.min(5, h.star)));
    const price = Number(h.price_per_night) > 0 ? `¥${h.price_per_night}/晚` : "价格待询";
    mk.on("click", () =>
      showInfo(
        map,
        [h.location.lng, h.location.lat],
        `<b>🏨 ${h.name}</b><br><span style="color:#d69e2e">${star}</span><br>${price}` +
          (h.rating ? `<br>评分 ${h.rating}` : "") +
          (h.distance_to_sights_km != null ? `<br>距景点群均 ${h.distance_to_sights_km} km` : "")
      )
    );
    map.add(mk);
  });

  // 优先画高德路径规划返回的真实轨迹；取不到时才退回景点直线连接
  let drewReal = false;
  (p.days_plan || []).forEach((d) => {
    ((d.route && d.route.legs) || []).forEach((l) => {
      if (!l.polyline) return;
      const path = l.polyline
        .split(";")
        .map((s) => s.split(",").map(Number))
        .filter((c) => c.length === 2 && !isNaN(c[0]) && !isNaN(c[1]));
      if (path.length < 2) return;
      map.add(
        new AMap.Polyline({ path, strokeColor: "#2b6cb0", strokeWeight: 5, strokeOpacity: 0.85 })
      );
      drewReal = true;
    });
  });
  if (!drewReal) {
    const order = [];
    p.days_plan.forEach((d) => d.attraction_ids.forEach((id) => order.push(id)));
    const routeLl = [...new Set(order)]
      .map((id) => attractionById(id))
      .filter(Boolean)
      .map((a) => [a.location.lng, a.location.lat]);
    if (routeLl.length > 1) {
      map.add(
        new AMap.Polyline({
          path: routeLl,
          strokeColor: "#2b6cb0",
          strokeWeight: 4,
          strokeStyle: "dashed",
          strokeOpacity: 0.8,
        })
      );
    }
  }
  map.setFitView();
}

function showInfo(map, pos, html) {
  const info = new AMap.InfoWindow({
    content: `<div style="padding:6px;max-width:220px;font-size:12px">${html}</div>`,
    offset: new AMap.Pixel(0, -20),
  });
  info.open(map, pos);
}

/* ---------- 查询历史（情景记忆） ---------- */
const HISTORY_API = `${API_BASE}/api/history`;

function escText(s) {
  return String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function loadHistory() {
  const box = $("history-list");
  if (!box) return;
  const sid = getSessionId();
  try {
    const res = await fetch(`${HISTORY_API}?session_id=${encodeURIComponent(sid)}&limit=12`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const items = (await res.json()).items || [];
    if (!items.length) {
      box.innerHTML = "<p class='hint'>暂无记录，生成一次行程后这里会出现。</p>";
      return;
    }
    box.innerHTML = items.map((it) => {
      const pref = (it.preferences || []).slice(0, 2).join("、");
      const meta = [
        it.created_at ? it.created_at.replace("T", " ") : "",
        `${it.days}天`,
        pref,
        it.ok ? `${it.n_attractions}景点 · ¥${it.budget_total}` : "",
        `${it.elapsed_s}s`,
        it.arch,
      ].filter(Boolean).join(" · ");
      return `<div class="history-item ${it.ok ? "" : "failed"}" data-id="${it.id}">
        <button class="hi-del" data-del="${it.id}" title="删除这条">×</button>
        <div class="hi-title">${escText(it.destination)}</div>
        <div class="hi-meta">${escText(meta)}</div>
        ${it.ok ? "" : `<div class="hi-err">${escText((it.error || "生成失败").slice(0, 70))}</div>`}
      </div>`;
    }).join("");
  } catch (e) {
    box.innerHTML = `<p class='hint'>历史加载失败：${escText(e.message)}（不影响规划功能）</p>`;
  }
}

async function replayHistory(id) {
  const sid = getSessionId();
  try {
    const res = await fetch(`${HISTORY_API}/${id}?session_id=${encodeURIComponent(sid)}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const item = await res.json();
    if (!item.plan_json) throw new Error("该记录没有可回放的行程");
    state.plan = JSON.parse(item.plan_json);
    state.demo = false;
    hideDemoBadge();
    renderAll();
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    const st = $("status");
    if (st) st.textContent = `已回放 ${item.destination} 的历史行程`;
  } catch (e) {
    const st = $("status");
    if (st) st.textContent = "回放失败：" + e.message;
  }
}

async function deleteHistory(id) {
  const sid = getSessionId();
  try {
    await fetch(`${HISTORY_API}/${id}?session_id=${encodeURIComponent(sid)}`, { method: "DELETE" });
  } catch (e) { /* 删除失败不阻塞界面刷新 */ }
  loadHistory();
}

(function bindHistory() {
  const box = $("history-list");
  if (box) {
    box.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del]");
      if (del) {
        e.stopPropagation();
        deleteHistory(del.dataset.del);
        return;
      }
      const card = e.target.closest(".history-item");
      if (card && !card.classList.contains("failed")) replayHistory(card.dataset.id);
    });
  }
  const refresh = $("btn-history-refresh");
  if (refresh) refresh.addEventListener("click", loadHistory);
  const clear = $("btn-history-clear");
  if (clear) clear.addEventListener("click", async () => {
    if (!window.confirm("确定清空当前浏览器的全部查询历史？此操作不可撤销。")) return;
    const sid = getSessionId();
    try {
      const res = await fetch(`${HISTORY_API}?session_id=${encodeURIComponent(sid)}&limit=100`);
      const items = (await res.json()).items || [];
      await Promise.all(items.map((it) => deleteHistory(it.id)));
    } catch (e) { /* 忽略 */ }
    loadHistory();
  });
  loadHistory();
})();

/* 页面初始化时消费 index.html 记下的地图状态。
   内联脚本先于本文件执行，所以 no-key / load-failed 这类早期判断要在这里呈现。 */
(function initMapHint() {
  window.showMapHint = showMapHint; // 供内联脚本后续 onerror 回调使用
  var reason = window.__mapHintReason;
  if (reason && reason !== "ok") showMapHint(reason);
})();

/* ---------- 导出（需求 5） ---------- */
$("btn-pdf").addEventListener("click", async () => {
  const canvas = await html2canvas($("result"));
  const img = canvas.toDataURL("image/png");
  const pdf = new jspdf.jsPDF({ orientation: "portrait", unit: "px", format: [canvas.width, canvas.height] });
  pdf.addImage(img, "PNG", 0, 0, canvas.width, canvas.height);
  pdf.save("行程计划.pdf");
});
$("btn-png").addEventListener("click", async () => {
  const canvas = await html2canvas($("result"));
  const a = document.createElement("a");
  a.href = canvas.toDataURL("image/png");
  a.download = "行程计划.png";
  a.click();
});

/* ---------- 省-市联动目的地选择 ----------
   数据源 cities.json 由高德行政区接口 /v3/config/district 一次性生成（静态、零运行时请求），
   避免用户手输境外名/错别字触发无效请求。隐藏的 #destination 仍承载选中城市名，
   这样规划/历史/资料库匹配的调用方一行都不用改。
   加载失败时降级为可输入的文本框，不让整个表单失效。 */
(function initCityPicker() {
  const provSel = $("province");
  const citySel = $("city");
  const dest = $("destination");
  if (!provSel || !citySel || !dest) return;

  function fallbackToInput() {
    const input = document.createElement("input");
    input.id = "destination_fallback";
    input.value = dest.value || "";
    input.placeholder = "城市列表加载失败，请输入国内城市名";
    input.required = true;
    input.addEventListener("input", () => { dest.value = input.value.trim(); });
    const wrap = provSel.parentElement;
    wrap.innerHTML = "";
    wrap.appendChild(input);
    dest.value = input.value;
  }

  function onProvinceChange() {
    const name = provSel.value;
    const prov = (window.__citiesData.provinces || []).find((p) => p.name === name);
    citySel.innerHTML = "";
    if (!prov) {
      citySel.disabled = true;
      citySel.innerHTML = '<option value="">— 先选省份 —</option>';
      dest.value = "";
      return;
    }
    citySel.disabled = false;
    citySel.innerHTML = '<option value="">— 选择城市 —</option>';
    prov.cities.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      citySel.appendChild(opt);
    });
    dest.value = "";
  }

  function onCityChange() {
    dest.value = citySel.value || "";
  }

  fetch("cities.json")
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((data) => {
      if (!data || !Array.isArray(data.provinces) || !data.provinces.length) throw new Error("空数据");
      window.__citiesData = data;
      provSel.innerHTML = '<option value="">— 选择省份 —</option>';
      data.provinces.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.name;
        provSel.appendChild(opt);
      });
      provSel.addEventListener("change", onProvinceChange);
      citySel.addEventListener("change", onCityChange);
      // 默认选中北京，与原来输入框默认值一致
      const bj = data.provinces.find((p) => p.name === "北京");
      if (bj) {
        provSel.value = "北京";
        onProvinceChange();
        if (bj.cities.includes("北京")) {
          citySel.value = "北京";
          dest.value = "北京";
        }
      }
    })
    .catch((e) => {
      console.warn("城市列表加载失败，降级为输入框：", e);
      fallbackToInput();
    });
})();

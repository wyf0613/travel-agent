/* 单页前端：规划 / 地图 / 预算 / 编辑 / 导出
 * 数据来源：优先调用本地后端 POST /api/trip/plan；不可达时自动降级到前端 mock.js，
 * 使 GitHub Pages 等纯静态托管环境下也能完整演示（零 Key、零后端）。
 * 地图：有高德 Web Key 时用 AMap JS API 真实渲染，否则降级为 SVG 示意图。
 */
const API = "http://127.0.0.1:8921/api/trip/plan";
// 后端请求超时（毫秒）：静态站点上请求本机端口会很快失败，短暂等待后降级
const BACKEND_TIMEOUT_MS = 2500;
const state = { plan: null };

async function requestPlan(payload) {
  // 1) 先试本地后端（带超时，避免静态站点上长时间挂起）
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), BACKEND_TIMEOUT_MS);
  try {
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (res.ok) return { plan: await res.json(), source: "backend" };
  } catch (e) {
    // 后端不可达/超时/被拒绝 → 走前端降级
  } finally {
    clearTimeout(timer);
  }
  // 2) 降级：前端内置 mock（与后端逻辑一致）
  if (typeof window.generateTripMock !== "function") {
    throw new Error("后端不可达，且前端 mock.js 未加载");
  }
  return { plan: window.generateTripMock(payload), source: "frontend-mock" };
}

const $ = (id) => document.getElementById(id);

document.getElementById("plan-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("status");
  status.textContent = "生成中…";
  const payload = {
    destination: $("destination").value.trim(),
    days: parseInt($("days").value, 10) || 3,
    preferences: $("preferences").value.split(",").map((s) => s.trim()).filter(Boolean),
    budget_limit: $("budget_limit").value ? Number($("budget_limit").value) : null,
    start_date: $("start-date").value || null,
  };
  try {
    const result = await requestPlan(payload);
    state.plan = result.plan;
    renderAll();
    status.textContent = result.source === "backend"
      ? "完成 ✅（本地后端）"
      : "完成 ✅（前端演示模式 · 未连接后端，数据为内置示例）";
  } catch (err) {
    status.textContent = "生成失败：" + err.message;
  }
});

/* ---------- 渲染 ---------- */
function renderAll() {
  const p = state.plan;
  window.__planReady = true;
  $("result").hidden = false;
  $("empty").hidden = true;
  $("overview").textContent = p.overview;
  renderBudget();
  renderDays();
  renderMap();
}

function renderBudget() {
  const b = state.plan.budget;
  $("budget-container").innerHTML = `
    <div class="row"><span>门票</span><span>¥${b.attractions}</span></div>
    <div class="row"><span>酒店(${state.plan.days - 1 || 1}晚)</span><span>¥${b.hotel}</span></div>
    <div class="row"><span>餐饮</span><span>¥${b.meals}</span></div>
    <div class="row"><span>交通</span><span>¥${b.transport}</span></div>
    <div class="row total"><span>合计</span><span>¥${b.total}</span></div>`;
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
          <span class="meta">¥${m.price_per_person}/人</span>`;
        return row.outerHTML;
      })
      .join("");
    const w = day.weather;
    const wText = w ? `🌤️ ${w.desc}` : "";
    card.innerHTML = `<h4>第 ${day.day} 天${day.date ? " · " + day.date : ""}${wText ? " · " + wText : ""}</h4>${attrItems}${mealItems}`;
    wrap.appendChild(card);
  });
}

function itemRow(a, type, day, i, len) {
  const row = document.createElement("div");
  row.className = "item";
  row.innerHTML = `
    <div class="move">
      <button data-act="up" data-id="${a.id}" data-day="${day}" ${i === 0 ? "disabled" : ""}>▲</button>
      <button data-act="down" data-id="${a.id}" data-day="${day}" ${i === len - 1 ? "disabled" : ""}>▼</button>
    </div>
    <span class="name">📍 ${a.name}</span>
    <span class="meta">${a.category} · ¥${a.ticket_price} · ${a.visit_duration_hours}h</span>
    <button class="del" data-act="del" data-id="${a.id}">删除</button>`;
  return row.outerHTML;
}

/* ---------- 编辑（需求 4） ---------- */
$("days-container").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { act, id, day } = btn.dataset;
  const p = state.plan;
  const dayPlan = p.days_plan.find((d) => String(d.day) === day);
  if (act === "del") {
    p.attractions = p.attractions.filter((a) => a.id !== id);
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
  const hotel = p.hotel ? p.hotel.price_per_night * Math.max(p.days - 1, 1) : 0;
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
function renderMap() {
  const p = state.plan;
  if (window.__amapReady && window.AMap) {
    renderAmap(p);
  } else {
    renderSvgMap(p);
  }
}

/* SVG 零 Key 版（无高德 key 时降级） */
function renderSvgMap(p) {
  const pts = [...p.attractions, p.hotel, ...p.meals]
    .filter(Boolean)
    .map((x) => x.location);
  if (!pts.length) return;
  const lats = pts.map((l) => l.lat),
    lngs = pts.map((l) => l.lng);
  const bounds = {
    minLat: Math.min(...lats),
    maxLat: Math.max(...lats),
    minLng: Math.min(...lngs),
    maxLng: Math.max(...lngs),
  };
  const W = 600,
    H = 360,
    pad = 40;
  const proj = (lat, lng) => [
    pad + ((lng - bounds.minLng) / (bounds.maxLng - bounds.minLng || 1)) * (W - 2 * pad),
    pad + ((bounds.maxLat - lat) / (bounds.maxLat - bounds.minLat || 1)) * (H - 2 * pad),
  ];

  const order = [];
  p.days_plan.forEach((d) => d.attraction_ids.forEach((id) => order.push(id)));
  const routePts = [...new Set(order)]
    .map((id) => attractionById(id))
    .filter(Boolean)
    .map((a) => proj(a.location.lat, a.location.lng));
  const route = routePts.map((p) => p.join(",")).join(" ");

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%">`;
  svg += `<polyline points="${route}" fill="none" stroke="#2b6cb0" stroke-width="2" stroke-dasharray="5,4" />`;
  p.attractions.forEach((a, i) => {
    const [x, y] = proj(a.location.lat, a.location.lng);
    svg += `<circle cx="${x}" cy="${y}" r="6" fill="#e53e3e" />`;
    svg += `<text x="${x + 9}" y="${y + 4}" font-size="11" fill="#333">${i + 1}. ${a.name}</text>`;
  });
  if (p.hotel) {
    const [hx, hy] = proj(p.hotel.location.lat, p.hotel.location.lng);
    svg += `<rect x="${hx - 6}" y="${hy - 6}" width="12" height="12" fill="#38a169" />`;
    svg += `<text x="${hx + 9}" y="${hy + 4}" font-size="11" fill="#333">🏨 ${p.hotel.name}</text>`;
  }
  svg += `</svg>`;
  $("map-container").innerHTML = svg;
}

/* 高德 JS API 真实地图（有 key 时） */
function renderAmap(p) {
  const pts = [...p.attractions, p.hotel, ...p.meals]
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
  const map = new AMap.Map("map-container", { zoom: 12, center });
  window.__map = map;

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

  if (p.hotel) {
    const h = p.hotel;
    const mk = new AMap.Marker({
      position: [h.location.lng, h.location.lat],
      content: `<div style="background:#38a169;color:#fff;width:20px;height:20px;border-radius:3px;text-align:center;line-height:20px;font-size:12px;">🏨</div>`,
      anchor: "center",
      title: h.name,
    });
    mk.on("click", () =>
      showInfo(map, [h.location.lng, h.location.lat], `<b>🏨 ${h.name}</b><br>¥${h.price_per_night}/晚`)
    );
    map.add(mk);
  }

  const order = [];
  p.days_plan.forEach((d) => d.attraction_ids.forEach((id) => order.push(id)));
  const routeLl = [...new Set(order)]
    .map((id) => attractionById(id))
    .filter(Boolean)
    .map((a) => [a.location.lng, a.location.lat]);
  if (routeLl.length > 1) {
    const line = new AMap.Polyline({
      path: routeLl,
      strokeColor: "#2b6cb0",
      strokeWeight: 4,
      strokeStyle: "dashed",
      strokeOpacity: 0.8,
    });
    map.add(line);
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

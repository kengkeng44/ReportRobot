// 待辦小工具（iPhone 桌面，Scriptable）
// 顯示：還沒做完的緊急事（逾期、今天到期、P0/P1）＋ 各專案進度。點一下打開待辦儀表板。
//
// 設定方式見 docs/widget/README.md。只要改下面兩行：
const TOKEN = "貼上 Railway 的 WIDGET_TOKEN";
const WHO_DEFAULT = "仁和"; // 看誰的：仁和／小螢／共同。小工具的「Parameter」欄填了會蓋過這個

const API = "https://chengreportbot-production.up.railway.app/widget/todos";
const DASHBOARD = "https://claude.ai/artifact/7rKimpFmqYP8zVztyG2fwW";

// ── 顏色（淺色／深色自動切換）──────────────────────
const C = {
  bg: Color.dynamic(new Color("#f2f2f7"), new Color("#1c1c1e")),
  ink: Color.dynamic(new Color("#1d1d1f"), new Color("#f5f5f7")),
  muted: Color.dynamic(new Color("#6e6e73"), new Color("#98989d")),
  red: Color.dynamic(new Color("#d70015"), new Color("#ff6961")),
  orange: Color.dynamic(new Color("#c96a00"), new Color("#ffb340")),
  green: Color.dynamic(new Color("#248a3d"), new Color("#30d158")),
  track: Color.dynamic(new Color("#e3e3e8"), new Color("#3a3a3c")),
};

const who = (args.widgetParameter || WHO_DEFAULT).trim();
const size = config.widgetFamily || "large";

async function load() {
  const fm = FileManager.local();
  const cache = fm.joinPath(fm.documentsDirectory(), `todo-widget-${who}.json`);
  try {
    const req = new Request(`${API}?who=${encodeURIComponent(who)}`);
    req.headers = { "X-Widget-Token": TOKEN };
    req.timeoutInterval = 15;
    const data = await req.loadJSON();
    if (req.response.statusCode !== 200) throw new Error(`HTTP ${req.response.statusCode}`);
    fm.writeString(cache, JSON.stringify(data));
    return { data, stale: false };
  } catch (e) {
    // 沒網路或喵管家在重開：先顯示上一次的，標「舊資料」
    if (fm.fileExists(cache)) return { data: JSON.parse(fm.readString(cache)), stale: true };
    return { error: String(e) };
  }
}

function text(stack, s, font, color, lines = 1) {
  const t = stack.addText(s);
  t.font = font;
  t.textColor = color;
  t.lineLimit = lines;
  return t;
}

function bar(progress, width) {
  const h = 6;
  const ctx = new DrawContext();
  ctx.size = new Size(width, h);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const track = new Path();
  track.addRoundedRect(new Rect(0, 0, width, h), 3, 3);
  ctx.addPath(track);
  ctx.setFillColor(C.track);
  ctx.fillPath();
  const w = Math.max(progress > 0 ? h : 0, Math.round(width * progress));
  if (w > 0) {
    const fill = new Path();
    fill.addRoundedRect(new Rect(0, 0, w, h), 3, 3);
    ctx.addPath(fill);
    ctx.setFillColor(C.green);
    ctx.fillPath();
  }
  return ctx.getImage();
}

function urgentColor(why) {
  return why.startsWith("逾期") || why === "今天" || why === "P0" ? C.red : C.orange;
}

function addUrgent(col, data, max) {
  const head = col.addStack();
  head.centerAlignContent();
  text(head, "🔥 緊急", Font.boldSystemFont(13), C.ink);
  head.addSpacer(4);
  text(head, String(data.urgent_count), Font.boldSystemFont(13), data.urgent_count ? C.red : C.muted);
  col.addSpacer(4);
  if (!data.urgent.length) {
    text(col, "沒有緊急的事 🎉", Font.systemFont(12), C.muted);
    return;
  }
  for (const u of data.urgent.slice(0, max)) {
    const row = col.addStack();
    row.centerAlignContent();
    text(row, u.text, Font.systemFont(12), C.ink);
    row.addSpacer(4);
    text(row, u.why, Font.mediumSystemFont(11), urgentColor(u.why));
    col.addSpacer(3);
  }
  const more = data.urgent_count - Math.min(max, data.urgent.length);
  if (more > 0) text(col, `還有 ${more} 件…`, Font.systemFont(11), C.muted);
}

function addProjects(col, data, max, barWidth) {
  text(col, "📁 專案進度", Font.boldSystemFont(13), C.ink);
  col.addSpacer(4);
  if (!data.projects.length) {
    text(col, "還沒有專案", Font.systemFont(12), C.muted);
    return;
  }
  for (const p of data.projects.slice(0, max)) {
    const row = col.addStack();
    row.centerAlignContent();
    text(row, p.name, Font.systemFont(12), C.ink);
    row.addSpacer();
    const pct = `${Math.round(p.progress * 100)}%`;
    text(row, p.urgent ? `${p.urgent}🔥 ${pct}` : pct, Font.mediumSystemFont(11), p.urgent ? C.red : C.muted);
    col.addSpacer(2);
    col.addImage(bar(p.progress, barWidth)).imageSize = new Size(barWidth, 6);
    col.addSpacer(5);
  }
}

async function build() {
  const w = new ListWidget();
  w.backgroundColor = C.bg;
  w.url = DASHBOARD;
  w.setPadding(14, 14, 12, 14);
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);

  const { data, stale, error } = await load();
  if (error) {
    text(w, "待辦小工具連不上", Font.boldSystemFont(13), C.ink);
    w.addSpacer(4);
    text(w, "檢查 TOKEN 有沒有貼對、喵管家有沒有在跑", Font.systemFont(11), C.muted, 3);
    return w;
  }

  if (size === "small") {
    text(w, `${who}・緊急`, Font.mediumSystemFont(12), C.muted);
    text(w, String(data.urgent_count), Font.boldSystemFont(36), data.urgent_count ? C.red : C.green);
    for (const u of data.urgent.slice(0, 2)) text(w, `• ${u.text}`, Font.systemFont(11), C.ink);
    w.addSpacer();
    text(w, stale ? `舊資料 ${data.updated}` : `${data.open} 件待辦`, Font.systemFont(10), C.muted);
    return w;
  }

  const title = w.addStack();
  title.centerAlignContent();
  text(title, `${who}的待辦`, Font.boldSystemFont(15), C.ink);
  title.addSpacer();
  text(title, `${stale ? "舊資料 " : ""}${data.updated} · ${data.open} 件`, Font.systemFont(10), C.muted);
  w.addSpacer(8);

  if (size === "medium") {
    const body = w.addStack();
    const left = body.addStack();
    left.layoutVertically();
    left.size = new Size(160, 0);
    addUrgent(left, data, 4);
    body.addSpacer(10);
    const right = body.addStack();
    right.layoutVertically();
    addProjects(right, data, 3, 110);
  } else {
    addUrgent(w, data, 7);
    w.addSpacer(10);
    addProjects(w, data, 5, 290);
  }
  w.addSpacer();
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget[`present${size === "small" ? "Small" : size === "medium" ? "Medium" : "Large"}`]();
Script.complete();

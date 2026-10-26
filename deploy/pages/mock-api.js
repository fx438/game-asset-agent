/* 静态演示站的"浏览器内后端"：GitHub Pages 上没有服务器，这里用 JS 模拟 /api 接口和工作流状态机。
 *
 * 接口路径、返回结构、状态流转、校验规则都和 FastAPI 后端保持一致，前端代码不区分两种模式。
 * 只支持示例需求（亚瑟 / 梅林 / 罗宾，像素风），图片用仓库里预先画好的示例帧；不调用任何模型。
 * 数据存在浏览器 localStorage 里，刷新后进行中的流程会接着跑；存不了时只保存在内存。
 */
(function () {
  const REPO_URL = 'https://github.com/fx438/game-asset-agent';
  const FRAMES = 'static/examples/frames/';
  const DB_KEY = 'gaa-static-db-v1';
  const MAX_AUTO_RETRIES = 2, MAX_PER_TASK = 8, MAX_TOTAL = 32, CONCURRENCY = 4;
  const STYLES = [
    { key: 'pixel', name: '像素风', description: '16-bit 复古像素游戏角色，色块清晰，适合横版动作游戏',
      prefix: '16-bit 像素艺术游戏角色精灵图，清晰像素边缘，有限调色板，纯色背景，全身，居中' },
    { key: 'anime', name: '二次元', description: '日系动漫赛璐璐上色，线条干净',
      prefix: '日系二次元游戏角色立绘，赛璐璐上色，干净线稿，高饱和配色，纯色背景，全身，居中' },
    { key: 'chibi', name: 'Q版', description: '大头小身 2-3 头身，可爱圆润',
      prefix: 'Q版卡通游戏角色，2.5头身，大眼睛，圆润造型，描边清晰，纯色背景，全身，居中' },
    { key: 'realistic', name: '写实', description: '写实奇幻概念设计，质感丰富',
      prefix: '写实奇幻游戏角色概念设计，电影级光影，精细材质，纯色背景，全身，居中' },
    { key: 'ink', name: '国风水墨', description: '中国水墨晕染，留白构图',
      prefix: '国风水墨游戏角色，水墨晕染笔触，淡彩，宣纸质感，留白背景，全身，居中' },
  ];
  const NEGATIVE = '低质量，模糊，畸形，多余的手指，多余的肢体，肢体残缺，文字，水印，签名，裁切不完整，写实，3D渲染，渐变模糊，抗锯齿过度';
  const SHEETS = { '亚瑟|待机': 'arthur_idle', '亚瑟|攻击': 'arthur_attack', '梅林|待机': 'merlin_idle',
    '梅林|施法': 'merlin_cast', '罗宾|行走': 'robin_walk', '罗宾|射击': 'robin_shoot' };
  // 与 app/static/examples/demo_requirement.xlsx 内容一致
  const DEMO_TASKS = [
    ['亚瑟', '战士', '待机', '银色铠甲，红色披风'], ['亚瑟', '战士', '攻击', '单手剑横劈'],
    ['梅林', '法师', '待机', '蓝色长袍，木质法杖'], ['梅林', '法师', '施法', '法杖顶端凝聚光球'],
    ['罗宾', '射手', '行走', '绿色斗篷，皮甲'], ['罗宾', '射手', '射击', '拉满弓后放箭'],
  ].map(([hero, role, action, description]) => ({ hero, role, action, count: 4, description, view: '侧面' }));
  const AUTO_ISSUES = ['内容符合度 6 分偏低', '风格一致性 6 分偏低', '手部疑似多指', '武器比例与设定卡不符'];
  const WORKING = ['CREATED', 'PARSING', 'MASTER_GENERATING', 'GENERATING', 'ARCHIVING'];
  const DECIDABLE = ['WAITING_HUMAN', 'AUTO_FAILED', 'ERROR', 'APPROVED', 'REJECTED'];
  const UNDECIDED = ['WAITING_HUMAN', 'AUTO_FAILED', 'ERROR'];

  class HttpError extends Error {}
  const fail = (msg) => { throw new HttpError(msg); };
  const now = () => new Date().toISOString();
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const seed = () => Math.floor(Math.random() * 2 ** 31);
  // 自动审查用确定性的伪随机：同一张图同一版本结果固定
  const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) / 2 ** 32; };
  const safe = (s) => s.replace(/[^\w一-龥]+/g, '_').replace(/^_+|_+$/g, '');

  let db = { seq: 0, projects: [] };
  try { db = JSON.parse(localStorage.getItem(DB_KEY)) || db; } catch {}
  const save = () => { try { localStorage.setItem(DB_KEY, JSON.stringify(db)); } catch {} };
  const nextId = () => ++db.seq;
  const find = (id) => db.projects.find((p) => p.id === id) || fail(`项目 ${id} 不存在`);
  const requireStatus = (p, s) => { if (p.status !== s) fail(`当前项目状态为 ${p.status}，此操作需要状态 ${s}`); };
  const style = (key) => STYLES.find((s) => s.key === key);

  // ---------- 输出结构（对齐 app/schemas.py） ----------
  const brief = (p) => ({ id: p.id, name: p.name, status: p.status, style_key: p.style_key, created_at: p.created_at });
  function detail(p) {
    const progress = {};
    for (const a of p.assets) progress[a.status] = (progress[a.status] || 0) + 1;
    return { ...brief(p), source_filename: p.source_filename, style_notes: p.style_notes, parse_issues: p.parse_issues,
      character_cards: p.character_cards, review_round: p.review_round, error: p.error, tasks: p.tasks,
      total_images: p.tasks.reduce((n, t) => n + t.count, 0), progress };
  }
  const assetOut = (a, withReviews = false) => ({ ...a, reviews: withReviews ? [...a.reviews].reverse() : [] });

  // ---------- 工作流（对齐 app/workflow/graph.py） ----------
  const running = new Set();
  function drive(p) {
    if (running.has(p.id) || !WORKING.includes(p.status)) return;
    running.add(p.id);
    const step = { CREATED: parse, PARSING: parse, MASTER_GENERATING: genMaster, GENERATING: genAssets, ARCHIVING: archive }[p.status];
    step(p).catch((e) => { p.status = 'FAILED'; p.error = String(e.message || e); })
      .finally(() => { running.delete(p.id); save(); drive(p); });
  }

  async function parse(p) {
    p.status = 'PARSING'; save();
    await sleep(1200);
    p.tasks = DEMO_TASKS.map((t) => ({ id: nextId(), ...t }));
    p.parse_issues = ['[解析方式: table]'];
    p.status = 'PLAN_CONFIRMING';
  }

  function masterImage(p, version) {
    const hero = p.tasks[0].hero;
    if (hero === '亚瑟') return `${FRAMES}master_v${(version - 1) % 2 + 1}.png`;
    const sheet = Object.entries(SHEETS).find(([k]) => k.startsWith(hero + '|'))[1];
    return `${FRAMES}${sheet}_1.png`;
  }

  async function genMaster(p) {
    await sleep(1600);
    const t = p.tasks[0], s = style(p.style_key), version = p.masters.length + 1;
    const fb = p.masters.length ? p.masters[p.masters.length - 1].feedback : null;
    const prompt = [s.prefix, p.style_notes, `${t.hero}（${t.role}），${p.character_cards[t.hero + '|' + t.role]}，站立展示姿态`,
      fb ? `修改要求：${fb}` : ''].filter(Boolean).join('，');
    p.masters.push({ id: nextId(), version, style_key: p.style_key, prompt, negative_prompt: NEGATIVE, seed: seed(),
      status: 'READY', feedback: null, image_url: masterImage(p, version), created_at: now() });
    p.status = 'MASTER_CONFIRMING';
  }

  function buildPrompt(p, a, fix) {
    const t = p.tasks.find((x) => x.id === a.task_id);
    return [style(p.style_key).prefix, p.style_notes, `角色：${t.hero}（${t.role}），${p.character_cards[t.hero + '|' + t.role]}`,
      `动作：${a.frame_desc}`, t.view ? `视角：${t.view}` : '', fix ? `修正：${fix}` : ''].filter(Boolean).join('，');
  }

  // 单张图的"生成 → 自动审查 → 修正重生成"闭环
  async function genOne(p, a) {
    for (;;) {
      a.status = 'GENERATING'; save();
      await sleep(350 + Math.random() * 400);
      a.image_url = `${FRAMES}${SHEETS[a.hero + '|' + a.action]}_${(a.seq - 1) % 4 + 1}.png`;
      a.status = 'AUTO_REVIEWING'; save();
      await sleep(250 + Math.random() * 300);
      const r = hash(`${p.id}|${a.id}|${a.version}`);
      const passed = r >= 0.28;
      const issues = passed ? [] : [AUTO_ISSUES[Math.floor(hash(`${a.id}|${a.version}|i`) * AUTO_ISSUES.length)]];
      a.reviews.push({ kind: 'auto', asset_version: a.version, passed, content_score: passed ? 7 + Math.floor(r * 4) : 6,
        style_score: passed ? 8 + Math.floor(hash(`${a.id}|s`) * 3) : 7, issues, comment: null, reviewer: null, created_at: now() });
      a.last_issues = issues;
      if (passed) { a.status = 'WAITING_HUMAN'; return; }
      if (a.auto_retries >= MAX_AUTO_RETRIES) { a.status = 'AUTO_FAILED'; return; }
      a.auto_retries += 1; a.version += 1;
      a.prompt = buildPrompt(p, a, [a.human_fix, issues[0]].filter(Boolean).join('；'));
    }
  }

  async function genAssets(p) {
    const queue = p.assets.filter((a) => ['PENDING', 'GENERATING', 'AUTO_REVIEWING'].includes(a.status));
    await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
      for (let a; (a = queue.shift());) await genOne(p, a);
    }));
    p.status = 'HUMAN_REVIEWING';
  }

  async function archive(p) {
    await sleep(1000);
    const root = `library/${p.id}_${safe(p.name)}`;
    for (const a of p.assets) {
      if (a.status !== 'APPROVED') continue;
      a.status = 'ARCHIVED';
      a.archive_key = `${root}/${a.hero}/${a.role}/${a.action}/${a.hero}_${a.role}_${a.action}_${String(a.seq).padStart(2, '0')}.png`;
    }
    p.status = 'ARCHIVED';
  }

  // ---------- 接口（对齐 app/api/*.py） ----------
  function createProject(body) {
    const name = String(body.get('name') || '').trim(), file = body.get('file');
    if (!name) fail('请填写项目名称');
    if (!file || file.name !== 'demo_requirement.xlsx')
      fail('静态演示站只能运行示例需求。上传自己的文档请在本地用 Docker 运行完整版');
    const p = { id: nextId(), name, status: 'CREATED', style_key: null, style_notes: null, created_at: now(),
      source_filename: file.name, parse_issues: [], character_cards: {}, review_round: 0, error: null,
      tasks: [], masters: [], assets: [] };
    db.projects.push(p);
    drive(p);
    return brief(p);
  }

  function confirmPlan(p, body) {
    requireStatus(p, 'PLAN_CONFIRMING');
    if (!style(body.style_key)) fail(`未知风格 ${body.style_key}`);
    if (body.style_key !== 'pixel') fail('静态演示站只有像素风的示例图，其他画风请在本地运行完整版');
    const tasks = body.tasks ?? p.tasks;
    if (!tasks.length) fail('任务清单不能为空');
    for (const t of tasks) {
      if (!SHEETS[t.hero + '|' + t.action]) fail(`静态演示站只有示例需求里的图片：${t.hero}·${t.action} 没有示例图`);
      if (!(t.count >= 1)) fail('张数必须是正整数');
      if (t.count > MAX_PER_TASK) fail(`单个任务最多 ${MAX_PER_TASK} 张`);
    }
    if (tasks.reduce((n, t) => n + t.count, 0) > MAX_TOTAL) fail(`在线演示每个项目最多 ${MAX_TOTAL} 张`);
    p.tasks = tasks.map((t) => ({ id: nextId(), hero: t.hero, role: t.role || '通用', action: t.action, count: +t.count,
      description: t.description || '', view: t.view || '' }));
    p.style_key = body.style_key; p.style_notes = body.style_notes || null;
    p.character_cards = {};
    for (const t of p.tasks) {
      const k = t.hero + '|' + t.role;
      p.character_cards[k] = [p.character_cards[k], t.description].filter(Boolean).join('，') || `${t.hero}，${t.role}装扮，标志性主色服装与武器`;
    }
    p.status = 'MASTER_GENERATING';
    drive(p);
    return { message: '已确认，正在生成风格母版' };
  }

  function confirmMaster(p, body) {
    requireStatus(p, 'MASTER_CONFIRMING');
    const m = p.masters[p.masters.length - 1];
    if (!body.approved) {
      if (!(body.feedback || '').trim()) fail('驳回母版时请填写修改意见，以便重新生成');
      m.status = 'REJECTED'; m.feedback = body.feedback;
      p.status = 'MASTER_GENERATING';
      drive(p);
      return { message: '已按意见重新生成母版' };
    }
    m.status = 'APPROVED';
    for (const t of p.tasks) {
      for (let i = 1; i <= t.count; i++) {
        const a = { id: nextId(), task_id: t.id, hero: t.hero, role: t.role, action: t.action, seq: i,
          frame_desc: t.count === 1 ? [t.action, t.description].filter(Boolean).join('，') : `${t.action} 第${i}/${t.count}帧`,
          prompt: '', seed: m.seed, status: 'PENDING', version: 1, image_url: null, last_issues: [], human_feedback: null,
          human_fix: null, archive_key: null, error: null, auto_retries: 0, reviews: [] };
        a.prompt = buildPrompt(p, a);
        p.assets.push(a);
      }
    }
    p.status = 'GENERATING';
    drive(p);
    return { message: '风格已锁定，开始批量生图' };
  }

  function applyReview(p, a, body) {
    if (!DECIDABLE.includes(a.status)) fail(`资源 ${a.id} 当前状态 ${a.status} 不能审核`);
    if (body.approved && a.status === 'ERROR') fail(`资源 ${a.id} 生成失败没有图片，只能驳回重新生成`);
    if (!body.approved && !(body.comment || '').trim()) fail('驳回时请填写批注，批注会回流给提示词扩写 Agent');
    a.status = body.approved ? 'APPROVED' : 'REJECTED';
    a.human_feedback = body.approved ? null : body.comment;
    a.reviews.push({ kind: 'human', asset_version: a.version, passed: !!body.approved, content_score: null, style_score: null,
      issues: body.approved ? [] : [body.comment], comment: body.comment || null, reviewer: 'reviewer', created_at: now() });
  }

  function submitReview(p) {
    requireStatus(p, 'HUMAN_REVIEWING');
    const pending = p.assets.filter((a) => UNDECIDED.includes(a.status)).map((a) => a.id);
    if (pending.length) fail(`还有 ${pending.length} 张图未审核：[${pending.slice(0, 20).join(', ')}]`);
    const rejected = p.assets.filter((a) => a.status === 'REJECTED');
    if (rejected.length) {
      p.review_round += 1;
      for (const a of rejected) {
        // 人工批注累积保留，之后的自动修正也不会把它冲掉
        a.human_fix = [a.human_fix, `人工审核意见：${a.human_feedback}`].filter(Boolean).join('；');
        a.version += 1; a.auto_retries = 0; a.status = 'PENDING';
        a.prompt = buildPrompt(p, a, a.human_fix);
      }
      p.status = 'GENERATING';
    } else p.status = 'ARCHIVING';
    drive(p);
    return { message: rejected.length ? '驳回的图片将带批注重新生成' : '全部通过，正在归档到资源库' };
  }

  function findAsset(id) {
    for (const p of db.projects) { const a = p.assets.find((x) => x.id === id); if (a) return [p, a]; }
    fail(`资源 ${id} 不存在`);
  }

  const routes = [
    ['GET', /^\/system$/, () => ({ mock_ai: true, demo_mode: true, static_demo: true, repo_url: REPO_URL, task_mode: 'browser',
      storage: 'localStorage', llm_model: 'qwen-plus', vl_model: 'qwen-vl-max', image_model: 'wan2.2-t2i-flash',
      images_per_task_limit: MAX_PER_TASK, total_images_limit: MAX_TOTAL, upload_limit_mb: 2 })],
    ['GET', /^\/styles$/, () => STYLES],
    ['GET', /^\/projects$/, () => [...db.projects].reverse().map(brief)],
    ['POST', /^\/projects$/, (m, body) => createProject(body)],
    ['GET', /^\/projects\/(\d+)$/, (m) => detail(find(+m[1]))],
    ['POST', /^\/projects\/(\d+)\/plan$/, (m, body) => confirmPlan(find(+m[1]), body)],
    ['GET', /^\/projects\/(\d+)\/masters$/, (m) => [...find(+m[1]).masters].reverse()],
    ['POST', /^\/projects\/(\d+)\/master$/, (m, body) => confirmMaster(find(+m[1]), body)],
    ['GET', /^\/projects\/(\d+)\/assets$/, (m) => find(+m[1]).assets.map((a) => assetOut(a))],
    ['GET', /^\/projects\/(\d+)\/library$/, (m) => find(+m[1]).assets.filter((a) => a.status === 'ARCHIVED')
      .sort((x, y) => (x.archive_key < y.archive_key ? -1 : 1)).map((a) => assetOut(a))],
    ['GET', /^\/assets\/(\d+)$/, (m) => assetOut(findAsset(+m[1])[1], true)],
    ['POST', /^\/assets\/(\d+)\/review$/, (m, body) => {
      const [p, a] = findAsset(+m[1]);
      requireStatus(p, 'HUMAN_REVIEWING');
      applyReview(p, a, body);
      return assetOut(a);
    }],
    ['POST', /^\/projects\/(\d+)\/review\/batch$/, (m, body) => {
      const p = find(+m[1]);
      requireStatus(p, 'HUMAN_REVIEWING');
      const assets = p.assets.filter((a) => body.asset_ids.includes(a.id));
      if (assets.length !== new Set(body.asset_ids).size) fail('部分资源不属于该项目');
      assets.forEach((a) => applyReview(p, a, body));
      return { updated: assets.length };
    }],
    ['POST', /^\/projects\/(\d+)\/review\/submit$/, (m) => submitReview(find(+m[1]))],
  ];

  window.mockApi = async function (path, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const url = path.split('?')[0];
    const body = opts.body instanceof FormData ? opts.body : opts.body ? JSON.parse(opts.body) : null;
    await sleep(60);   // 模拟网络往返，让按钮的 busy 状态可见
    for (const [m, re, fn] of routes) {
      const match = method === m && url.match(re);
      if (!match) continue;
      const result = fn(match, body);
      save();
      return clone(result);
    }
    fail(`接口不存在：${method} ${url}`);
  };

  // 刷新页面后，把中断的流程接着跑完
  for (const p of db.projects) {
    for (const a of p.assets) if (['GENERATING', 'AUTO_REVIEWING'].includes(a.status)) a.status = 'PENDING';
    drive(p);
  }
})();

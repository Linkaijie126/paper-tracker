# 文献追踪 Paper Tracker

每日自动从 arXiv 抓取指定关键词的最新论文，在手机上以 PWA 形式阅读。
**无需云服务器、完全免费、零运维**——基于 GitHub Actions + GitHub Pages。

当前追踪五个方向：离子插层、二维材料热导、材料制备（RGO 旋涂）、纳米力学（vdW 层间）、原子力显微镜与表征。

---

## 目录结构

```
paper-tracker/
├── .github/workflows/
│   └── daily-fetch.yml      # GitHub Actions 定时任务（每天 8:00）
├── config/
│   └── keywords.yml         # 关键词配置（改这里即可定制）
├── scripts/
│   └── fetch_papers.py      # arXiv 抓取脚本
├── docs/                    # GitHub Pages 静态站点
│   ├── index.html           # PWA 移动端页面（三 Tab 架构）
│   ├── manifest.json        # PWA 配置
│   ├── sw.js                # Service Worker（离线缓存）
│   ├── icon.svg             # 应用图标
│   └── data/
│       ├── papers.json      # 抓取结果（自动生成）
│       └── history.json     # 去重历史（自动生成，已 gitignore）
├── .gitignore
└── README.md
```

---

## 四步上手

### 第 1 步：本地试跑（可选，验证效果）

```bash
cd paper-tracker
pip install pyyaml
python scripts/fetch_papers.py
```

运行后会在 `docs/data/papers.json` 生成文献数据。
本地预览 PWA：

```bash
cd docs
python -m http.server 8000
# 浏览器访问 http://localhost:8000
```

### 第 2 步：推到 GitHub（一次性）

1. 在 GitHub 新建一个仓库（建议 public，免费账户 Pages 需要 public）
2. 推送代码：

```bash
cd paper-tracker
git init
git add .
git commit -m "init: paper tracker"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

3. 开启 GitHub Pages：
   - 仓库 → **Settings** → **Pages**
   - Source 选 **Deploy from a branch**
   - Branch 选 `main`，文件夹选 `/docs`
   - 保存，等待 1-2 分钟
   - 访问 `https://<用户名>.github.io/<仓库名>/`

4. 手动触发一次抓取验证：
   - 仓库 → **Actions** → **Daily Paper Fetch** → **Run workflow**

### 第 3 步：手机浏览器打开网址

在手机 Safari（iPhone）或 Chrome（Android）地址栏输入：

```
https://你的用户名.github.io/paper-tracker
```

### 第 4 步：添加到主屏幕 → 变桌面 App

| 平台 | 操作 |
|------|------|
| **iPhone (Safari)** | 底部分享按钮（方框+向上箭头）→「添加到主屏幕」→ 完成 |
| **Android (Chrome)** | 右上角菜单（⋮）→「添加到主屏幕」或「安装应用」 |

添加后桌面出现"文献追踪"图标，点开全屏运行，跟原生 App 一样。
每天 8:00 GitHub 自动抓取新文献，手机打开就是最新内容，无需任何手动操作。

---

## App 功能：三个 Tab

| Tab | 功能 |
|-----|------|
| **今日推送** | 当天抓取的新文献，顶部按方向分类筛选，3 天内文献标 NEW |
| **文献库** | 所有历史文献，每篇右上角⭐可加入星标 |
| **星标文献** | 手动添加 + 从文献库收藏的文献，可打标签（如"重要/待读/电池"），按标签筛选 |

星标数据存在手机浏览器 localStorage，完全私有。

---

## 自定义关键词

编辑 `config/keywords.yml`：

```yaml
categories:
  - name: 你的研究方向        # 显示在 App 筛选条，中英文都行
    keywords:
      - "english keyword"    # 必须英文，多个之间 OR 关系
      - "another keyword"
    arxiv_categories:        # 可选，限定 arXiv 学科分类（AND 关系）
      - cond-mat.mtrl-sci
```

arXiv 分类参考：https://arxiv.org/category_taxonomy
常用：`cond-mat.mtrl-sci`（材料）、`cond-mat.mes-hall`（介观）、`physics.chem-ph`（化学物理）、`physics.ins-det`（仪器）、`materials-science`（材料综合）

修改后 `git push`，下次定时任务自动生效。

---

## 工作原理

```
arXiv API
   ↓ (关键词 + 分类查询，每分类取最新 50 篇)
GitHub Actions (每天 8:00 cron 触发)
   ↓ 运行 fetch_papers.py
   ↓ 解析 XML、去重、增量更新
   ↓ 生成 docs/data/papers.json
   ↓ git commit & push
GitHub Pages (托管 docs/ 静态站点)
   ↓
手机 PWA (访问网页，添加到桌面)
```

- GitHub Actions 免费额度每月 2000 分钟（本任务约 30 分钟/月）
- GitHub Pages 免费托管静态站点
- 无需运维、无需域名、无需 HTTPS 证书

---

## 常见问题

**Q: GitHub Actions 没按时运行？**
A: cron 在高峰期可能延迟几分钟。可在 Actions 页面手动 Run workflow。

**Q: 抓取频率想更高/更低？**
A: 修改 `.github/workflows/daily-fetch.yml` 里的 cron。例如每 12 小时：`0 0,12 * * *`。

**Q: 关键词必须英文吗？**
A: 是。arXiv 是英文库，中文关键词搜不到。分类名 name 可以用中文。

**Q: 想换数据源（不用 arXiv）？**
A: 改 `scripts/fetch_papers.py`，加 Semantic Scholar / Crossref / PubMed 抓取函数。

**Q: 星标数据会丢吗？**
A: 存在手机浏览器 localStorage，换手机或清浏览器数据会丢。如需跨设备同步，可加导出/导入 JSON 功能。

**Q: 私有仓库能用吗？**
A: GitHub Pages 对免费账户需要仓库 public。如需私有，升级 Pro 或用 Vercel/Netlify 部署 `docs/` 目录。

---

## 许可

自用项目，可自由修改使用。

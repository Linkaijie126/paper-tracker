#!/usr/bin/env python3
"""
文献抓取脚本
- 双数据源：arXiv API（预印本）+ OpenAlex API（期刊论文）
- 按关键词与分类过滤
- 增量去重，保留历史
- 调用 Agnes AI 为每篇新文献生成中文一句话总结
- 输出 docs/data/papers.json 供前端读取

由 GitHub Actions 每天定时运行，也可本地手动执行。
"""

import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import json
import sys
import time
import os
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "keywords.yml"
OUTPUT_PATH = ROOT / "docs" / "data" / "papers.json"
HISTORY_PATH = ROOT / "docs" / "data" / "history.json"

ARXIV_API = "http://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_MAILTO = "papertracker@gmail.com"  # 进入 polite pool，限流更宽松
AGNES_API = "https://apihub.agnes-ai.com/v1/chat/completions"
AGNES_MODEL = "agnes-2.0-flash"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_date_range(days_back):
    """构造 arXiv API 的 submittedDate 范围查询字符串。
    arXiv 语法: submittedDate:[YYYYMMDDhhmm TO YYYYMMDDhhmm]
    """
    now = datetime.utcnow()
    start = now - timedelta(days=days_back)
    start_str = start.strftime("%Y%m%d") + "0000"
    end_str = now.strftime("%Y%m%d") + "2359"
    return f"submittedDate:[{start_str} TO {end_str}]"


def build_openalex_date_filter(days_back):
    """构造 OpenAlex 的 from_publication_date 过滤值。"""
    start = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    return start


# ============ arXiv 数据源 ============

def fetch_arxiv(query, max_results=50, retries=3):
    """调用 arXiv API，返回解析后的论文列表。
    arXiv 要求每 3 秒最多 1 次请求，否则返回 429。
    """
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = ARXIV_API + "?" + urllib.parse.urlencode(params)

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "PaperTracker/1.0 (self-use)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")
            time.sleep(3)
            return parse_arxiv_response(data)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = 5 * (2 ** attempt)
                print(f"  [arXiv限流] 429，{wait}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                print(f"  [arXiv网络错误] {e}，3s 后重试")
                time.sleep(3)
                continue
            raise
    raise last_err


def parse_arxiv_response(xml_text):
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_text)
    papers = []

    for entry in root.findall("atom:entry", ns):
        arxiv_url = entry.find("atom:id", ns).text
        arxiv_id = arxiv_url.split("/abs/")[-1]

        if "arxiv/api" in arxiv_id:
            continue

        title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
        summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
        summary = " ".join(summary.split())
        published = entry.find("atom:published", ns).text
        updated = entry.find("atom:updated", ns).text

        authors = []
        for author in entry.findall("atom:author", ns):
            name = author.find("atom:name", ns).text
            authors.append(name)

        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break

        doi_elem = entry.find("arxiv:doi", ns)
        doi = doi_elem.text if doi_elem is not None else ""

        primary_elem = entry.find("arxiv:primary_category", ns)
        primary_category = (
            primary_elem.get("term") if primary_elem is not None else ""
        )

        papers.append(
            {
                "id": arxiv_id,
                "title": title,
                "summary": summary,
                "authors": authors,
                "published": published,
                "updated": updated,
                "pdf_url": pdf_url,
                "arxiv_url": arxiv_url,
                "doi": doi,
                "primary_category": primary_category,
                "source": "arxiv",
            }
        )

    return papers


# ============ OpenAlex 数据源（覆盖期刊论文）============

def fetch_openalex(keywords, days_back, max_results=25, retries=3, api_key=""):
    """调用 OpenAlex API 搜索标题+摘要含关键词的近期论文。
    OpenAlex 覆盖 2.5 亿+作品，含 Science/Nature/期刊。
    有 api_key 时限流更宽松（10万/天），否则用 mailto 进 polite pool。
    """
    query_str = " ".join(keywords)
    from_date = build_openalex_date_filter(days_back)

    params = {
        "search": query_str,
        "filter": f"from_publication_date:{from_date},type:article",
        "per-page": max_results,
        "sort": "publication_date:desc",
        "select": "id,title,publication_date,doi,abstract_inverted_index,authorships,primary_location,url",
        "mailto": OPENALEX_MAILTO,
    }
    if api_key:
        params["api_key"] = api_key
    url = OPENALEX_API + "?" + urllib.parse.urlencode(params)

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "PaperTracker/1.0 (mailto:" + OPENALEX_MAILTO + ")"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            time.sleep(1)  # OpenAlex polite pool 限流：~10 req/s，保守起见 1s
            return parse_openalex_response(data)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = 10 * (2 ** attempt)
                print(f"  [OpenAlex限流] 429，{wait}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            print(f"  [OpenAlex HTTP {e.code}] {e.read().decode('utf-8', errors='ignore')[:200]}")
            return []
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                print(f"  [OpenAlex网络错误] {e}，3s 后重试")
                time.sleep(3)
                continue
            print(f"  [OpenAlex错误] {e}")
            return []
    print(f"  [OpenAlex] 全部重试失败: {last_err}")
    return []


def parse_openalex_response(data):
    """解析 OpenAlex 响应，返回统一格式的论文列表。"""
    papers = []
    for w in data.get("results", []):
        # OpenAlex id 形如 https://openalex.org/W123456789
        oax_id = w.get("id", "").split("/")[-1]
        if not oax_id:
            continue

        # 还原摘要（倒排索引 → 顺序文本）
        abstract = ""
        aidx = w.get("abstract_inverted_index") or {}
        if aidx:
            positions = []
            for word, pos_list in aidx.items():
                for pos in pos_list:
                    positions.append((pos, word))
            positions.sort()
            abstract = " ".join(w for _, w in positions)

        # 作者列表
        authors = []
        for a in w.get("authorships", [])[:10]:
            author = a.get("author", {})
            name = author.get("display_name", "")
            if name:
                authors.append(name)

        doi_raw = w.get("doi") or ""
        doi = doi_raw.replace("https://doi.org/", "") if doi_raw else ""

        pub_date = w.get("publication_date", "")
        # 转成 ISO datetime 格式，兼容前端 new Date()
        published = pub_date + "T00:00:00Z" if pub_date else ""

        # 主位置信息（期刊名）
        primary_loc = w.get("primary_location") or {}
        source_info = (primary_loc.get("source") or {}) if primary_loc else {}
        journal = source_info.get("display_name", "") if source_info else ""

        # URL：优先 doi 链接，否则 OpenAlex url
        url = w.get("url") or (f"https://doi.org/{doi}" if doi else "")

        papers.append({
            "id": "openalex_" + oax_id,
            "title": w.get("title", ""),
            "summary": abstract,
            "authors": authors,
            "published": published,
            "updated": published,
            "pdf_url": "",
            "arxiv_url": "",
            "doi": doi,
            "primary_category": journal,  # 借用此字段存期刊名
            "journal": journal,
            "url": url,
            "source": "openalex",
        })

    return papers


# ============ Agnes AI 总结 ============

def generate_ai_summary(title, summary, api_key):
    """调用 Agnes AI 生成中文一句话总结。
    失败时返回空字符串，不影响整体流程。
    """
    if not api_key:
        return ""

    # 摘要过长则截断，避免超出 token 限制
    trunc_summary = summary[:1500] if len(summary) > 1500 else summary

    prompt = (
        f"请用中文一句话（30字以内）总结这篇论文的核心贡献，直接给出总结，不要前缀：\n\n"
        f"标题：{title}\n\n摘要：{trunc_summary}"
    )

    payload = {
        "model": AGNES_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.3,
    }

    try:
        req = urllib.request.Request(
            AGNES_API,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return content
    except Exception as e:
        print(f"    [AI总结失败] {e}")
        return ""


def load_history():
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": [], "papers": []}


def save_history(history):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main():
    # 统一用 UTC 时间戳，写入 JSON 时带 Z 后缀，前端 new Date() 能正确转本地时区
    now_utc = datetime.now(timezone.utc)
    # 北京时间（UTC+8）用于 fetch_date，保证"今日"与用户认知一致
    beijing_tz = timezone(timedelta(hours=8))
    now_beijing = now_utc.astimezone(beijing_tz)
    # last_updated 带 UTC 标记，前端会自动转北京时间显示
    last_updated_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_utc.microsecond:06d}" + "Z"
    print(f"=== 文献抓取开始 {last_updated_str} (北京 {now_beijing.isoformat()}) ===")

    config = load_config()
    history = load_history()
    seen_ids = set(history["seen_ids"])

    # 历史文献中已有 AI 总结的，复用 id 集合，避免重复调用
    existing_summaries = {
        p["id"]: p.get("ai_summary", "")
        for p in history["papers"]
        if p.get("ai_summary")
    }

    new_papers = []
    today_str = now_beijing.strftime("%Y-%m-%d")  # 用北京日期作为 fetch_date
    max_per_query = config.get("fetch", {}).get("max_results_per_query", 50)
    keep_history = config.get("fetch", {}).get("keep_history", 500)
    days_back = config.get("fetch", {}).get("days_back", 14)

    # AI 总结配置：从环境变量读取 key
    agnes_key = os.environ.get("AGNES_API_KEY", "")
    if agnes_key:
        print(f"[AI总结] 已启用 Agnes AI 总结 (model={AGNES_MODEL})")
    else:
        print("[AI总结] 未设置 AGNES_API_KEY 环境变量，跳过 AI 总结")

    # OpenAlex API key（有 key 限流更宽松，无 key 用 polite pool）
    openalex_key = os.environ.get("OPENALEX_API_KEY", "")
    if openalex_key:
        print("[OpenAlex] 已配置 API key，限流 10万/天")
    else:
        print("[OpenAlex] 未配置 API key，使用 polite pool（限流较严）")

    date_range = build_date_range(days_back)
    print(f"[日期过滤] 仅抓取最近 {days_back} 天的文献")

    for category in config["categories"]:
        cat_name = category["name"]
        keywords = category["keywords"]
        arxiv_cats = category.get("arxiv_categories", [])

        print(f"\n--- 分类: {cat_name} ---")

        # ===== 数据源1: arXiv =====
        def build_phrase_query(phrase):
            words = phrase.split()
            if len(words) == 1:
                return f"all:{words[0]}"
            return "(" + " AND ".join(f"all:{w}" for w in words) + ")"

        kw_query = " OR ".join(build_phrase_query(kw) for kw in keywords)
        if arxiv_cats:
            cat_query = " OR ".join(f"cat:{c}" for c in arxiv_cats)
            query = f"({kw_query}) AND ({cat_query}) AND ({date_range})"
        else:
            query = f"({kw_query}) AND ({date_range})"

        try:
            arxiv_papers = fetch_arxiv(query, max_results=max_per_query)
            print(f"  [arXiv] 返回 {len(arxiv_papers)} 篇")
        except Exception as e:
            print(f"  [arXiv错误] {e}")
            arxiv_papers = []

        # ===== 数据源2: OpenAlex（期刊论文）=====
        # 用分类的所有关键词组合搜索
        try:
            openalex_papers = fetch_openalex(keywords, days_back, max_results=25, api_key=openalex_key)
            print(f"  [OpenAlex] 返回 {len(openalex_papers)} 篇")
        except Exception as e:
            print(f"  [OpenAlex错误] {e}")
            openalex_papers = []

        # ===== 合并去重 =====
        all_papers = arxiv_papers + openalex_papers
        # 用 DOI 二次去重（arXiv 和 OpenAlex 可能命中同一篇）
        seen_dois = set()
        deduped = []
        for p in all_papers:
            doi = (p.get("doi") or "").lower().strip()
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)
            deduped.append(p)
        if len(deduped) < len(all_papers):
            print(f"  [去重] {len(all_papers)} → {len(deduped)} 篇（DOI去重 {len(all_papers) - len(deduped)} 篇）")

        cat_new = 0
        for p in deduped:
            if p["id"] in seen_ids:
                continue
            p["category"] = cat_name
            p["fetch_date"] = today_str

            # AI 总结：优先复用已有的，否则调用 API
            if p["id"] in existing_summaries:
                p["ai_summary"] = existing_summaries[p["id"]]
            elif agnes_key:
                print(f"    [AI] 生成总结: {p['id']} ({p['source']})")
                p["ai_summary"] = generate_ai_summary(
                    p["title"], p["summary"], agnes_key
                )
                time.sleep(1)  # 限流，避免 API 过载
            else:
                p["ai_summary"] = ""

            new_papers.append(p)
            seen_ids.add(p["id"])
            cat_new += 1

        print(f"  新增 {cat_new} 篇")

    # 增量更新历史（新文献排前，保留最近 N 篇）
    history["seen_ids"] = list(seen_ids)
    history["papers"] = (new_papers + history["papers"])[:keep_history]
    save_history(history)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "last_updated": last_updated_str,
        "total_papers": len(history["papers"]),
        "new_today": len(new_papers),
        "categories": [c["name"] for c in config["categories"]],
        "papers": history["papers"],
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 数据源统计
    arxiv_count = sum(1 for p in new_papers if p.get("source") == "arxiv")
    openalex_count = sum(1 for p in new_papers if p.get("source") == "openalex")
    print(f"\n=== 完成 ===")
    print(f"今日新增: {len(new_papers)} 篇（arXiv {arxiv_count} + OpenAlex {openalex_count}）")
    print(f"总文献数: {len(history['papers'])}")
    print(f"输出文件: {OUTPUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[致命错误] {e}", file=sys.stderr)
        sys.exit(1)

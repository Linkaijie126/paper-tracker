#!/usr/bin/env python3
"""
文献抓取脚本
- 从 arXiv API 抓取最新论文
- 按关键词与分类过滤
- 增量去重，保留历史
- 输出 docs/data/papers.json 供前端读取

由 GitHub Actions 每天定时运行，也可本地手动执行。
"""

import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import sys
import time
import yaml
from pathlib import Path

# 路径配置（脚本位于 scripts/，项目根在上一级）
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "keywords.yml"
OUTPUT_PATH = ROOT / "docs" / "data" / "papers.json"
HISTORY_PATH = ROOT / "docs" / "data" / "history.json"

ARXIV_API = "http://export.arxiv.org/api/query"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_arxiv(query, max_results=50, retries=3):
    """调用 arXiv API，返回解析后的论文列表。
    arXiv 要求每 3 秒最多 1 次请求，否则返回 429。
    本函数内置 3 秒间隔 + 指数退避重试。
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
            # 成功后等 3 秒，避免下一个请求触发 429
            time.sleep(3)
            return parse_arxiv_response(data)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                # 限流，指数退避：5s, 10s, 20s
                wait = 5 * (2 ** attempt)
                print(f"  [限流] 429，{wait}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last_err = e
            # 网络错误也重试一次
            if attempt < retries - 1:
                print(f"  [网络错误] {e}，3s 后重试")
                time.sleep(3)
                continue
            raise
    raise last_err


def parse_arxiv_response(xml_text):
    """解析 arXiv Atom XML 响应"""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_text)
    papers = []

    for entry in root.findall("atom:entry", ns):
        arxiv_url = entry.find("atom:id", ns).text
        arxiv_id = arxiv_url.split("/abs/")[-1]

        # 跳过 arXiv 公告条目
        if "arxiv/api" in arxiv_id:
            continue

        title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
        summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
        summary = " ".join(summary.split())  # 折叠多余空白
        published = entry.find("atom:published", ns).text
        updated = entry.find("atom:updated", ns).text

        authors = []
        for author in entry.findall("atom:author", ns):
            name = author.find("atom:name", ns).text
            authors.append(name)

        # PDF 链接
        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break

        # DOI
        doi_elem = entry.find("arxiv:doi", ns)
        doi = doi_elem.text if doi_elem is not None else ""

        # 主分类
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
            }
        )

    return papers


def load_history():
    """加载历史抓取记录，用于增量去重"""
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": [], "papers": []}


def save_history(history):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main():
    now = datetime.now()
    print(f"=== 文献抓取开始 {now.isoformat()} ===")

    config = load_config()
    history = load_history()
    seen_ids = set(history["seen_ids"])

    new_papers = []
    today_str = now.strftime("%Y-%m-%d")
    max_per_query = config.get("fetch", {}).get("max_results_per_query", 50)
    keep_history = config.get("fetch", {}).get("keep_history", 500)

    for category in config["categories"]:
        cat_name = category["name"]
        keywords = category["keywords"]
        arxiv_cats = category.get("arxiv_categories", [])

        print(f"\n--- 分类: {cat_name} ---")

        # 构造查询：关键词 OR + 分类过滤
        kw_query = " OR ".join(f"all:{kw}" for kw in keywords)
        if arxiv_cats:
            cat_query = " OR ".join(f"cat:{c}" for c in arxiv_cats)
            query = f"({kw_query}) AND ({cat_query})"
        else:
            query = kw_query

        try:
            papers = fetch_arxiv(query, max_results=max_per_query)
            print(f"  arXiv 返回 {len(papers)} 篇")
        except Exception as e:
            print(f"  [错误] arXiv API 调用失败: {e}")
            continue

        cat_new = 0
        for p in papers:
            if p["id"] in seen_ids:
                continue
            # 标记分类与抓取日期
            p["category"] = cat_name
            p["fetch_date"] = today_str
            p["source"] = "arxiv"
            new_papers.append(p)
            seen_ids.add(p["id"])
            cat_new += 1

        print(f"  新增 {cat_new} 篇")

    # 增量更新历史（新文献排前，保留最近 N 篇）
    history["seen_ids"] = list(seen_ids)
    history["papers"] = (new_papers + history["papers"])[:keep_history]
    save_history(history)

    # 输出 papers.json 供前端读取
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "last_updated": now.isoformat(),
        "total_papers": len(history["papers"]),
        "new_today": len(new_papers),
        "categories": [c["name"] for c in config["categories"]],
        "papers": history["papers"],
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完成 ===")
    print(f"今日新增: {len(new_papers)}")
    print(f"总文献数: {len(history['papers'])}")
    print(f"输出文件: {OUTPUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[致命错误] {e}", file=sys.stderr)
        sys.exit(1)

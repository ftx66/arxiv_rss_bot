import os
import json
import requests
from config_loader import load_config
from email_subscription import get_latest_rss_file, parse_rss_file
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any
import argparse
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISH_HISTORY_FILE = os.path.join(SCRIPT_DIR, "notion_publish_history.json")

def load_publish_history():
    if not os.path.exists(PUBLISH_HISTORY_FILE):
        return {"published_guids": []}
    try:
        with open(PUBLISH_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"published_guids": []}

def save_publish_history(history):
    try:
        with open(PUBLISH_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        return True
    except:
        return False

def get_database_properties(token, database_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return {}
    data = resp.json()
    return data.get("properties", {})

def ensure_database_properties(token, database_id, properties_map):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    need = {}
    if "URL" not in properties_map or properties_map["URL"].get("type") != "url":
        need["URL"] = {"url": {}}
    if "Authors" not in properties_map or properties_map["Authors"].get("type") != "multi_select":
        need["Authors"] = {"multi_select": {}}
    if "Date" not in properties_map or properties_map["Date"].get("type") != "date":
        need["Date"] = {"date": {}}
    if "Keywords" not in properties_map or properties_map["Keywords"].get("type") != "multi_select":
        need["Keywords"] = {"multi_select": {}}
    if "Abstract" not in properties_map or properties_map["Abstract"].get("type") != "rich_text":
        need["Abstract"] = {"rich_text": {}}
    if "ArXiv ID" not in properties_map or properties_map["ArXiv ID"].get("type") != "rich_text":
        need["ArXiv ID"] = {"rich_text": {}}
    if not need:
        return properties_map
    body = {"properties": need}
    url = f"https://api.notion.com/v1/databases/{database_id}"
    resp = requests.patch(url, headers=headers, json=body, timeout=60)
    if resp.status_code == 200:
        return get_database_properties(token, database_id)
    return properties_map

def find_title_property(properties):
    for key, prop in properties.items():
        if prop.get("type") == "title":
            return key
    return "Name"

def create_page(token, database_id, properties, children=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    if children:
        body["children"] = children
    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=body, timeout=60)
    return resp

def update_page(token, page_id, properties):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    body = {"properties": properties}
    resp = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=headers, json=body, timeout=60)
    return resp

def query_database_pages(token, database_id) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    pages = []
    payload = {"page_size": 100}
    while True:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            break
        data = resp.json()
        results = data.get("results", [])
        pages.extend(results)
        next_cursor = data.get("next_cursor")
        has_more = data.get("has_more")
        if has_more and next_cursor:
            payload["start_cursor"] = next_cursor
        else:
            break
    return pages

def get_page_title(page) -> str:
    props = page.get("properties", {})
    for key, prop in props.items():
        if prop.get("type") == "title":
            arr = prop.get("title", [])
            return "".join([t.get("plain_text", "") for t in arr])
    return ""

def build_properties_for_paper(config, properties_map, paper, title_prop):
    link = paper.get("link", "")
    desc = paper.get("description", "")
    authors, abstract_text = extract_authors_and_abstract(desc)
    pub_date_str = paper.get("pubDate", "")
    pub_dt = None
    try:
        if pub_date_str:
            pub_dt = parsedate_to_datetime(pub_date_str)
    except:
        pub_dt = None
    matched = match_keywords(paper.get("title", ""), desc, config.get("keywords", []))
    has_url_prop = "URL" in properties_map and properties_map["URL"].get("type") == "url"
    has_authors_prop = "Authors" in properties_map and properties_map["Authors"].get("type") == "multi_select"
    has_date_prop = "Date" in properties_map and properties_map["Date"].get("type") == "date"
    has_keywords_prop = "Keywords" in properties_map and properties_map["Keywords"].get("type") == "multi_select"
    has_abstract_prop = "Abstract" in properties_map and properties_map["Abstract"].get("type") == "rich_text"
    has_arxiv_prop = "ArXiv ID" in properties_map and properties_map["ArXiv ID"].get("type") == "rich_text"
    props = {
        title_prop: {"title": [{"text": {"content": paper.get("title", "Untitled")}}]}
    }
    if has_url_prop and link:
        props["URL"] = {"url": link}
    if has_authors_prop and authors:
        props["Authors"] = {"multi_select": [{"name": a} for a in authors[:20]]}
    if has_date_prop and pub_dt:
        props["Date"] = {"date": {"start": pub_dt.date().isoformat()}}
    if has_keywords_prop and matched:
        props["Keywords"] = {"multi_select": [{"name": k} for k in matched[:20]]}
    if has_abstract_prop and abstract_text:
        props["Abstract"] = {"rich_text": [{"type": "text", "text": {"content": abstract_text[:1900]}}]}
    arxiv_id = None
    if link and "arxiv.org" in link:
        try:
            arxiv_id = link.rstrip("/").split("/")[-1]
        except:
            arxiv_id = None
    if has_arxiv_prop and arxiv_id:
        props["ArXiv ID"] = {"rich_text": [{"type": "text", "text": {"content": arxiv_id}}]}
    # 动态字段映射（基于XML项字段）
    if "link" in properties_map and properties_map["link"].get("type") == "url" and link:
        props["link"] = {"url": link}
    if "pubDate" in properties_map and properties_map["pubDate"].get("type") == "date" and pub_dt:
        props["pubDate"] = {"date": {"start": pub_dt.date().isoformat()}}
    if "description" in properties_map and properties_map["description"].get("type") == "rich_text" and desc:
        props["description"] = {"rich_text": [{"type": "text", "text": {"content": desc[:1900]}}]}
    guid = paper.get("guid") or paper.get("link")
    if "guid" in properties_map and properties_map["guid"].get("type") == "rich_text" and guid:
        props["guid"] = {"rich_text": [{"type": "text", "text": {"content": guid}}]}
    if "category" in properties_map and properties_map["category"].get("type") == "multi_select":
        cats = extract_categories_from_description(desc)
        if cats:
            props["category"] = {"multi_select": [{"name": c} for c in cats[:20]]}
    return props

def extract_authors_and_abstract(description):
    if not description:
        return [], ""
    lines = [l.strip() for l in description.split("\n") if l.strip()]
    authors = []
    abstract_lines = []
    found = False
    for line in lines:
        if line.lower().startswith("authors:"):
            names = line.split(":", 1)[1].strip()
            authors = [n.strip() for n in names.split(",") if n.strip()]
            found = True
            continue
        if found:
            abstract_lines.append(line)
    if not abstract_lines:
        abstract_text = " ".join(lines)
    else:
        abstract_text = " ".join(abstract_lines)
    return authors, abstract_text

def match_keywords(title, description, keywords):
    text = f"{title} {description}".lower()
    return [kw for kw in (keywords or []) if kw.lower() in text]

def strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag

def parse_first_item_fields(xml_path: str) -> List[str]:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        channel = root.find("channel")
        if channel is None:
            return []
        item = channel.find("item")
        if item is None:
            return []
        fields = []
        for child in list(item):
            name = strip_ns(child.tag)
            fields.append(name)
        return fields
    except Exception:
        return []

def guess_notion_type(field_name: str) -> str:
    name = field_name.lower()
    if name in {"link"}:
        return "url"
    if name in {"pubdate", "updated"}:
        return "date"
    if name in {"category", "keywords", "tags"}:
        return "multi_select"
    if name in {"title"}:
        return "title"
    return "rich_text"

def ensure_properties_from_xml(token, database_id, properties_map, xml_path):
    fields = parse_first_item_fields(xml_path)
    need = {}
    for f in fields:
        t = guess_notion_type(f)
        if t == "title":
            continue
        exists = f in properties_map and properties_map[f].get("type") == t
        if not exists:
            need[f] = {t: {}}
    if not need:
        return properties_map
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}"
    resp = requests.patch(url, headers=headers, json={"properties": need}, timeout=60)
    if resp.status_code == 200:
        return get_database_properties(token, database_id)
    return properties_map

def extract_categories_from_description(description: str) -> List[str]:
    if not description:
        return []
    for line in description.split("\n"):
        l = line.strip()
        if l.lower().startswith("categories:"):
            cats = l.split(":", 1)[1].strip()
            return [c.strip() for c in cats.split(",") if c.strip()]
    return []

def check_notion_connection():
    config = load_config()
    notion_cfg = config.get("ai_analysis", {}).get("notion", {})
    if not notion_cfg.get("enabled", False):
        return {"success": False, "error": "Notion未启用"}
    token = notion_cfg.get("integration_token", "")
    database_id = notion_cfg.get("database_id", "")
    if not token or not database_id:
        return {"success": False, "error": "缺少Notion集成令牌或数据库ID"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "database": {
                    "id": data.get("id"),
                    "title": "".join([t.get("plain_text", "") for t in data.get("title", [])]),
                },
            }
        else:
            return {"success": False, "status_code": resp.status_code, "error": resp.text[:300]}
    except Exception as e:
        return {"success": False, "error": str(e)}

def setup_notion_database():
    config = load_config()
    notion_cfg = config.get("ai_analysis", {}).get("notion", {})
    if not notion_cfg.get("enabled", False):
        return {"success": False, "error": "Notion未启用"}
    token = notion_cfg.get("integration_token", "")
    database_id = notion_cfg.get("database_id", "")
    if not token or not database_id:
        return {"success": False, "error": "缺少Notion集成令牌或数据库ID"}
    properties_map = get_database_properties(token, database_id)
    latest_rss = get_latest_rss_file()
    if latest_rss:
        properties_map = ensure_properties_from_xml(token, database_id, properties_map, latest_rss)
    else:
        properties_map = ensure_database_properties(token, database_id, properties_map)
    title_prop = find_title_property(properties_map)
    keys = sorted(list(properties_map.keys()))
    return {"success": True, "title_property": title_prop, "properties": keys}

def publish_from_latest_rss(limit=30):
    config = load_config()
    notion_cfg = config.get("ai_analysis", {}).get("notion", {})
    if not notion_cfg.get("enabled", False):
        return {"success": False, "error": "Notion未启用"}
    token = notion_cfg.get("integration_token", "")
    database_id = notion_cfg.get("database_id", "")
    if not token or not database_id:
        return {"success": False, "error": "缺少Notion集成令牌或数据库ID"}
    latest_rss = get_latest_rss_file()
    if not latest_rss:
        return {"success": False, "error": "没有RSS文件"}
    papers = parse_rss_file(latest_rss)
    if not papers:
        return {"success": False, "error": "RSS无论文"}
    properties_map = get_database_properties(token, database_id)
    properties_map = ensure_database_properties(token, database_id, properties_map)
    title_prop = find_title_property(properties_map)
    has_url_prop = "URL" in properties_map and properties_map["URL"].get("type") == "url"
    has_authors_prop = "Authors" in properties_map and properties_map["Authors"].get("type") == "multi_select"
    has_date_prop = "Date" in properties_map and properties_map["Date"].get("type") == "date"
    has_keywords_prop = "Keywords" in properties_map and properties_map["Keywords"].get("type") == "multi_select"
    has_abstract_prop = "Abstract" in properties_map and properties_map["Abstract"].get("type") == "rich_text"
    has_arxiv_prop = "ArXiv ID" in properties_map and properties_map["ArXiv ID"].get("type") == "rich_text"
    history = load_publish_history()
    published_guids = set(history.get("published_guids", []))
    created = 0
    errors = []
    for paper in papers:
        if created >= limit:
            break
        guid = paper.get("guid") or paper.get("link")
        if guid in published_guids:
            continue
        title = paper.get("title", "Untitled")
        link = paper.get("link", "")
        desc = paper.get("description", "")
        authors, abstract_text = extract_authors_and_abstract(desc)
        pub_date_str = paper.get("pubDate", "")
        pub_dt = None
        try:
            if pub_date_str:
                pub_dt = parsedate_to_datetime(pub_date_str)
        except:
            pub_dt = None
        matched = match_keywords(title, desc, config.get("keywords", []))
        properties = {
            title_prop: {
                "title": [{"text": {"content": title}}]
            }
        }
        if has_url_prop and link:
            properties["URL"] = {"url": link}
        if has_authors_prop and authors:
            properties["Authors"] = {"multi_select": [{"name": a} for a in authors[:20]]}
        if has_date_prop and pub_dt:
            properties["Date"] = {"date": {"start": pub_dt.date().isoformat()}}
        if has_keywords_prop and matched:
            properties["Keywords"] = {"multi_select": [{"name": k} for k in matched[:20]]}
        if has_abstract_prop and abstract_text:
            properties["Abstract"] = {"rich_text": [{"type": "text", "text": {"content": abstract_text[:1900]}}]}
        arxiv_id = None
        if link and "arxiv.org" in link:
            try:
                arxiv_id = link.rstrip("/").split("/")[-1]
            except:
                arxiv_id = None
        if has_arxiv_prop and arxiv_id:
            properties["ArXiv ID"] = {"rich_text": [{"type": "text", "text": {"content": arxiv_id}}]}
        # 动态字段映射（基于XML项字段）
        if "link" in properties_map and properties_map["link"].get("type") == "url" and link:
            properties["link"] = {"url": link}
        if "pubDate" in properties_map and properties_map["pubDate"].get("type") == "date" and pub_dt:
            properties["pubDate"] = {"date": {"start": pub_dt.date().isoformat()}}
        if "description" in properties_map and properties_map["description"].get("type") == "rich_text" and desc:
            properties["description"] = {"rich_text": [{"type": "text", "text": {"content": desc[:1900]}}]}
        if "guid" in properties_map and properties_map["guid"].get("type") == "rich_text" and guid:
            properties["guid"] = {"rich_text": [{"type": "text", "text": {"content": guid}}]}
        if "category" in properties_map and properties_map["category"].get("type") == "multi_select":
            cats = extract_categories_from_description(desc)
            if cats:
                properties["category"] = {"multi_select": [{"name": c} for c in cats[:20]]}
        children = []
        if desc:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": desc[:1900]}}]
                }
            })
        resp = create_page(token, database_id, properties, children)
        if resp.status_code == 200:
            created += 1
            published_guids.add(guid)
        else:
            errors.append({"title": title, "status": resp.status_code, "error": resp.text[:300]})
    history["published_guids"] = list(published_guids)
    save_publish_history(history)
    return {"success": created > 0, "created": created, "errors": errors}

def backfill_from_latest_rss():
    config = load_config()
    notion_cfg = config.get("ai_analysis", {}).get("notion", {})
    if not notion_cfg.get("enabled", False):
        return {"success": False, "error": "Notion未启用"}
    token = notion_cfg.get("integration_token", "")
    database_id = notion_cfg.get("database_id", "")
    if not token or not database_id:
        return {"success": False, "error": "缺少Notion集成令牌或数据库ID"}
    latest_rss = get_latest_rss_file()
    if not latest_rss:
        return {"success": False, "error": "没有RSS文件"}
    papers = parse_rss_file(latest_rss)
    if not papers:
        return {"success": False, "error": "RSS无论文"}
    properties_map = get_database_properties(token, database_id)
    properties_map = ensure_database_properties(token, database_id, properties_map)
    title_prop = find_title_property(properties_map)
    pages = query_database_pages(token, database_id)
    page_by_title = {get_page_title(p): p for p in pages}
    updated = 0
    created = 0
    errors = []
    history = load_publish_history()
    published_guids = set(history.get("published_guids", []))
    for paper in papers:
        title = paper.get("title", "")
        props = build_properties_for_paper(config, properties_map, paper, title_prop)
        if title in page_by_title and page_by_title[title]:
            page_id = page_by_title[title]["id"]
            resp = update_page(token, page_id, props)
            if resp.status_code == 200:
                updated += 1
            else:
                errors.append({"title": title, "status": resp.status_code, "error": resp.text[:300]})
        else:
            resp = create_page(token, database_id, props, None)
            if resp.status_code == 200:
                created += 1
                guid = paper.get("guid") or paper.get("link")
                if guid:
                    published_guids.add(guid)
            else:
                errors.append({"title": title, "status": resp.status_code, "error": resp.text[:300]})
    history["published_guids"] = list(published_guids)
    save_publish_history(history)
    return {"success": (updated + created) > 0, "updated": updated, "created": created, "errors": errors}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    outputs = {}
    if args.check:
        outputs["check"] = check_notion_connection()
    if args.setup:
        outputs["setup"] = setup_notion_database()
    if args.publish:
        outputs["publish"] = publish_from_latest_rss(limit=args.limit)
    if args.backfill:
        outputs["backfill"] = backfill_from_latest_rss()
    print(outputs)

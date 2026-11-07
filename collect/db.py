"""
PostgreSQL 数据库操作
"""
import json
import threading
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PG_DSN


_local = threading.local()


def get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None or _local.conn.closed:
        _local.conn = psycopg2.connect(PG_DSN)
        _local.conn.autocommit = True
    return _local.conn


# ============ 模型 ============

def get_all_models():
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, full_name, abbrev, search_expr FROM model ORDER BY id")
        return cur.fetchall()


def get_model_by_id(model_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, full_name, abbrev, search_expr FROM model WHERE id = %s", (model_id,))
        return cur.fetchone()


# ============ 论文 ============

def deduplicate_paper(paper_data):
    """三级去重：EID → OID → 标题+期刊+页码，返回已有 id 或 None"""
    conn = get_conn()
    with conn.cursor() as cur:
        eid = paper_data.get("eid")
        if eid:
            cur.execute("SELECT id FROM paper WHERE eid = %s", (eid,))
            row = cur.fetchone()
            if row:
                return row[0]

        oid = paper_data.get("oid")
        if oid:
            cur.execute("SELECT id FROM paper WHERE oid = %s", (oid,))
            row = cur.fetchone()
            if row:
                return row[0]

        title = paper_data.get("title")
        pub_name = paper_data.get("publicationName")
        pages = paper_data.get("pageRange") or paper_data.get("pages")
        if title and pub_name and pages:
            cur.execute(
                "SELECT id FROM paper WHERE title = %s AND publication_name = %s AND page_range = %s",
                (title, pub_name, pages),
            )
            row = cur.fetchone()
            if row:
                return row[0]

    return None


def insert_paper(paper_data):
    """插入论文，返回 paper_id"""
    conn = get_conn()
    with conn.cursor() as cur:
        published = None
        published_str = paper_data.get("publishedStr", "")
        if published_str:
            try:
                published = datetime.strptime(published_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        eid = paper_data.get("eid")
        params = (
            eid, paper_data.get("oid"), paper_data.get("doi"),
            paper_data.get("title"), paper_data.get("publicationName"),
            paper_data.get("pageRange"), paper_data.get("issueIdentifier"),
            paper_data.get("volume"), int(paper_data.get("citedby-count", 0) or 0),
            published, published_str,
            paper_data.get("abstract"), paper_data.get("authorKeyword"),
            json.dumps(paper_data.get("researchArea", [])),
            json.dumps(paper_data.get("author", [])),
            json.dumps(paper_data.get("agency", [])),
            paper_data.get("source", "scopus"),
        )

        if eid:
            cur.execute("""
                INSERT INTO paper (eid, oid, doi, title, publication_name, page_range,
                                   issue_identifier, volume, cited_by_count, published,
                                   published_str, abstract, author_keywords,
                                   subject_areas, authors, agencies, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (eid) WHERE eid IS NOT NULL AND eid != '' DO NOTHING
                RETURNING id
            """, params)
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute("SELECT id FROM paper WHERE eid = %s", (eid,))
            return cur.fetchone()[0]
        else:
            cur.execute("""
                INSERT INTO paper (eid, oid, doi, title, publication_name, page_range,
                                   issue_identifier, volume, cited_by_count, published,
                                   published_str, abstract, author_keywords,
                                   subject_areas, authors, agencies, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, params)
            return cur.fetchone()[0]


def add_paper_to_model(model_id, paper_id):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_paper (model_id, paper_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (model_id, paper_id),
        )


# ============ 论文引用 ============

def deduplicate_cite(cite_data):
    """施引论文去重（按 EID），返回已有 id 或 None"""
    eid = cite_data.get("eid")
    if not eid:
        return None
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM paper_cite WHERE eid = %s", (eid,))
        row = cur.fetchone()
        return row[0] if row else None


def insert_cites_batch(cite_list):
    """批量插入施引论文，返回 id 列表"""
    if not cite_list:
        return []
    conn = get_conn()
    ids = []
    with conn.cursor() as cur:
        for cite in cite_list:
            eid = cite.get("eid")
            if not eid:
                continue
            cur.execute("""
                INSERT INTO paper_cite (eid, title, doi, publication_name, cover_date, cited_by_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (eid) DO NOTHING
                RETURNING id
            """, (
                eid, cite.get("title"), cite.get("doi"),
                cite.get("publicationName"), cite.get("coverDate"),
                int(cite.get("citedby-count", 0) or 0),
            ))
            row = cur.fetchone()
            if row:
                ids.append(row[0])
            else:
                cur.execute("SELECT id FROM paper_cite WHERE eid = %s", (eid,))
                row = cur.fetchone()
                if row:
                    ids.append(row[0])
    return ids


def update_paper_citings(paper_id, citing_ids, cited_by_count):
    """更新论文的引用关系和计数"""
    conn = get_conn()
    old_autocommit = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for cite_id in citing_ids:
                cur.execute(
                    "INSERT INTO paper_citing (paper_id, cite_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (paper_id, cite_id),
                )
            cur.execute(
                "UPDATE paper SET cited_by_count = %s, citations_collected = TRUE WHERE id = %s",
                (cited_by_count, paper_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = old_autocommit


def get_papers_for_model(model_id):
    """获取模型关联的相关论文（relevance=1）用于引用采集"""
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT p.id, p.eid, p.citations_collected
            FROM paper p
            JOIN model_paper mp ON p.id = mp.paper_id
            WHERE mp.model_id = %s AND mp.relevance = 1
            ORDER BY p.id
        """, (model_id,))
        return cur.fetchall()


# ============ 专利 ============

def deduplicate_patent(pub_num):
    """专利去重（按 pubNum），返回已有 id 或 None"""
    if not pub_num:
        return None
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM patent WHERE pub_num = %s", (pub_num,))
        row = cur.fetchone()
        return row[0] if row else None


def insert_patent(patent_data):
    """插入专利，返回 patent_id"""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO patent (pub_num, title, country_code, country_name,
                                application_number, ori_assignee, inventors,
                                prior_art_keywords, priority_date, filing_date,
                                publication_date, grant_date, abstract, backward_refs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            patent_data.get("pubNum"), patent_data.get("title"),
            patent_data.get("authCountryCode"), patent_data.get("authCountryName"),
            patent_data.get("applicationNumber"), patent_data.get("oriAssignee"),
            patent_data.get("inventors"), patent_data.get("priorArtKeywords"),
            patent_data.get("priorityDate"), patent_data.get("filingDate"),
            patent_data.get("publicationDate"), patent_data.get("grantDate"),
            patent_data.get("abstract", ""),
            json.dumps(patent_data.get("referenceList", [])),
        ))
        return cur.fetchone()[0]


def insert_patent_cite(cite_data):
    """插入专利前向引用，返回 cite_id"""
    conn = get_conn()
    with conn.cursor() as cur:
        pub_num = cite_data.get("pubNum")
        if not pub_num:
            return None
        cur.execute("""
            INSERT INTO patent_cite (pub_num, title, ori_assignee, language, prior_date, pub_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (pub_num) DO NOTHING
            RETURNING id
        """, (
            pub_num, cite_data.get("title"),
            cite_data.get("oriAssignee"), cite_data.get("language"),
            cite_data.get("priorDate"), cite_data.get("pubDate"),
        ))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT id FROM patent_cite WHERE pub_num = %s", (pub_num,))
        row = cur.fetchone()
        return row[0] if row else None


def add_patent_citing(patent_id, cite_id, ref_type):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO patent_citing (patent_id, cite_id, ref_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (patent_id, cite_id, ref_type),
        )


def add_patent_to_model(model_id, patent_id):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_patent (model_id, patent_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (model_id, patent_id),
        )


# ============ 科研项目 ============

def deduplicate_project(project_name, fund_name):
    """项目去重（按项目名+基金名），返回已有 id 或 None"""
    if not project_name:
        return None
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM project WHERE project_name = %s AND fund_name = %s",
            (project_name, fund_name),
        )
        row = cur.fetchone()
        return row[0] if row else None


def insert_project(project_data):
    """插入科研项目，返回 project_id"""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO project (fund_name, type, country, project_name, keyword, abstract,
                                 project_name_en, keyword_en, abstract_en, chair, org,
                                 create_year, start_date, end_date, money, unit,
                                 subject, subject_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            project_data.get("fundName"), project_data.get("type"),
            project_data.get("country"), project_data.get("projectName"),
            project_data.get("keyword"), project_data.get("abstract"),
            project_data.get("projectName_en"), project_data.get("keyword_en"),
            project_data.get("abstract_en"), project_data.get("chair"),
            project_data.get("org"), project_data.get("createYear"),
            project_data.get("start"), project_data.get("end"),
            project_data.get("money"), project_data.get("unit"),
            project_data.get("subject"), project_data.get("subjectCode"),
        ))
        return cur.fetchone()[0]


def add_project_to_model(model_id, project_id):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_project (model_id, project_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (model_id, project_id),
        )

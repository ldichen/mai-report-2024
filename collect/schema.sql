-- MAI 数据采集 PostgreSQL Schema

-- 模型
CREATE TABLE IF NOT EXISTS model (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL DEFAULT '',
    abbrev TEXT NOT NULL DEFAULT '',
    status INTEGER DEFAULT 0,            -- 1=纳入统计，0=停用
    description_zh TEXT NOT NULL DEFAULT '',
    description_en TEXT NOT NULL DEFAULT '',
    license TEXT,
    repo_url TEXT,
    type INTEGER DEFAULT 1,              -- 1=模型，2=方法
    -- 自定义 Scopus 检索式，非空时**替代** full_name / abbrev 两条自动生成的检索式。
    -- 只写 TITLE-ABS-KEY() 括号内的表达式，不要写 PUBYEAR——年份子句由采集脚本拼接。
    -- 用于按字段拼不出正确检索式的条目，例如 SPECFEM 需要通配符 SPECFEM* 才能
    -- 匹配到 SPECFEM3D / SPECFEM2D，而通配符不能写进 abbrev（那是榜单显示名）。
    -- 注意：内容原样拼进检索式，没有语法校验，写错了 Scopus 会报错或返回垃圾。
    search_expr TEXT NOT NULL DEFAULT '',
    -- aliases：版本别名与旧 abbrev，'|' 分隔，关键词过滤按此匹配文本（裸 abbrev 的
    --           边界匹配不到 GFDL-ESM2M 这类后缀）；level：coupled / component /
    --          standalone；predecessor：前身系列的 abbrev（如 CESM 的前身是 CCSM）。
    aliases TEXT,
    level TEXT,
    predecessor TEXT
);

-- 论文
CREATE TABLE IF NOT EXISTS paper (
    id SERIAL PRIMARY KEY,
    eid TEXT,
    oid TEXT,
    doi TEXT,
    title TEXT,
    publication_name TEXT,
    page_range TEXT,
    issue_identifier TEXT,
    volume TEXT,
    cited_by_count INTEGER DEFAULT 0,
    published DATE,
    published_str TEXT,
    abstract TEXT,
    author_keywords TEXT[],
    subject_areas JSONB DEFAULT '[]',
    authors JSONB DEFAULT '[]',
    agencies JSONB DEFAULT '[]',
    source TEXT DEFAULT 'scopus',
    citations_collected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_eid ON paper(eid) WHERE eid IS NOT NULL AND eid != '';
CREATE INDEX IF NOT EXISTS idx_paper_oid ON paper(oid) WHERE oid IS NOT NULL AND oid != '';
CREATE INDEX IF NOT EXISTS idx_paper_title_pub_page ON paper(title, publication_name, page_range);

-- 施引论文（被引论文的引用者）
CREATE TABLE IF NOT EXISTS paper_cite (
    id SERIAL PRIMARY KEY,
    eid TEXT UNIQUE NOT NULL,
    title TEXT,
    doi TEXT,
    publication_name TEXT,
    cover_date TEXT,
    cited_by_count INTEGER DEFAULT 0
);

-- 模型-论文 多对多
CREATE TABLE IF NOT EXISTS model_paper (
    model_id INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    paper_id INTEGER NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    -- 1=确认相关（MAI 只统计这一档），0=新采集未审，-1=清洗剔除
    -- 采集入库时不指定，走默认值 0，由清洗流程提升为 1
    relevance INTEGER DEFAULT 0,
    PRIMARY KEY (model_id, paper_id)
);

-- 论文-施引 多对多
CREATE TABLE IF NOT EXISTS paper_citing (
    paper_id INTEGER NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    cite_id INTEGER NOT NULL REFERENCES paper_cite(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, cite_id)
);

-- 专利
CREATE TABLE IF NOT EXISTS patent (
    id SERIAL PRIMARY KEY,
    pub_num TEXT UNIQUE NOT NULL,
    title TEXT,
    country_code TEXT,
    country_name TEXT,
    application_number TEXT,
    ori_assignee TEXT,
    inventors TEXT[],
    prior_art_keywords TEXT[],
    priority_date TEXT,
    filing_date TEXT,
    publication_date TEXT,
    grant_date TEXT,
    abstract TEXT DEFAULT '',
    backward_refs JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW()
);

-- 专利前向引用（施引专利）
CREATE TABLE IF NOT EXISTS patent_cite (
    id SERIAL PRIMARY KEY,
    pub_num TEXT UNIQUE NOT NULL,
    title TEXT,
    ori_assignee TEXT,
    language TEXT,
    prior_date TIMESTAMP,
    pub_date TIMESTAMP
);

-- 专利-施引 多对多
CREATE TABLE IF NOT EXISTS patent_citing (
    patent_id INTEGER NOT NULL REFERENCES patent(id) ON DELETE CASCADE,
    cite_id INTEGER NOT NULL REFERENCES patent_cite(id) ON DELETE CASCADE,
    ref_type TEXT,
    PRIMARY KEY (patent_id, cite_id)
);

-- 模型-专利 多对多
CREATE TABLE IF NOT EXISTS model_patent (
    model_id INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    patent_id INTEGER NOT NULL REFERENCES patent(id) ON DELETE CASCADE,
    PRIMARY KEY (model_id, patent_id)
);

-- 科研项目
CREATE TABLE IF NOT EXISTS project (
    id SERIAL PRIMARY KEY,
    fund_name TEXT,
    type TEXT,
    country TEXT,
    project_name TEXT,
    keyword TEXT,
    abstract TEXT,
    project_name_en TEXT,
    keyword_en TEXT,
    abstract_en TEXT,
    chair TEXT,
    org TEXT,
    create_year TEXT,
    start_date TEXT,
    end_date TEXT,
    money TEXT,
    unit TEXT,
    subject TEXT,
    subject_code TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_name_fund
    ON project(project_name, fund_name)
    WHERE project_name IS NOT NULL AND project_name != '';

-- 模型-项目 多对多
CREATE TABLE IF NOT EXISTS model_project (
    model_id INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    PRIMARY KEY (model_id, project_id)
);
